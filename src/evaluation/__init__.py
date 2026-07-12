"""
Hermes Evaluation Framework v1 — measurement layer for agent efficiency.

Components:
  - eval_store: SQLite + JSON storage for runs, steps, metrics
  - telemetry_collector: hooks into agent loop to collect raw data
  - metrics_engine: calculates all metric groups from raw data
  - haes_calculator: computes Hermes Agent Efficiency Score (0-100)
  - eval_framework: orchestrator + CLI integration
"""

from .eval_framework import EvaluationFramework
from .eval_store import EvalStore
from .haes_calculator import HAESCalculator
from .metrics_engine import MetricsEngine
from .telemetry_collector import TelemetryCollector

__all__ = [
    "EvalStore",
    "TelemetryCollector",
    "MetricsEngine",
    "HAESCalculator",
    "EvaluationFramework",
]
