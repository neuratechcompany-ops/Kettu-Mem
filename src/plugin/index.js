/**
 * Hermes Memory Manager — OpenClaw Plugin (JavaScript)
 *
 * Feature flags:
 *   HERMES_MEMORY_ENABLED=1        — enable memory storage
 *   HERMES_COGNITIVE_RUNTIME=1     — enable cognitive runtime (Planning + Reflection + ToolIntelligence)
 *
 * Rollback:
 *   HERMES_COGNITIVE_RUNTIME=0     — cognitive off, memory-only mode (MemoryManager continues)
 *   HERMES_MEMORY_ENABLED=0        — full disable (all hooks silent)
 */

// ── Feature flags ───────────────────────────────────────

function isMemoryEnabled() {
  const envVar = process.env.HERMES_MEMORY_ENABLED;
  if (envVar === "0" || envVar === "false" || envVar === "no") return false;
  if (envVar === "1" || envVar === "true" || envVar === "yes") return true;
  return false;
}

function isCognitiveEnabled() {
  const envVar = process.env.HERMES_COGNITIVE_RUNTIME;
  if (envVar === "0" || envVar === "false" || envVar === "no") return false;
  if (envVar === "1" || envVar === "true" || envVar === "yes") return true;
  return false;
}

// ── HTTP client ─────────────────────────────────────────

async function mmFetch(apiUrl, path, body) {
  try {
    const resp = await fetch(`${apiUrl}${path}`, {
      method: body ? "POST" : "GET",
      headers: { "Content-Type": "application/json" },
      body: body ? JSON.stringify(body) : undefined,
      signal: AbortSignal.timeout(5000),
    });
    if (!resp.ok) return null;
    return await resp.json();
  } catch {
    return null;
  }
}

// ── Plugin entry ────────────────────────────────────────

export default {
  id: "hermes-memory",
  name: "Hermes Memory Manager",
  register(api) {
    const cfg = api.config || {};
    const apiUrl = cfg.apiUrl || "http://127.0.0.1:8765";
    const enabled = cfg.enabled !== false;
    const extractEveryN = cfg.extractEveryNTurns || 20;
    const strategy = cfg.contextStrategy || "normal";

    const sessionEventCounts = new Map();

    console.log(`[hermes-memory] Loaded. apiUrl=${apiUrl} enabled=${enabled} strategy=${strategy}`);
    console.log(`[hermes-memory] HERMES_MEMORY_ENABLED=${process.env.HERMES_MEMORY_ENABLED || 'unset'} (${isMemoryEnabled() ? 'ON' : 'OFF'})`);
    console.log(`[hermes-memory] HERMES_COGNITIVE_RUNTIME=${process.env.HERMES_COGNITIVE_RUNTIME || 'unset'} (${isCognitiveEnabled() ? 'ON' : 'OFF'})`);

    // ═══════════════════════════════════════════════════
    // session_start
    // ═══════════════════════════════════════════════════
    api.on("session_start", async (event) => {
      if (!isMemoryEnabled() || !enabled) return;
      const sessionKey = event.context?.sessionKey;
      if (!sessionKey) return;

      // Memory: start/resume session
      await mmFetch(apiUrl, "/session/start", {
        session_id: sessionKey,
        project_id: "hermes",
      });

      // Cognitive: try resume PlanningState
      if (isCognitiveEnabled()) {
        const crState = await mmFetch(apiUrl, "/cognitive/resume", {});
        if (crState && crState.status === "resumed") {
          const s = crState.state?.planning;
          console.log(`[hermes-memory] cognitive: resumed "${s?.goal?.slice(0,60)}" (${crState.state?.step_counter} steps, ${s?.progress})`);
        } else {
          console.log(`[hermes-memory] cognitive: no saved state for ${sessionKey}, starting fresh`);
          // Start a default task if no state exists
          const userMsg = event.context?.userMessage || "";
          if (userMsg) {
            await mmFetch(apiUrl, "/cognitive/start", {
              goal: userMsg.slice(0, 200),
              plan: ["Understand request", "Gather information", "Execute", "Verify", "Report"],
              space: "project",
            });
          }
        }
      }

      sessionEventCounts.set(sessionKey, 0);
      console.log(`[hermes-memory] session_start: ${sessionKey}`);
    }, { priority: 30 });

    // ═══════════════════════════════════════════════════
    // before_prompt_build — Dynamic Context
    // ═══════════════════════════════════════════════════
    api.on("before_prompt_build", async (event) => {
      if (!isMemoryEnabled() || !enabled) return;
      const sessionKey = event.context?.sessionKey;
      if (!sessionKey) return;

      const userMsg = event.context?.userMessage;
      const query =
        typeof userMsg === "string"
          ? userMsg.slice(0, 500)
          : userMsg?.content?.slice(0, 500) || "";

      const budget = event.context?.tokenBudget || 32000;

      // Cognitive: use CognitiveRuntime's dynamic context builder
      if (isCognitiveEnabled()) {
        const resp = await mmFetch(apiUrl, "/cognitive/context", {
          query,
          token_budget: budget,
        });
        if (resp && resp.prompt) {
          return { prependContext: resp.prompt };
        }
      }

      // Fallback: memory-only context
      const resp = await mmFetch(apiUrl, "/turn/before", {
        query,
        session_id: sessionKey,
        strategy,
        token_budget: budget,
        system_prompt: event.context?.systemPrompt,
      });

      if (!resp || !resp.prompt) return;
      return { prependContext: resp.prompt };
    }, { priority: 40 });

    // ═══════════════════════════════════════════════════
    // after_tool_call — ToolIntelligence
    // ═══════════════════════════════════════════════════
    api.on("after_tool_call", async (event) => {
      if (!isMemoryEnabled() || !enabled) return;
      const sessionKey = event.context?.sessionKey;
      if (!sessionKey) return;

      const toolOutput = event.result?.content || event.result?.output || "";
      const toolName = event.toolName || "unknown";
      const error = event.result?.error;

      const evt = {
        role: "tool",
        type: error ? "error" : "tool_output",
        content: error
          ? `Error in ${toolName}: ${String(error).slice(0, 500)}`
          : `[${toolName}] ${String(toolOutput).slice(0, 1000)}`,
      };

      // Memory: record tool output
      await mmFetch(apiUrl, "/turn/after", {
        session_id: sessionKey,
        events: [evt],
        extract_facts: false,
      });

      // Cognitive: feed tool result to ToolIntelligence
      if (isCognitiveEnabled()) {
        await mmFetch(apiUrl, "/cognitive/reflect", {
          tool_calls: [{ name: toolName, params: {} }],
          tool_outputs: [evt],
        });
      }

      const count = (sessionEventCounts.get(sessionKey) || 0) + 1;
      sessionEventCounts.set(sessionKey, count);
    }, { priority: 20 });

    // ═══════════════════════════════════════════════════
    // agent_end — ReflectionEngine + PlanningState update
    // ═══════════════════════════════════════════════════
    api.on("agent_end", async (event) => {
      if (!isMemoryEnabled() || !enabled) return;
      const sessionKey = event.context?.sessionKey;
      if (!sessionKey) return;

      const messages = event.context?.messages || [];
      const events = [];
      let assistantResponse = "";
      const toolCalls = [];
      const toolOutputs = [];

      for (const msg of messages) {
        const role = msg.role || "unknown";
        const content = msg.content || "";

        if (role === "user") {
          events.push({ role: "user", type: "message", content });
        } else if (role === "assistant") {
          assistantResponse = content;
          events.push({ role: "assistant", type: "message", content });
          if (msg.toolCalls && Array.isArray(msg.toolCalls)) {
            for (const tc of msg.toolCalls) {
              toolCalls.push({ name: tc.name || "unknown", params: tc.params || {} });
              events.push({
                role: "assistant",
                type: "tool_call",
                content: `${tc.name || "unknown"}(${JSON.stringify(tc.params || {})})`,
              });
            }
          }
        } else if (role === "tool") {
          toolOutputs.push({ type: "tool_output", content });
        }
      }

      // Memory: record events
      if (events.length > 0) {
        const count = (sessionEventCounts.get(sessionKey) || 0) + events.length;
        sessionEventCounts.set(sessionKey, count);
        const doExtract = count >= extractEveryN && count % extractEveryN === 0;

        await mmFetch(apiUrl, "/turn/after", {
          session_id: sessionKey,
          events,
          extract_facts: doExtract,
        });
      }

      // Cognitive: run ReflectionEngine + update PlanningState
      if (isCognitiveEnabled() && (assistantResponse || toolCalls.length > 0)) {
        const userMsg = messages.find(m => m.role === "user")?.content || "";
        const crResult = await mmFetch(apiUrl, "/cognitive/step", {
          response: assistantResponse,
          tool_calls: toolCalls,
          tool_outputs: toolOutputs,
          user_input: userMsg,
        });

        if (crResult) {
          const ref = crResult.reflection;
          const st = crResult.state?.planning;
          if (ref?.outcome === "stuck" || ref?.outcome === "loop") {
            console.log(`[hermes-memory] reflection: ${ref.outcome} — ${ref.suggestion?.slice(0,80)}`);
          }
          if (st) {
            console.log(`[hermes-memory] plan: ${st.progress} | next: ${st.next_action || st.current_step || '—'}`);
          }
        }
      }
    }, { priority: 10 });

    // ═══════════════════════════════════════════════════
    // session_end — persist PlanningState
    // ═══════════════════════════════════════════════════
    api.on("session_end", async (event) => {
      if (!isMemoryEnabled() || !enabled) return;
      const sessionKey = event.context?.sessionKey;
      if (!sessionKey) return;

      // Memory: finalize
      await mmFetch(apiUrl, "/session/end", {
        reason: event.context?.reason || "ended",
        extract_facts: true,
      });

      // Cognitive: state already persisted by /cognitive/step
      if (isCognitiveEnabled()) {
        const state = await mmFetch(apiUrl, "/cognitive/state", null);
        if (state?.planning?.goal) {
          console.log(`[hermes-memory] cognitive: state saved — "${state.planning.goal.slice(0,60)}" (${state.planning.progress})`);
        }
      }

      sessionEventCounts.delete(sessionKey);
      console.log(`[hermes-memory] session_end: ${sessionKey}`);
    }, { priority: 10 });
  },
};
