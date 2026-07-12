"""
Backward-compatible re-export shim — see retrieval/ package for canonical location.
"""

from retrieval.context_builder import (
    BudgetStrategy,
    ContextBuilder,
    ContextConfig,
    ToolSchema,
)

__all__ = ["ContextBuilder", "ContextConfig", "BudgetStrategy", "ToolSchema"]
