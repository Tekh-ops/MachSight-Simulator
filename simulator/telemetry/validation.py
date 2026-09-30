"""
simulator/telemetry/validation.py
==================================
Canonical Telemetry Validation Layer.

Enforces physical sanity bounds, schema compliance, sequence integrity,
and strict ground-truth isolation.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

from simulator.telemetry.schema import TelemetryMessage

logger = logging.getLogger(__name__)


# ── Sanity Boundaries ──────────────────────────────────────────────────────────

MIN_REASONABLE_TIMESTAMP = 1_000_000_000.0  # ~2001-09-09
MAX_REASONABLE_TIMESTAMP = 3_000_000_000.0  # ~2065-01-24

DISTANCE_MIN_CM = 0.0
DISTANCE_MAX_CM = 1_000.0   # 10 metres (HC-SR04 physical envelope ~4-5m max)

CURRENT_MIN_A = 0.0
CURRENT_MAX_A = 50.0        # Small RC car motor stall max ~20-30A

RPM_MIN = 0.0
RPM_MAX = 5_000.0           # RC wheel speed upper bound

PWM_MIN = 0
PWM_MAX = 255

ALLOWED_SENSOR_STATUSES: Set[str] = {
    "ok",
    "good",
    "stale",
    "dropped",
    "degraded",
    "invalid",
    "noisy",
    "disconnected",
}

FORBIDDEN_GROUND_TRUTH_KEYS: Set[str] = {
    "ground_truth",
    "fault",
    "faults",
    "active_faults",
    "fault_id",
    "mechanical_load",
    "wheel_slip",
    "true_position_m",
    "true_velocity_mps",
    "wheel_rpm_true",
    "motor_current_true",
    "obstacle_distance_m",
}


# ── Validation Report ─────────────────────────────────────────────────────────

class TelemetryValidationError(ValueError):
    """Raised when telemetry fails contract validation."""


@dataclass
class ValidationReport:
    """Report detailing validation results."""

    is_valid: bool
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def raise_if_invalid(self) -> None:
        if not self.is_valid:
            raise TelemetryValidationError(
                f"Telemetry validation failed with {len(self.errors)} error(s): "
                + "; ".join(self.errors)
            )


# ── Telemetry Validator ───────────────────────────────────────────────────────

class TelemetryValidator:
    """Validates TelemetryMessage models or raw wire dictionaries."""

    @classmethod
    def validate_message(cls, msg: TelemetryMessage) -> ValidationReport:
        """Validate a typed TelemetryMessage instance."""
        errors: List[str] = []
        warnings: List[str] = []

        # 1. Schema version
        if msg.schema_version < 1:
            errors.append(f"Invalid schema_version: {msg.schema_version}")

        # 2. Timestamp semantics
        if not (MIN_REASONABLE_TIMESTAMP <= msg.timestamp <= MAX_REASONABLE_TIMESTAMP):
            errors.append(
                f"Timestamp {msg.timestamp} out of reasonable epoch bounds "
                f"[{MIN_REASONABLE_TIMESTAMP}, {MAX_REASONABLE_TIMESTAMP}]"
            )

        # 3. Sequence
        if msg.sequence < 0:
            errors.append(f"Negative sequence number: {msg.sequence}")

        # 4. Machine identity
        if not msg.machine.machine_id or not msg.machine.machine_id.strip():
            errors.append("machine.machine_id must be a non-empty string")
        if not msg.machine.asset_type or not msg.machine.asset_type.strip():
            errors.append("machine.asset_type must be a non-empty string")

        # 5. Operating state
        if not (PWM_MIN <= msg.operating_state.pwm_command <= PWM_MAX):
            errors.append(
                f"pwm_command {msg.operating_state.pwm_command} out of range [{PWM_MIN}, {PWM_MAX}]"
            )

        # 6. Sensor readings & physical boundaries
        s = msg.sensors
        if s.distance_cm is not None:
            if not (DISTANCE_MIN_CM <= s.distance_cm <= DISTANCE_MAX_CM):
                errors.append(
                    f"distance_cm {s.distance_cm} out of physical range [{DISTANCE_MIN_CM}, {DISTANCE_MAX_CM}]"
                )

        if s.current_a is not None:
            if not (CURRENT_MIN_A <= s.current_a <= CURRENT_MAX_A):
                errors.append(
                    f"current_a {s.current_a} out of physical range [{CURRENT_MIN_A}, {CURRENT_MAX_A}]"
                )

        if s.wheel_rpm is not None:
            if not (RPM_MIN <= s.wheel_rpm <= RPM_MAX):
                errors.append(
                    f"wheel_rpm {s.wheel_rpm} out of physical range [{RPM_MIN}, {RPM_MAX}]"
                )

        # 7. Sensor status
        st = msg.sensor_status
        for name, val in [("distance", st.distance), ("current", st.current), ("wheel_rpm", st.wheel_rpm)]:
            if val.lower() not in ALLOWED_SENSOR_STATUSES:
                warnings.append(f"Unrecognized sensor_status.{name}: '{val}'")

        # 8. Source info
        if not msg.source.source_type or not msg.source.source_type.strip():
            errors.append("source.source_type must be specified")
        if not msg.source.device_id or not msg.source.device_id.strip():
            errors.append("source.device_id must be specified")

        return ValidationReport(is_valid=len(errors) == 0, errors=errors, warnings=warnings)

    @classmethod
    def validate_dict(cls, data: Dict[str, Any]) -> ValidationReport:
        """Validate a wire dictionary for ground-truth leaks and contract conformance."""
        errors: List[str] = []
        warnings: List[str] = []

        # Check for forbidden ground truth leaks
        for key in FORBIDDEN_GROUND_TRUTH_KEYS:
            if key in data:
                errors.append(f"Forbidden ground-truth key '{key}' found in telemetry wire payload")

        # Check nested structures if present
        if isinstance(data.get("sensors"), dict):
            for key in FORBIDDEN_GROUND_TRUTH_KEYS:
                if key in data["sensors"]:
                    errors.append(f"Forbidden ground-truth key '{key}' found in sensors sub-model")

        # Attempt Pydantic model parsing
        try:
            msg = TelemetryMessage.model_validate(data)
            report = cls.validate_message(msg)
            errors.extend(report.errors)
            warnings.extend(report.warnings)
        except Exception as exc:
            errors.append(f"Schema parsing error: {exc}")

        return ValidationReport(is_valid=len(errors) == 0, errors=errors, warnings=warnings)


# ── Sequence Integrity Tracker ────────────────────────────────────────────────

class SequenceTracker:
    """Tracks sequence numbers within a source session to detect loss and duplicates."""

    def __init__(self, session_id: Optional[str] = None) -> None:
        self.session_id = session_id
        self._last_seq: Optional[int] = None
        self._received_count: int = 0
        self._gap_count: int = 0
        self._duplicate_count: int = 0
        self._out_of_order_count: int = 0

    def record(self, sequence: int) -> Dict[str, Any]:
        """Record an incoming sequence number.

        Returns a diagnosis dict with:
            is_duplicate, is_out_of_order, is_gap, missing_count
        """
        self._received_count += 1
        result = {
            "sequence": sequence,
            "is_duplicate": False,
            "is_out_of_order": False,
            "is_gap": False,
            "missing_count": 0,
        }

        if self._last_seq is None:
            self._last_seq = sequence
            return result

        expected = self._last_seq + 1

        if sequence == self._last_seq:
            result["is_duplicate"] = True
            self._duplicate_count += 1
        elif sequence < self._last_seq:
            result["is_out_of_order"] = True
            self._out_of_order_count += 1
        elif sequence > expected:
            missing = sequence - expected
            result["is_gap"] = True
            result["missing_count"] = missing
            self._gap_count += missing
            self._last_seq = sequence
        else:
            # Clean monotonic advancement
            self._last_seq = sequence

        return result

    @property
    def stats(self) -> Dict[str, int]:
        return {
            "total_received": self._received_count,
            "gaps_detected": self._gap_count,
            "duplicates": self._duplicate_count,
            "out_of_order": self._out_of_order_count,
        }

    def reset(self) -> None:
        self._last_seq = None
        self._received_count = 0
        self._gap_count = 0
        self._duplicate_count = 0
        self._out_of_order_count = 0
