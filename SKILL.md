---
name: kettu-mem
description: "Kettu Mem v0.2.0-rc1 — когнитивный слой памяти для OpenClaw. Production-ready. FastAPI, гибридный retrieval, изоляция сессий, Prometheus."
version: "0.2.0-rc1"
metadata:
  openclaw:
    emoji: "🦊"
    events: []
    requires:
      bins: ["python3", "curl"]
      env: ["HERMES_MEMORY_ENABLED"]
      python: ">=3.10"
---

# Kettu Mem v0.2.0-rc1 — Скилл для OpenClaw

Production-ready когнитивный слой памяти с гибридным retrieval, изоляцией сессий и Prometheus метриками.

## Быстрая установка

```bash
# 1. Клонировать
git clone https://github.com/neuratechcompany-ops/Kettu-Mem.git
cd Kettu-Mem

# 2. Зависимости
pip install -r requirements.txt --break-system-packages

# 3. Конфигурация (создать .env)
cat > .env << 'EOF'
HERMES_MEMORY_ENABLED=1
HERMES_MEMORY_DATA_DIR=~/.openclaw/memory-store
OPENAI_API_KEY=sk-...
HERMES_MEMORY_PORT=8765
EOF

# 4. Запустить сервер
python3 -m uvicorn src.api.server:app --host 127.0.0.1 --port 8765

# 5. Проверить
curl http://127.0.0.1:8765/health
# → {"status":"ok"}
```

## Docker

```bash
docker-compose up -d
```

## Проверка здоровья

```bash
curl http://127.0.0.1:8765/health
curl http://127.0.0.1:8765/ready
curl http://127.0.0.1:8765/metrics
```

## Основные endpoint'ы

| Метод | Путь | Назначение |
|---|---|---|
| GET | /health | Health check |
| GET | /ready | Readiness (7 layers) |
| GET | /live | Liveness |
| GET | /metrics | Prometheus |
| POST | /session/start | Начать сессию |
| POST | /turn/before | Собрать контекст |
| POST | /turn/after | Записать события |
| GET | /mem0/search?q= | Поиск в памяти |
| GET | /mem0/stats | Статистика Mem0 |
| POST | /cognitive/start | Запустить задачу |
| GET | /cognitive/state | Состояние планирования |

## Конфигурация (.env)

| Переменная | Значение по умолчанию |
|---|---|
| HERMES_MEMORY_ENABLED | 1 |
| HERMES_MEMORY_DATA_DIR | /tmp/mm-server |
| HERMES_MEMORY_PORT | 8765 |
| HERMES_MEMORY_API_KEY | (нет) |
| OPENAI_API_KEY | (обязательно для эмбеддингов) |

## Устранение неполадок

**Сервер не стартует:** проверь `OPENAI_API_KEY` в .env или `~/.openclaw/workspace/secrets/openai-key.txt`

**FAISS пустой:** сервер сам перестроит индекс при первом `/turn/before`. Или: `python3 -c "from src.memory.memory_manager import MemoryManager; mm = MemoryManager(); mm.rebuild_index()"`

**OOM:** уменьши `FAISS_DIM` в `kettu_mem.yaml` с 1536 до 384.

## Обновление с v0.1.0

```bash
git pull origin main
pip install -r requirements.txt --break-system-packages
# API обратно совместим — старые endpoint'ы сохранены
```

## Версия

**v0.2.0-rc1** — Release Candidate. 100 тестов, 71% coverage. Аудит 10/10 critical fixed.
