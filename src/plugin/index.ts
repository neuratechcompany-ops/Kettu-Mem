/**
 * Hermes Memory Manager — OpenClaw Plugin
 *
 * Integrates MemoryManager into the agent loop via plugin hooks:
 *   - session_start: ensure session in MemoryManager
 *   - before_prompt_build: retrieve Mem0 facts + FAISS + build context
 *   - agent_end: record assistant response + tool calls
 *   - after_tool_call: record tool outputs
 *   - session_end: finalize session
 *
 * Feature flag: HERMES_MEMORY_ENABLED=1  (set to 0 to disable)
 */

import { definePluginEntry } from "openclaw/plugin-sdk/plugin-entry";

// ── Feature flag ────────────────────────────────────────

function isMemoryEnabled(): boolean {
  const envVar = process.env.HERMES_MEMORY_ENABLED;
  if (envVar === "0" || envVar === "false" || envVar === "no") return false;
  if (envVar === "1" || envVar === "true" || envVar === "yes") return true;
  // Default: disabled unless explicitly enabled
  return false;
}

// ── HTTP client ─────────────────────────────────────────

interface MMEvent {
  role: string;
  type: string;
  content: string;
  refs?: Array<[string, string]>;
  meta?: Record<string, unknown>;
}

interface MMResponse {
  status: string;
  prompt?: string;
  stats?: Record<string, unknown>;
  event_ids?: string[];
  count?: number;
  total_events?: number;
  mem0_facts?: number;
  error?: string;
}

async function mmFetch(
  apiUrl: string,
  path: string,
  body?: Record<string, unknown>,
): Promise<MMResponse | null> {
  try {
    const resp = await fetch(`${apiUrl}${path}`, {
      method: body ? "POST" : "GET",
      headers: { "Content-Type": "application/json" },
      body: body ? JSON.stringify(body) : undefined,
      signal: AbortSignal.timeout(5000), // 5s timeout
    });
    if (!resp.ok) return null;
    return (await resp.json()) as MMResponse;
  } catch {
    // Silently fail — memory is non-critical
    return null;
  }
}

// ── Plugin entry ────────────────────────────────────────

export default definePluginEntry({
  id: "hermes-memory",
  name: "Hermes Memory Manager",
  register(api) {
    const cfg = api.config as {
      apiUrl?: string;
      enabled?: boolean;
      extractEveryNTurns?: number;
      compressionThreshold?: number;
      contextStrategy?: string;
    };

    const apiUrl = cfg.apiUrl ?? "http://127.0.0.1:8765";
    const enabled = cfg.enabled !== false;
    const extractEveryN = cfg.extractEveryNTurns ?? 20;
    const strategy = cfg.contextStrategy ?? "normal";

    // Track per-session event counter for Mem0 extraction
    const sessionEventCounts = new Map<string, number>();

    console.log(`[hermes-memory] Plugin loaded. apiUrl=${apiUrl} enabled=${enabled} strategy=${strategy}`);

    // ═══════════════════════════════════════════════════
    // session_start
    // ═══════════════════════════════════════════════════
    api.on(
      "session_start",
      async (event) => {
        if (!isMemoryEnabled() || !enabled) return;

        const sessionKey = event.context?.sessionKey;
        if (!sessionKey) return;

        await mmFetch(apiUrl, "/session/start", {
          session_id: sessionKey,
          project_id: "hermes",
        });

        sessionEventCounts.set(sessionKey, 0);
        console.log(`[hermes-memory] Session started: ${sessionKey}`);
      },
      { priority: 30 },
    );

    // ═══════════════════════════════════════════════════
    // before_prompt_build
    // ═══════════════════════════════════════════════════
    api.on(
      "before_prompt_build",
      async (event) => {
        if (!isMemoryEnabled() || !enabled) return;

        const sessionKey = event.context?.sessionKey;
        if (!sessionKey) return;

        // Extract query from user message
        const userMsg = event.context?.userMessage;
        const query =
          typeof userMsg === "string"
            ? userMsg.slice(0, 500)
            : userMsg?.content?.slice(0, 500) ?? "";

        const budget = event.context?.tokenBudget ?? 32000;

        const resp = await mmFetch(apiUrl, "/turn/before", {
          query,
          session_id: sessionKey,
          strategy,
          token_budget: budget,
          system_prompt: event.context?.systemPrompt,
        });

        if (!resp?.prompt) return;

        // Return context for prompt assembly
        return {
          prependContext: resp.prompt,
        };
      },
      { priority: 40 },
    );

    // ═══════════════════════════════════════════════════
    // after_tool_call
    // ═══════════════════════════════════════════════════
    api.on(
      "after_tool_call",
      async (event) => {
        if (!isMemoryEnabled() || !enabled) return;

        const sessionKey = event.context?.sessionKey;
        if (!sessionKey) return;

        // Record tool output
        const toolOutput = event.result?.content ?? event.result?.output ?? "";
        const toolName = event.toolName ?? "unknown";
        const error = event.result?.error;

        const evt: MMEvent = {
          role: "tool",
          type: error ? "error" : "tool_output",
          content: error
            ? `Error in ${toolName}: ${String(error).slice(0, 500)}`
            : `[${toolName}] ${String(toolOutput).slice(0, 1000)}`,
        };

        await mmFetch(apiUrl, "/turn/after", {
          session_id: sessionKey,
          events: [evt],
          extract_facts: false,
        });

        // Increment counter
        const count = (sessionEventCounts.get(sessionKey) ?? 0) + 1;
        sessionEventCounts.set(sessionKey, count);
      },
      { priority: 20 },
    );

    // ═══════════════════════════════════════════════════
    // agent_end
    // ═══════════════════════════════════════════════════
    api.on(
      "agent_end",
      async (event) => {
        if (!isMemoryEnabled() || !enabled) return;

        const sessionKey = event.context?.sessionKey;
        if (!sessionKey) return;

        const messages = event.context?.messages ?? [];
        const events: MMEvent[] = [];

        for (const msg of messages) {
          const role = msg.role ?? "unknown";
          const content = msg.content ?? "";

          if (role === "user") {
            events.push({ role: "user", type: "message", content });
          } else if (role === "assistant") {
            events.push({ role: "assistant", type: "message", content });
            // Tool calls within assistant message
            if (msg.toolCalls && Array.isArray(msg.toolCalls)) {
              for (const tc of msg.toolCalls) {
                events.push({
                  role: "assistant",
                  type: "tool_call",
                  content: `${tc.name ?? "unknown"}(${JSON.stringify(tc.params ?? {})})`,
                });
              }
            }
          }
        }

        if (events.length === 0) return;

        // Increment counter
        const count = (sessionEventCounts.get(sessionKey) ?? 0) + events.length;
        sessionEventCounts.set(sessionKey, count);

        // Extract Mem0 facts periodically
        const doExtract = count >= extractEveryN && count % extractEveryN === 0;

        await mmFetch(apiUrl, "/turn/after", {
          session_id: sessionKey,
          events,
          extract_facts: doExtract,
        });

        if (doExtract) {
          console.log(`[hermes-memory] Mem0 extraction triggered at ${count} events`);
        }
      },
      { priority: 10 },
    );

    // ═══════════════════════════════════════════════════
    // session_end
    // ═══════════════════════════════════════════════════
    api.on(
      "session_end",
      async (event) => {
        if (!isMemoryEnabled() || !enabled) return;

        const sessionKey = event.context?.sessionKey;
        if (!sessionKey) return;

        await mmFetch(apiUrl, "/session/end", {
          reason: event.context?.reason ?? "ended",
          extract_facts: true,
        });

        sessionEventCounts.delete(sessionKey);
        console.log(`[hermes-memory] Session ended: ${sessionKey}`);
      },
      { priority: 10 },
    );
  },
});
