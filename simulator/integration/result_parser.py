"""
simulator/integration/result_parser.py
======================================
Parsers and normalization models for MachSight diagnostic outputs.

Converts raw MachSight WebSocket and REST payloads into typed records
for alignment with ground-truth data in evaluation workflows.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Union

logger = logging.getLogger(__name__)


@dataclass
class AnomalyDetectionRecord:
    """Normalized sample-level anomaly detection record from MachSight."""
    timestamp: float
    is_anomaly: bool
    anomaly_score: float
    raw_id: Optional[int] = None
    current_rpm_ratio: Optional[float] = None
    current_zscore: Optional[float] = None
    rpm_zscore: Optional[float] = None
    distance_plausible: bool = True
    bucket_used: str = ""
    machine_id: str = ""

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> AnomalyDetectionRecord:
        raw_is_anomaly = data.get("is_anomaly", 0)
        is_anom = bool(raw_is_anomaly) if not isinstance(raw_is_anomaly, str) else raw_is_anomaly.lower() in ("1", "true")
        score = float(data.get("mahalanobis_distance", data.get("anomaly_score", 0.0)))
        ts = float(data.get("timestamp", 0.0))
        return cls(
            timestamp=ts,
            is_anomaly=is_anom,
            anomaly_score=score,
            raw_id=data.get("raw_id", data.get("id")),
            current_rpm_ratio=data.get("current_rpm_ratio"),
            current_zscore=data.get("current_zscore"),
            rpm_zscore=data.get("rpm_zscore"),
            distance_plausible=bool(data.get("distance_plausible", True)),
            bucket_used=str(data.get("bucket_used", "")),
            machine_id=str(data.get("machine_id", "")),
        )


@dataclass
class InvestigationRecord:
    """Normalized fault investigation & diagnosis record from MachSight."""
    trace_id: str
    machine_id: str
    timestamp: float
    step_type: str
    diagnosis: str
    suspected_component: Optional[str]
    confidence: float
    severity: str
    reasoning: str
    payload: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> InvestigationRecord:
        trace_id = str(data.get("trace_id", ""))
        machine_id = str(data.get("machine_id", ""))
        ts = float(data.get("timestamp", 0.0))
        step_type = str(data.get("step_type", "diagnosis"))

        # Payload may be raw dict or JSON string
        payload_raw = data.get("payload", data.get("final_payload", {}))
        if isinstance(payload_raw, str):
            try:
                payload = json.loads(payload_raw) if payload_raw else {}
            except json.JSONDecodeError:
                payload = {"raw_text": payload_raw}
        elif isinstance(payload_raw, dict):
            payload = payload_raw
        else:
            payload = {}

        # Diagnosis string can be in root or payload
        diag = str(data.get("diagnosis") or payload.get("diagnosis", "unknown")).strip()
        comp = payload.get("suspected_component") or data.get("suspected_component")
        conf = float(payload.get("confidence", data.get("confidence", 0.0)))
        sev = str(payload.get("severity", data.get("severity", "info")))
        reason = str(payload.get("reasoning", payload.get("reason", data.get("reason", ""))))

        return cls(
            trace_id=trace_id,
            machine_id=machine_id,
            timestamp=ts,
            step_type=step_type,
            diagnosis=diag,
            suspected_component=str(comp) if comp else None,
            confidence=conf,
            severity=sev,
            reasoning=reason,
            payload=payload,
        )


@dataclass
class DiagnosticRunResult:
    """Aggregated detection and investigation results collected for a single run."""
    run_id: str
    machine_id: str
    anomaly_detections: List[AnomalyDetectionRecord] = field(default_factory=list)
    investigations: List[InvestigationRecord] = field(default_factory=list)
    infrastructure_errors: List[str] = field(default_factory=list)

    @property
    def has_anomaly(self) -> bool:
        return any(d.is_anomaly for d in self.anomaly_detections)

    @property
    def first_anomaly_timestamp(self) -> Optional[float]:
        for d in self.anomaly_detections:
            if d.is_anomaly:
                return d.timestamp
        return None

    @property
    def final_diagnosis(self) -> Optional[str]:
        if not self.investigations:
            return None
        # Return diagnosis from last investigation step
        return self.investigations[-1].diagnosis

    @property
    def final_suspected_component(self) -> Optional[str]:
        if not self.investigations:
            return None
        return self.investigations[-1].suspected_component

    @property
    def final_confidence(self) -> float:
        if not self.investigations:
            return 0.0
        return self.investigations[-1].confidence

    @property
    def diagnosis_timestamp(self) -> Optional[float]:
        if not self.investigations:
            return None
        return self.investigations[-1].timestamp


class ResultParser:
    """Utility functions to parse and normalize incoming events from MachSight."""

    @staticmethod
    def parse_broadcast_message(msg: Union[str, Dict[str, Any]]) -> Optional[Union[AnomalyDetectionRecord, InvestigationRecord]]:
        """Parse a message received over MachSight's /ws broadcast channel."""
        if isinstance(msg, str):
            try:
                data = json.loads(msg)
            except json.JSONDecodeError:
                logger.warning("Unparseable broadcast message: %s", msg)
                return None
        else:
            data = msg

        msg_type = data.get("type", data.get("event", ""))
        payload_data = data.get("data", data)

        if msg_type == "processed" or "mahalanobis_distance" in payload_data:
            return AnomalyDetectionRecord.from_dict(payload_data)

        if msg_type == "diagnosis" or payload_data.get("step_type") == "diagnosis":
            return InvestigationRecord.from_dict(payload_data)

        return None

    @staticmethod
    def parse_rest_telemetry(resp_data: Dict[str, Any]) -> List[AnomalyDetectionRecord]:
        """Parse records from MachSight GET /api/telemetry."""
        rows = resp_data.get("data", [])
        return [AnomalyDetectionRecord.from_dict(r) for r in rows if isinstance(r, dict)]

    @staticmethod
    def parse_rest_investigations(resp_data: Union[List[Dict[str, Any]], Dict[str, Any]]) -> List[InvestigationRecord]:
        """Parse records from MachSight GET /api/investigations."""
        rows = resp_data if isinstance(resp_data, list) else resp_data.get("data", resp_data.get("traces", []))
        return [InvestigationRecord.from_dict(r) for r in rows if isinstance(r, dict)]
