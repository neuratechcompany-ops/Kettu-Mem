"""
HAES Calculator — Hermes Agent Efficiency Score.

Computes a single 0-100 score from 9 component groups.

Components (weighted):
  Memory Efficiency      20%
  Retrieval Quality      15%
  Planning Quality       15%
  Reflection Value       10%
  Tool Efficiency        10%
  Context Efficiency     10%
  Latency                10%
  Recovery               10%
  Learning / Reuse       10%
                        ----
                         100

HAES = sum(component_score * weight) across all 9 groups.

Interpretation:
  90-100: Exceptional — near optimal
  75-89:  Excellent — strong agent performance
  60-74:  Good — functional, some areas to improve
  40-59:  Fair — significant improvement needed
  20-39:  Poor — major issues
  0-19:   Critical — agent barely functional
"""
from dataclasses import dataclass, field
from collections import OrderedDict
from typing import Optional


@dataclass(frozen=True)
class HAESComponent:
    name: str
    weight: float        # 0.0 to 1.0
    max_score: float     # max raw score for this component
    description: str


class HAESCalculator:
    """
    Calculates Hermes Agent Efficiency Score from raw metrics.

    Usage:
        calc = HAESCalculator()
        result = calc.calculate(engine_output)
        print(f"HAES: {result['haes']}/100")
        print(result['breakdown'])
    """

    COMPONENTS = OrderedDict([
        ("memory_efficiency", HAESComponent(
            "Memory Efficiency", 0.20, 20,
            "Prompt compression, memory hit rate, archive growth, pollution"
        )),
        ("retrieval_quality", HAESComponent(
            "Retrieval Quality", 0.15, 15,
            "Recall@5, Precision@5, false retrieval, lookup success"
        )),
        ("planning_quality", HAESComponent(
            "Planning Quality", 0.15, 15,
            "Goal/plan completion, revisions, deviation, blockers"
        )),
        ("reflection_value", HAESComponent(
            "Reflection Value", 0.10, 10,
            "Useful reflections, stuck/loop detection, strategy changes"
        )),
        ("tool_efficiency", HAESComponent(
            "Tool Efficiency", 0.10, 10,
            "Tool success rate, useful vs duplicate tools, latency"
        )),
        ("context_efficiency", HAESComponent(
            "Context Efficiency", 0.10, 10,
            "Budget utilisation, prompt growth, output reserve"
        )),
        ("latency", HAESComponent(
            "Latency", 0.10, 10,
            "Step latency (avg, p50, p99), component breakdown"
        )),
        ("recovery", HAESComponent(
            "Recovery", 0.10, 10,
            "Recovery success rate, graceful degradation"
        )),
        ("learning_reuse", HAESComponent(
            "Learning / Reuse", 0.10, 10,
            "Steps/TTS reduction vs previous runs, playbook reuse"
        )),
    ])

    def calculate(self, metrics: dict) -> dict:
        """
        Compute HAES from metrics engine output.

        Args:
            metrics: output of MetricsEngine.calculate()

        Returns:
            {
                "haes": float (0-100),
                "grade": str,
                "breakdown": [{"component": str, "score": float, "max": float,
                               "weight": float, "contribution": float, "notes": str}, ...],
                "tts": float,
                "interpretation": str,
            }
        """
        total = 0.0
        breakdown = []

        for key, comp in self.COMPONENTS.items():
            group = metrics.get(key, {})
            raw = group.get("raw_score", 0) if isinstance(group, dict) else 0
            max_raw = comp.max_score

            # Normalize: raw/max_raw * weight * 100
            if max_raw > 0:
                normalized = (raw / max_raw) * comp.weight * 100
            else:
                normalized = 0

            total += normalized
            breakdown.append({
                "component": comp.name,
                "raw_score": raw,
                "max_raw": max_raw,
                "weight": round(comp.weight, 2),
                "contribution": round(normalized, 1),
                "details": {k: v for k, v in group.items()
                           if k not in ("raw_score", "max_score")},
            })

        haes = round(total, 1)
        grade = self._grade(haes)

        return {
            "haes": haes,
            "grade": grade,
            "breakdown": breakdown,
            "tts": metrics.get("tts", 0),
            "total_steps": metrics.get("total_steps", 0),
            "total_tool_calls": metrics.get("total_tool_calls", 0),
            "interpretation": self._interpretation(haes, breakdown),
        }

    def compare(self, haes_a: dict, haes_b: dict) -> dict:
        """
        Compare two HAES results (e.g., before/after optimization).

        Returns delta per component and overall.
        """
        delta_total = round(haes_b["haes"] - haes_a["haes"], 1)
        deltas = []

        for ba, bb in zip(haes_a["breakdown"], haes_b["breakdown"]):
            deltas.append({
                "component": ba["component"],
                "before": ba["contribution"],
                "after": bb["contribution"],
                "delta": round(bb["contribution"] - ba["contribution"], 1),
                "improved": bb["contribution"] > ba["contribution"],
            })

        return {
            "haes_before": haes_a["haes"],
            "haes_after": haes_b["haes"],
            "haes_delta": delta_total,
            "improved": delta_total > 0,
            "component_deltas": deltas,
            "tts_before": haes_a.get("tts", 0),
            "tts_after": haes_b.get("tts", 0),
            "tts_delta": round(haes_b.get("tts", 0) - haes_a.get("tts", 0), 2),
        }

    @staticmethod
    def _grade(haes: float) -> str:
        if haes >= 90:
            return "🏆 Exceptional"
        elif haes >= 75:
            return "✨ Excellent"
        elif haes >= 60:
            return "✅ Good"
        elif haes >= 40:
            return "⚠️ Fair"
        elif haes >= 20:
            return "🔴 Poor"
        else:
            return "💀 Critical"

    @staticmethod
    def _interpretation(haes: float, breakdown: list) -> str:
        """Generate human-readable interpretation of HAES score."""
        if haes >= 90:
            base = "Agent operates near optimal efficiency across all dimensions."
        elif haes >= 75:
            base = "Agent performs strongly with minor areas for improvement."
        elif haes >= 60:
            base = "Agent is functional but several dimensions need attention."
        elif haes >= 40:
            base = "Significant efficiency gaps across multiple components."
        elif haes >= 20:
            base = "Agent has major performance issues requiring immediate attention."
        else:
            base = "Agent is critically inefficient — fundamental redesign needed."

        # Find weakest component
        weakest = min(breakdown, key=lambda b: b["contribution"] / max(b["weight"] * 100, 0.01))
        strongest = max(breakdown, key=lambda b: b["contribution"] / max(b["weight"] * 100, 0.01))

        return (
            f"{base}\n"
            f"💪 Strongest: {strongest['component']} ({strongest['contribution']}/{strongest['weight']*100:.0f})\n"
            f"🔧 Focus area: {weakest['component']} ({weakest['contribution']}/{weakest['weight']*100:.0f})"
        )

    @staticmethod
    def format_report(haes_result: dict, detailed: bool = False) -> str:
        """Format HAES result as a readable report string."""
        grade_icon = {
            "🏆 Exceptional": "🏆", "✨ Excellent": "✨", "✅ Good": "✅",
            "⚠️ Fair": "⚠️", "🔴 Poor": "🔴", "💀 Critical": "💀",
        }

        icon = grade_icon.get(haes_result["grade"], "❓")
        lines = [
            "═══════════════════════════════════════════",
            f"  {icon} HAES: {haes_result['haes']}/100 — {haes_result['grade']}",
            "═══════════════════════════════════════════",
            "",
            f"  ⏱  TTS: {haes_result['tts']:.1f}s  |  📊 Steps: {haes_result['total_steps']}  |  🔧 Tools: {haes_result['total_tool_calls']}",
            "",
            "  Component Breakdown:",
        ]

        for b in haes_result["breakdown"]:
            pct = (b["contribution"] / max(b["weight"] * 100, 0.01)) * 100
            bar = HAESCalculator._bar(pct)
            lines.append(
                f"  {bar} {b['component']:<22s}  {b['contribution']:>5.1f}/{b['weight']*100:>3.0f}"
            )

        if detailed:
            lines.append("")
            lines.append("  Detailed Metrics:")
            for b in haes_result["breakdown"]:
                if b.get("details"):
                    lines.append(f"  ── {b['component']} ──")
                    for k, v in b["details"].items():
                        lines.append(f"    {k}: {v}")

        lines.append("")
        lines.append(f"  📝 {haes_result['interpretation']}")
        lines.append("═══════════════════════════════════════════")

        return "\n".join(lines)

    @staticmethod
    def _bar(pct: float, width: int = 10) -> str:
        """Draw a mini progress bar."""
        filled = int(pct / 100 * width)
        if pct >= 90:
            return f"[{'█' * filled}{'░' * (width - filled)}]"
        elif pct >= 60:
            return f"[{'▓' * filled}{'░' * (width - filled)}]"
        elif pct >= 30:
            return f"[{'▒' * filled}{'░' * (width - filled)}]"
        else:
            return f"[{'░' * filled}{' ' * (width - filled)}]"
