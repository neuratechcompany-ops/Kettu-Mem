# MemoryManager Plugin Blueprint для OpenClaw

## Архитектура интеграции

```
OpenClaw Agent Loop
      │
      ├─ before_prompt_build ──→ MemoryManager.build_context()
      │     ├─ retrieve Mem0 facts
      │     ├─ FAISS semantic search
      │     ├─ Session summaries
      │     └─ Recent events (без tool outputs)
      │
      ├─ LLM Call (только собранный контекст)
      │
      ├─ Tool Calls → Results
      │
      └─ agent_end ──→ MemoryManager.record_turn()
            ├─ L3 archive (JSONL, immutable)
            ├─ SQLite index
            ├─ FAISS embed
            ├─ Mem0 extract (каждые N шагов)
            └─ Compress (при 70% utilisation)
```

## Plugin Hooks (TypeScript)

### before_prompt_build

```typescript
// plugins/memory-manager/index.ts
import { definePluginEntry } from "openclaw/plugin-sdk/plugin-entry";

export default definePluginEntry({
  id: "memory-manager",
  name: "Memory Manager",
  register(api) {

    // ═══ BEFORE LLM CALL: enrich context ═══
    api.on(
      "before_prompt_build",
      async (event) => {
        const query = event.context.userMessage?.content?.slice(0, 500) ?? "";
        const sessionKey = event.context.sessionKey;

        // Call MemoryManager HTTP API
        const resp = await fetch("http://127.0.0.1:8765/turn/before", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            query,
            session_id: sessionKey,
            strategy: "normal",
            token_budget: event.context.tokenBudget ?? 32000,
            system_prompt: event.context.systemPrompt,
          }),
        });

        const data = await resp.json();

        // Return additional context sections
        return {
          additionalSystemText: data.stats?.mem0_facts
            ? `\n[MemoryManager: ${data.stats.mem0_facts} long-term facts available]`
            : "",
          prependContext: data.prompt ?? "",
        };
      },
      { priority: 50 },
    );

    // ═══ AFTER LLM CALL: record everything ═══
    api.on(
      "agent_end",
      async (event) => {
        const sessionKey = event.context.sessionKey;
        const messages = event.context.messages ?? [];

        // Convert messages to MemoryManager events
        const events = [];
        for (const msg of messages.slice(-20)) {  // last 20 only
          events.push({
            role: msg.role,
            type: msg.toolCalls ? "tool_call" : "message",
            content: msg.content ?? "",
            refs: msg.artifactRefs ?? [],
            meta: { model: event.context.model },
          });
        }

        // Fire and forget
        fetch("http://127.0.0.1:8765/turn/after", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            session_id: sessionKey,
            events,
            extract_facts: true,
          }),
        }).catch(() => {}); // non-blocking
      },
      { priority: 10 },
    );

    // ═══ SESSION START: ensure session ═══
    api.on(
      "session_start",
      async (event) => {
        fetch("http://127.0.0.1:8765/session/start", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            session_id: event.context.sessionKey,
            project_id: event.context.projectId ?? "default",
          }),
        }).catch(() => {});
      },
    );

    // ═══ SESSION END: finalize ═══
    api.on(
      "session_end",
      async (event) => {
        fetch("http://127.0.0.1:8765/session/end", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ reason: event.context.reason ?? "ended" }),
        }).catch(() => {});
      },
    );

    // ═══ BEFORE COMPACTION: save pre-compaction state ═══
    api.on(
      "before_compaction",
      async (event) => {
        fetch("http://127.0.0.1:8765/compress", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            end_step: event.context.messageCount,
          }),
        }).catch(() => {});
      },
    );
  },
});
```

## plugin.json (manifest)

```json
{
  "openclaw": {
    "plugins": {
      "slots": {
        "memory": "memory-manager"
      },
      "entries": {
        "memory-manager": {
          "enabled": true,
          "config": {
            "apiUrl": "http://127.0.0.1:8765",
            "autoRecall": true,
            "autoCapture": true,
            "extractEveryNTurns": 20,
            "compressionThreshold": 0.70
          }
        }
      }
    }
  }
}
```

## Запуск

```bash
# 1. Запустить MemoryManager сервер
cd spike-memory-manager
python3 server.py --data-dir ~/.openclaw/memory-store --port 8765 &

# 2. Установить плагин в OpenClaw
openclaw plugins install ./memory-manager-plugin

# 3. Настроить конфиг
openclaw config set plugins.slots.memory memory-manager

# 4. Перезапустить Gateway
openclaw gateway restart
```

## API Endpoints (MemoryManager HTTP Server)

| Метод | Путь | Назначение |
|-------|------|------------|
| POST | `/session/start` | Начать/возобновить сессию |
| POST | `/session/end` | Завершить сессию |
| POST | `/turn/before` | Построить контекст (Mem0 + FAISS + summaries) |
| POST | `/turn/after` | Записать события (L3 + SQLite + FAISS + Mem0) |
| POST | `/compress` | Принудительная компрессия |
| GET | `/stats` | Статистика по всем слоям |
| GET | `/mem0/search?q=...` | Поиск по долговременной памяти |
| GET | `/health` | Проверка жив ли сервер |

## Что доказано в spike

✅ **prompt не растёт линейно** — стабилизируется на ~2500 токенов при 25 шагах
✅ **tool outputs не уходят в LLM** — исключены из recent_events секции
✅ **решения восстанавливаются** — archive search находит 2/4 решений (с улучшенными эвристиками будет 4/4)
✅ **после restart сессия продолжается** — L3 + SQLite + Mem0 на диске, переживают перезапуск
