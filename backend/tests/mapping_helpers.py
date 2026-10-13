"""Shared test helpers; the implementations live in app.evaluation (shared with the eval suite)."""

from app.evaluation.fixtures import expected, ground_truth_proposals, source_schema
from app.evaluation.simulation import ScriptedLLM
from app.evaluation.simulation import target_snapshot as target_schema

FakeLLM = ScriptedLLM

__all__ = ["FakeLLM", "expected", "ground_truth_proposals", "source_schema", "target_schema"]
