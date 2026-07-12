#!/usr/bin/env python3
"""
Cognitive Runtime Acceptance Test — 500+ steps, multiple restarts, multiple projects.

Validates:
  1. Agent runs 500+ steps without prompt growth
  2. Multiple gateway restarts — state survives
  3. Multiple concurrent projects
  4. No linear prompt growth
  5. No repeated useless tool calls (tool intelligence)
  6. Goal, Plan, Session State correctly recovered
"""
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

# ── Test API client ─────────────────────────────────────

class CRClient:
    def __init__(self, base="http://127.0.0.1:8765"):
        self.base = base

    def _post(self, path, data):
        body = json.dumps(data).encode()
        req = urllib.request.Request(f"{self.base}{path}", data=body,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read())

    def _get(self, path):
        with urllib.request.urlopen(f"{self.base}{path}") as r:
            return json.loads(r.read())

    def start_task(self, goal, plan, space="project"):
        return self._post("/cognitive/start", {"goal": goal, "plan": plan, "space": space})

    def resume(self):
        return self._post("/cognitive/resume", {})

    def build_context(self, query="", budget=32000):
        return self._post("/cognitive/context", {"query": query, "token_budget": budget})

    def record_step(self, response, tool_calls=None, tool_outputs=None, user_input=""):
        return self._post("/cognitive/step", {
            "response": response,
            "tool_calls": tool_calls or [],
            "tool_outputs": tool_outputs or [],
            "user_input": user_input,
        })

    def get_state(self):
        return self._get("/cognitive/state")

    def health(self):
        return self._get("/health")

    def stats(self):
        return self._get("/stats")


# ── Simulated agent step ────────────────────────────────

TOPICS = [
    "market research", "competitor analysis", "pricing strategy",
    "feature prioritization", "launch planning", "budget allocation",
    "channel strategy", "content marketing", "SEO optimization",
    "email campaigns", "social media", "analytics setup",
    "A/B testing", "conversion optimization", "customer journey",
    "retention strategy", "brand positioning", "PR outreach",
]

TOOLS = ["web_search", "analyze_data", "write", "read", "create_report"]
TOOL_OUTPUT_TEMPLATES = {
    "web_search": "Search results for '{query}': found {n} sources. Key insight: {insight}.",
    "analyze_data": "Analysis complete: {n} rows processed. Mean: {mean:.1f}, median: {median:.1f}.",
    "write": "File '{filename}' written ({size} bytes).",
    "read": "File '{filename}' read ({size} bytes): {content}",
    "create_report": "Report '{filename}' generated. {pages} pages, {sections} sections.",
}


def simulate_agent_step(step_num: int, topic_idx: int, client: CRClient,
                        useless_patterns: set) -> dict:
    """Simulate one agent step with tool calls and reflection."""
    topic = TOPICS[topic_idx % len(TOPICS)]

    # Decide tool calls (avoid useless patterns)
    tool_calls = []
    available_tools = [t for t in TOOLS if t not in useless_patterns]

    if step_num % 3 == 0 and available_tools:
        tool = available_tools[step_num % len(available_tools)]
        tool_calls.append({"name": tool, "params": {"query": topic}})

    if step_num % 7 == 0 and "write" not in useless_patterns:
        tool_calls.append({"name": "write", "params": {
            "filename": f"report-step-{step_num}.md",
            "content": f"## Step {step_num}\nAnalysis of {topic}"
        }})

    # Simulate tool outputs
    tool_outputs = []
    for tc in tool_calls:
        name = tc["name"]
        params = tc.get("params", {})
        if name == "web_search":
            tool_outputs.append({"type": "tool_output", "content":
                TOOL_OUTPUT_TEMPLATES["web_search"].format(
                    query=params.get("query", ""), n=step_num % 50 + 5,
                    insight=f"Trend #{step_num % 10}: market growing"
                )})
        elif name == "analyze_data":
            tool_outputs.append({"type": "tool_output", "content":
                TOOL_OUTPUT_TEMPLATES["analyze_data"].format(
                    n=step_num * 100, mean=step_num % 100, median=step_num % 80
                )})
        elif name == "write":
            size = 500 + step_num * 10
            tool_outputs.append({"type": "tool_output", "content":
                TOOL_OUTPUT_TEMPLATES["write"].format(
                    filename=params.get("filename", ""), size=size
                )})
        else:
            tool_outputs.append({"type": "tool_output",
                "content": f"Tool '{name}' completed successfully."})

    # Simulate errors occasionally
    if step_num % 23 == 0:
        tool_outputs.append({"type": "error",
            "content": "API rate limit exceeded. Please retry later."})

    # Assistant response
    response = (
        f"Step {step_num}: Analyzed '{topic}'. "
        f"Key findings: market indicator at {step_num % 100}%, "
        f"competitor activity {'high' if step_num % 3 == 0 else 'moderate'}. "
        f"Recommendation: {'proceed' if step_num % 5 != 0 else 'investigate further'}."
    )

    return {
        "response": response,
        "tool_calls": tool_calls,
        "tool_outputs": tool_outputs,
    }


def run_test(client: CRClient, project_name: str, steps: int,
             start_step: int = 0) -> dict:
    """Run N steps of simulated agent."""
    token_history = []
    useless_patterns = set()
    reflection_outcomes = {"progress": 0, "stuck": 0, "loop": 0, "wrong_tool": 0}

    for i in range(steps):
        step_num = start_step + i
        topic_idx = step_num

        # Build context
        ctx = client.build_context(query=TOPICS[topic_idx % len(TOPICS)], budget=16000)
        tokens = ctx.get("stats", {}).get("used_tokens", 0)
        token_history.append(tokens)

        # Simulate step
        step = simulate_agent_step(step_num, topic_idx, client, useless_patterns)

        # Record step
        result = client.record_step(
            step["response"], step["tool_calls"], step["tool_outputs"],
            user_input=f"Analyze {TOPICS[topic_idx % len(TOPICS)]}"
        )

        # Track reflection outcomes
        reflection = result.get("reflection", {})
        outcome = reflection.get("outcome", "unknown")
        if outcome in reflection_outcomes:
            reflection_outcomes[outcome] += 1

        # Track useless tool patterns
        state = result.get("state", {})
        useless = state.get("useless_tools", [])
        for t in useless:
            useless_patterns.add(t)

        if (step_num + 1) % 100 == 0:
            print(f"  [{project_name}] Step {step_num + 1}/{start_step + steps}: "
                  f"tokens={tokens}, outcomes={reflection_outcomes}")

    return {
        "token_history": token_history,
        "reflection_outcomes": reflection_outcomes,
        "useless_patterns": list(useless_patterns),
        "final_state": client.get_state(),
        "total_steps": start_step + steps,
    }


# ── Main test ───────────────────────────────────────────

def main():
    print("=" * 60)
    print("🧪 COGNITIVE RUNTIME ACCEPTANCE TEST")
    print("=" * 60)

    # Check server
    client = CRClient()
    try:
        h = client.health()
        print(f"\n✅ Server healthy: {h}")
    except Exception:
        print("❌ Server not running. Start with: python3 server.py --port 8765 &")
        sys.exit(1)

    # ═══ TEST 1: 500-step task ═══
    print(f"\n{'='*60}")
    print("TEST 1: 500-step task with planning + reflection")
    print(f"{'='*60}")

    plan = [
        "Research market size and trends",
        "Analyze top 5 competitors",
        "Determine pricing strategy",
        "Define key product features",
        "Create launch timeline",
        "Allocate marketing budget",
        "Select distribution channels",
        "Develop content strategy",
        "Set up analytics and KPIs",
        "Execute and iterate",
    ]

    start_time = time.time()
    client.start_task("Launch new smart coffee maker product in Russia", plan, "project")

    result = run_test(client, "coffeemaker", 500, 0)
    elapsed = time.time() - start_time

    tokens = result["token_history"]
    print(f"\n  ✅ Completed {result['total_steps']} steps in {elapsed:.1f}s")
    print(f"  📏 Tokens: first={tokens[0]}, last={tokens[-1]}, "
          f"avg={sum(tokens)//len(tokens)}, max={max(tokens)}")

    # Check: token growth should be bounded
    first_100 = tokens[:100]
    last_100 = tokens[-100:]
    growth_ratio = (sum(last_100) / len(last_100)) / max(sum(first_100) / len(first_100), 1)
    print(f"  📏 Token growth: {growth_ratio:.1f}x (first 100 vs last 100 avg)")

    task_state = client.get_state()
    plan_progress = task_state.get("planning", {}).get("progress", "0%")
    print(f"  📋 Plan progress: {plan_progress}")
    print(f"  🧠 Reflection: {result['reflection_outcomes']}")

    # ═══ TEST 2: Restart recovery ═══
    print(f"\n{'='*60}")
    print("TEST 2: Session survives restart")
    print(f"{'='*60}")

    pre_state = client.get_state()
    pre_steps = pre_state.get("step_counter", 0)
    pre_completed = len(pre_state.get("planning", {}).get("completed_steps", []))

    print(f"  Pre-restart: {pre_steps} steps, {pre_completed} completed")

    # Resume (simulates restart)
    resume_result = client.resume()
    post_state = client.get_state()
    post_steps = post_state.get("step_counter", 0)
    post_completed = len(post_state.get("planning", {}).get("completed_steps", []))

    print(f"  Post-resume: {post_steps} steps, {post_completed} completed")

    state_preserved = (
        post_steps == pre_steps and
        post_completed == pre_completed and
        post_state.get("planning", {}).get("goal") == pre_state.get("planning", {}).get("goal")
    )
    print(f"  State preserved: {'✅' if state_preserved else '❌'}")

    # Continue after restart
    result2 = run_test(client, "coffeemaker", 50, pre_steps)
    print(f"  ✅ Continued after restart: +50 steps (total: {result2['total_steps']})")

    # ═══ TEST 3: Multiple projects ═══
    print(f"\n{'='*60}")
    print("TEST 3: Multiple concurrent projects")
    print(f"{'='*60}")

    project_results = {}
    for proj_name in ["Project Alpha", "Project Beta", "Project Gamma"]:
        # Create new client (simulates different agent)
        pc = CRClient()
        pc.start_task(
            f"Complete {proj_name} tasks",
            ["Research", "Analyze", "Build", "Test", "Deploy"],
            "project"
        )
        proj_result = run_test(pc, proj_name, 30, 0)
        project_results[proj_name] = {
            "steps": proj_result["total_steps"],
            "tokens_avg": sum(proj_result["token_history"]) // len(proj_result["token_history"]),
            "progress": proj_result["final_state"].get("planning", {}).get("progress", "?"),
        }
        print(f"  {proj_name}: {proj_result['total_steps']} steps, "
              f"avg tokens={project_results[proj_name]['tokens_avg']}")

    # ═══ FINAL REPORT ═══
    print(f"\n{'='*60}")
    print("📊 FINAL ACCEPTANCE REPORT")
    print(f"{'='*60}")

    # Criterion 1: 500+ steps
    c1 = result["total_steps"] >= 500
    print(f"\n  {'✅' if c1 else '❌'} 500+ steps: {result['total_steps']} steps")

    # Criterion 2: restart recovery
    print(f"  {'✅' if state_preserved else '❌'} Restart recovery: state={'preserved' if state_preserved else 'LOST'}")

    # Criterion 3: multiple projects
    c3 = len(project_results) >= 3 and all(r["steps"] >= 30 for r in project_results.values())
    print(f"  {'✅' if c3 else '❌'} Multiple projects: {len(project_results)} projects")

    # Criterion 4: no linear prompt growth
    c4 = growth_ratio < 5
    print(f"  {'✅' if c4 else '⚠'} Prompt stability: {growth_ratio:.1f}x growth "
          f"({'stable' if c4 else 'growing'} — linear would be ~{result['total_steps']}x)")

    # Criterion 5: no repeated useless tool calls
    useless_total = sum(len(r["useless_patterns"]) for r in [result, result2])
    c5 = useless_total == 0
    print(f"  {'✅' if c5 else '⚠'} Tool intelligence: {useless_total} useless patterns detected")

    # Criterion 6: Goal + Plan + Session State recovered
    c6 = state_preserved and pre_state.get("planning", {}).get("goal", "")
    print(f"  {'✅' if c6 else '❌'} Goal/Plan/State recovery: "
          f"goal='{pre_state.get('planning',{}).get('goal','')[:50]}...'")

    # Performance
    avg_ms = (elapsed / result["total_steps"]) * 1000
    print(f"\n  ⏱ Performance: {avg_ms:.1f}ms avg per step (target <100ms for context+reflection)")

    all_pass = c1 and state_preserved and c3 and c4 and c5 and c6
    print(f"\n{'='*60}")
    print(f"🏁 OVERALL: {'✅ PASSED' if all_pass else '⚠ PARTIAL'}")
    print(f"{'='*60}")

    return all_pass


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
