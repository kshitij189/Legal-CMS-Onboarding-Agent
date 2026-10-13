"""The in-memory CMS lives in app.evaluation.simulation (shared with the evaluation suite)."""

from app.evaluation.simulation import InMemoryCMS as FakeCMS
from app.evaluation.simulation import default_configuration

__all__ = ["FakeCMS", "default_configuration"]
