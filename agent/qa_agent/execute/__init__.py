"""Browser execution: harness, semantic actions and evidence capture."""

from .browser import DESKTOP, ActionError, Harness, ScenarioRun
from .evidence import EvidenceRecorder, NetworkRecord, StepRecord

__all__ = ["DESKTOP", "ActionError", "EvidenceRecorder", "Harness", "NetworkRecord", "ScenarioRun", "StepRecord"]
