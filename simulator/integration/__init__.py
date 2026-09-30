"""
simulator/integration
=====================
Integration and evaluation adapter connecting MachSight-Simulator with
the MachSight backend.
"""

from simulator.integration.evaluation_adapter import (
    MachSightEvaluator,
    RunEvaluationResult,
    normalize_fault_class,
)
from simulator.integration.machsight_client import (
    MachSightClient,
    MachSightConnectionError,
    MachSightProtocolError,
    MachSightTimeoutError,
)
from simulator.integration.result_parser import (
    AnomalyDetectionRecord,
    DiagnosticRunResult,
    InvestigationRecord,
    ResultParser,
)

__all__ = [
    "MachSightClient",
    "MachSightConnectionError",
    "MachSightProtocolError",
    "MachSightTimeoutError",
    "AnomalyDetectionRecord",
    "InvestigationRecord",
    "DiagnosticRunResult",
    "ResultParser",
    "MachSightEvaluator",
    "RunEvaluationResult",
    "normalize_fault_class",
]
