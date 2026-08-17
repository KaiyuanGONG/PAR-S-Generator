"""Research-grade synthetic-data preparation pipeline.

This package intentionally stops at finalized datasets.  It contains no
reconstruction, training, inference, checkpoint, or model-evaluation code.
"""

from pipeline.contracts import RunLayout, RunLedger, assign_fixed_splits

__all__ = [
    "PipelineConfig",
    "PipelineRunner",
    "RunLayout",
    "RunLedger",
    "assign_fixed_splits",
]


def __getattr__(name):
    """Keep the public convenience API without creating core import cycles."""
    if name in {"PipelineConfig", "PipelineRunner"}:
        from pipeline.runner import PipelineConfig, PipelineRunner

        return {"PipelineConfig": PipelineConfig, "PipelineRunner": PipelineRunner}[name]
    raise AttributeError(name)
