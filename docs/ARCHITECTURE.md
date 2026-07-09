# Kettu Mem Architecture — v0.2.0

## High-Level Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                      OpenClaw Gateway                        │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────┐  │
│  │ Plugin Hook  │  │ Agent Loop  │  │ Session Management  │  │
│  └──────┬───────┘  └──────┬──────┘  └──────────┬──────────┘  │
└─────────┼─────────────────┼────────────────────┼─────────────┘
          │                 │                    │
          ▼                 ▼                    ▼
┌─────────────────────────────────────────────────────────────┐
│                   Kettu Mem HTTP API (:8765)                 │
│  FastAPI + Uvicorn  │  /health /ready /live                 │
│  Security: API Key, Rate Limiting, Input Validation         │
│  Logging: Structlog + Request ID  │  Metrics: Prometheus    │
└──────────────────────┬──────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────────┐
│                    MemoryManager                             │
│                 (Thin Orchestrator)                          │
└───┬──────────┬──────────┬──────────┬──────────┬─────────────┘
    │          │          │          │          │
    ▼          ▼          ▼          ▼          ▼
┌────────┐┌────────┐┌────────┐┌────────┐┌──────────────┐
│Storage ││Embed-  ││Retriev-││Extrac- ││   Evaluation  │
│ Layer  ││dings   ││al      ││tors    ││   (MES/HAES)  │
│        ││        ││        ││        ││              │
│• L3    ││• OpenAI││• Context││• Mem0  ││• MES Calc    │
│  JSONL ││  1536d ││  Builder││  Store ││  83→85+      │
│• SQLite││• ST    ││• Hybrid ││• Compr-││• HAES Calc   │
│  Meta  ││  MiniLM││  Search ││  ession││• Telemetry   │
│• Sess. ││• Random││  BM25+  ││• Inges-││              │
│  Isol. ││  Fallbk││  FAISS+ ││  tion  ││              │
│        ││        ││  RRF    ││  Filter││              │
└────────┘└────────┘└────────┘│• Quality│└──────────────┘
                               │  Score  │
                               │• Cogni- │
                               │  tive RT│
                               └─────────┘
```

## Package Structure

```
src/
├── api/              # HTTP layer (FastAPI + Uvicorn)
│   ├── server.py     # Application + endpoints (20+)
│   ├── security.py   # API key, rate limiting, validation
│   └── metrics.py    # Prometheus /metrics
├── memory/           # Orchestrator
│   └── memory_manager.py  # Thin orchestrator
├── storage/          # Persistent storage
│   ├── l3_verbatim.py      # Immutable JSONL archive
│   ├── sqlite_index.py     # Relational metadata
│   └── session_isolation.py # Hierarchical namespaces
├── embeddings/       # Vector encoding
│   └── faiss_index.py      # OpenAI/ST/random embeddings + FAISS
├── retrieval/        # Search + context
│   ├── context_builder.py  # Token-budgeted prompt assembly
│   └── hybrid_search.py    # BM25 + FAISS + RRF fusion
├── extractors/       # Knowledge extraction
│   ├── mem0.py             # Long-term memory (ADD-only)
│   ├── compression.py      # Event summarization
│   ├── cognitive_runtime.py # Planning + reflection
│   ├── ingestion_filter.py  # Pre-ingestion filtering
│   └── memory_quality.py    # Scoring, TTL, decay
├── evaluation/       # MES/HAES frameworks
├── config/           # pydantic-settings
│   └── settings.py
├── utils/            # Common utilities
│   └── logging.py          # Structlog
└── tests/            # pytest suite
```

## Data Flow

### Ingestion Path
```
User/Agent Event
    │
    ▼
[IngestionFilter] ──→ reject? → log + skip
    │ (ok)
    ▼
[L3 Archive] ──→ immutable append
    │
    ▼
[SQLite] ──→ metadata index
    │
    ▼
[FAISS] ──→ embed + vector store
    │
    ▼ (every N events)
[Mem0] ──→ extract preferences/decisions/entities
```

### Retrieval Path
```
Query
    │
    ▼
[Query Normalization]
    │
    ├──→ [BM25] ──→ keyword scores
    │
    └──→ [FAISS] ──→ semantic scores
    │
    ▼
[RRF Fusion] ──→ merged ranked list
    │
    ▼
[Context Builder] ──→ layered assembly
    │   ├── System prompt
    │   ├── Recent events
    │   ├── Mem0 long-term facts
    │   ├── Semantic results
    │   └── Summaries
    ▼
Final Prompt (under token budget)
```

## Memory Quality Scoring
```
memory_score = 
    importance × 0.3 +     # type-based (decision > preference > fact)
    recency    × 0.3 +     # exponential decay (half-life ~13.5 days)
    confidence × 0.2 +     # raw extraction confidence
    access     × 0.2       # log-scaled access count
```

## Session Isolation Hierarchy
```
project → workspace → agent → user → session

Example: "myproject/main/agent1/nastya/session-42"
```

## API Endpoints (20+)
| Method | Path | Description |
|--------|------|-------------|
| GET | /health | Liveness check |
| GET | /ready | Readiness check (all layers) |
| GET | /live | Combined health + readiness |
| GET | /health/deep | Deep health check (v0.1 compat) |
| GET | /metrics | Prometheus metrics |
| GET | /stats | Full layer statistics |
| POST | /session/start | Start/resume session |
| POST | /session/end | Finalize session |
| POST | /turn/before | Build LLM context |
| POST | /turn/after | Record events |
| POST | /compress | Manual compression |
| GET | /events/last | Recent events |
| GET | /mem0/search | Search memory |
| GET | /mem0/all | List all facts |
| GET | /mem0/stats | Mem0 statistics |
| GET | /mem0/entities | Entity list |
| POST | /mem0/add | Add fact |
| POST | /cognitive/start | Start task |
| POST | /cognitive/resume | Resume task |
| POST | /cognitive/context | Build cognitive context |
| POST | /cognitive/step | Record step |
| POST | /cognitive/reflect | Reflection |
| POST | /cognitive/strategy | Adjust strategy |
| GET/POST | /cognitive/state | Current state |
| POST | /cognitive/space | Set memory space |
