"""Compact Stage2 research planning and evidence compilation."""

from .evidence import (
    EVIDENCE_SCHEMA_VERSION,
    RESULT_SCHEMA_VERSION,
    ResearchValidationError,
    compile_research_results,
)
from .plan import (
    BATCH_SCHEMA_VERSION,
    PLAN_SCHEMA_VERSION,
    build_research_plan,
    write_research_plan,
)

__all__ = [
    "BATCH_SCHEMA_VERSION",
    "EVIDENCE_SCHEMA_VERSION",
    "PLAN_SCHEMA_VERSION",
    "RESULT_SCHEMA_VERSION",
    "ResearchValidationError",
    "build_research_plan",
    "compile_research_results",
    "write_research_plan",
]
