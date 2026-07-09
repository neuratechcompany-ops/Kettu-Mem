"""
MES Calculator — Memory Efficiency Score (0-100).

Components (weighted):
  Compression       20%
  Prompt Stability  15%
  Retrieval         15%
  Mem0              10%
  Archive           10%
  Context Builder   10%
  Latency           10%
  Recovery          10%
  Pollution         10% (bonus: clean = +10, dirty = deduction)
                   ----
                    100

MES = Σ (raw_component_score / max_component_score × weight × 100)
"""
from collections import OrderedDict
from dataclasses import dataclass


@dataclass(frozen=True)
class MESComponent:
    name: str
    weight: float
    max_score: float
    description: str


class MESCalculator:
    """Memory Efficiency Score calculator."""

    COMPONENTS = OrderedDict([
        ("compression", MESComponent(
            "Compression", 0.20, 20,
            "Raw history vs prompt ratio, summary quality, absence of degradation"
        )),
        ("prompt_stability", MESComponent(
            "Prompt Stability", 0.15, 15,
            "Prompt size curve across 10/50/100/300/500/1000 steps"
        )),
        ("retrieval", MESComponent(
            "Retrieval", 0.15, 15,
            "Recall@1/3/5/10, Precision@1/5, false/missed/irrelevant"
        )),
        ("mem0", MESComponent(
            "Mem0 Quality", 0.10, 10,
            "Hit rate, duplicates, contradictions, stale/low-confidence facts"
        )),
        ("archive", MESComponent(
            "Archive Integrity", 0.10, 10,
            "Append-only, JSONL valid, refs correct, search speed"
        )),
        ("context_builder", MESComponent(
            "Context Builder", 0.10, 10,
            "Build latency, utilisation, contributions, no leakage"
        )),
        ("semantic_index", MESComponent(
            "Semantic Index", 0.05, 10,
            "FAISS consistency, orphans, missing vectors, search speed"
        )),
        ("recovery", MESComponent(
            "Recovery", 0.10, 10,
            "Post-restart: L3, SQLite, FAISS, Mem0, refs, summaries"
        )),
        ("pollution", MESComponent(
            "Memory Pollution", 0.05, 10,
            "Duplicate entities/facts, obsolete, unused, temporary — lower is better"
        )),
    ])

    def calculate(self, metrics: dict) -> dict:
        total = 0.0
        breakdown = []

        for key, comp in self.COMPONENTS.items():
            group = metrics.get(key, {})
            raw = group.get("raw_score", 0) if isinstance(group, dict) else 0
            normalized = (raw / comp.max_score) * comp.weight * 100
            total += normalized

            breakdown.append({
                "component": comp.name,
                "raw_score": raw,
                "max_raw": comp.max_score,
                "weight": round(comp.weight, 2),
                "contribution": round(normalized, 1),
                "details": {k: v for k, v in group.items()
                           if k not in ("raw_score", "max_score")},
            })

        mes = round(total, 1)
        return {
            "mes": mes,
            "grade": self._grade(mes),
            "breakdown": breakdown,
            "interpretation": self._interpretation(mes, breakdown),
        }

    def format_report(self, mes_result: dict, detailed: bool = False) -> str:
        mes = mes_result["mes"]
        grade = mes_result["grade"]

        lines = [
            "═══════════════════════════════════════════",
            f"  🧠 MES: {mes}/100 — {grade}",
            "═══════════════════════════════════════════",
            "",
            "  Component Breakdown:",
        ]

        for b in mes_result["breakdown"]:
            pct = (b["contribution"] / max(b["weight"] * 100, 0.01)) * 100
            bar = self._bar(pct)
            lines.append(
                f"  {bar} {b['component']:<22s}  {b['contribution']:>5.1f}/{b['weight']*100:>3.0f}"
            )

        if detailed:
            lines.append("")
            lines.append("  📋 Detailed Metrics:")
            for b in mes_result["breakdown"]:
                if b.get("details"):
                    lines.append(f"  ── {b['component']} ──")
                    for k, v in b["details"].items():
                        lines.append(f"    {k}: {v}")

        lines.append("")
        lines.append(f"  📝 {mes_result['interpretation']}")
        lines.append("═══════════════════════════════════════════")

        return "\n".join(lines)

    @staticmethod
    def _grade(mes: float) -> str:
        if mes >= 90: return "🏆 Exceptional"
        elif mes >= 75: return "✨ Excellent"
        elif mes >= 60: return "✅ Good"
        elif mes >= 40: return "⚠️ Fair"
        elif mes >= 20: return "🔴 Poor"
        else: return "💀 Critical"

    @staticmethod
    def _interpretation(mes: float, breakdown: list) -> str:
        if mes >= 90:
            base = "Memory system operates at near-optimal efficiency."
        elif mes >= 75:
            base = "Memory system performs strongly with minor areas for improvement."
        elif mes >= 60:
            base = "Memory system is functional but several layers need attention."
        elif mes >= 40:
            base = "Significant memory efficiency gaps across multiple layers."
        elif mes >= 20:
            base = "Memory system has major issues requiring immediate attention."
        else:
            base = "Memory system is critically inefficient — fundamental redesign needed."

        weakest = min(breakdown, key=lambda b: b["contribution"] / max(b["weight"] * 100, 0.01))
        strongest = max(breakdown, key=lambda b: b["contribution"] / max(b["weight"] * 100, 0.01))

        return (
            f"{base}\n"
            f"💪 Strongest: {strongest['component']} ({strongest['contribution']}/{strongest['weight']*100:.0f})\n"
            f"🔧 Focus area: {weakest['component']} ({weakest['contribution']}/{weakest['weight']*100:.0f})"
        )

    @staticmethod
    def _bar(pct: float, width: int = 10) -> str:
        filled = int(pct / 100 * width)
        if pct >= 90:
            return f"[{'█' * filled}{'░' * (width - filled)}]"
        elif pct >= 60:
            return f"[{'▓' * filled}{'░' * (width - filled)}]"
        elif pct >= 30:
            return f"[{'▒' * filled}{'░' * (width - filled)}]"
        else:
            return f"[{'░' * filled}{' ' * (width - filled)}]"
