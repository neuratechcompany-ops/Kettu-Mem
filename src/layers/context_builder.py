"""
Backward-compatible re-export shim — see retrieval/ package for canonical location.
"""
from retrieval.context_builder import (
    ContextBuilder,
    ContextConfig,
    BudgetStrategy,
    ToolSchema,
)

__all__ = ["ContextBuilder", "ContextConfig", "BudgetStrategy", "ToolSchema"]
