# 🦊 Kettu Mem — Руководство по установке для AI-агентов

> **Целевая аудитория:** AI-агенты (OpenClaw и другие), которые скачали этот репозиторий и хотят установить Kettu Mem.
> **Язык:** инструкции написаны так, чтобы агент мог выполнить их автономно — копируй команды и исполняй.

---

## 📋 Системные требования

| Компонент | Минимум | Рекомендовано |
|---|---|---|
| Python | 3.11+ | 3.12 |
| RAM | 256 MB | 512 MB |
| Диск | 100 MB + данные | 1 GB |
| OS | Linux / macOS | Linux (Ubuntu 22.04+) |
| Сеть | localhost:8765 | localhost:8765 |

**Необязательно, но улучшает качество:**
- OpenAI API key (для качественных эмбеддингов)
- sentence-transformers (для локальных эмбеддингов без API)

---

## 🚀 Быстрая установка (3 шага)

### Шаг 1: Проверка окружения

```bash
python3 --version  # должно быть >= 3.11
pip --version       # должен быть доступен
```

### Шаг 2: Установка зависимостей

```bash
cd "/путь/к/Kettu Mem"

# Основные зависимости
pip install fastapi uvicorn[standard] pydantic pydantic-settings \
  structlog prometheus-client psutil python-multipart \
  numpy faiss-cpu tiktoken openai

# Опционально: локальные эмбеддинги
pip install sentence-transformers

# Опционально: инструменты разработки
pip install pytest pytest-cov
```

### Шаг 3: Запуск сервера

```bash
cd src
python3 -m uvicorn api.server:app --host 127.0.0.1 --port 8765 &
```

Проверка:

```bash
curl http://127.0.0.1:8765/health
# Ожидаемый ответ: {"status":"ok"}

curl http://127.0.0.1:8765/ready
# Ожидаемый ответ: {"status":"ready","layers":{...}}
```

---

## 🐳 Установка через Docker

```bash
cd "/путь/к/Kettu Mem"
docker compose up -d

# Проверка
curl http://127.0.0.1:8765/health
```

Docker автоматически:
- Создаст volume для данных
- Настроит healthcheck
- Ограничит память (512 MB)

---

## ⚙️ Конфигурация

### Через .env файл

```bash
# Создать в корне проекта
cat > .env << 'EOF'
KETTU_MEM_DATA_DIR=~/.openclaw/memory-store
KETTU_MEM_PORT=8765
KETTU_MEM_LOG_LEVEL=INFO
KETTU_MEM_TTL_DAYS=90
KETTU_MEM_EMBEDDING_BACKEND=auto
EOF
```

### Через переменные окружения

```bash
export KETTU_MEM_DATA_DIR=~/.openclaw/memory-store
export KETTU_MEM_PORT=8765
export OPENAI_API_KEY="sk-..."  # если используешь OpenAI эмбеддинги
```

### Конфиг-файл

Файл `kettu_mem.yaml` уже в корне проекта — правь под свои нужды.

---

## 🔧 Диагностика (hermes_doctor)

После установки всегда запускай доктора:

```bash
python3 scripts/hermes_doctor.py
```

Доктор проверит:
- ✅ Python и зависимости
- ✅ Сервер (healthcheck)
- ✅ FAISS index
- ✅ SQLite database
- ✅ L3 archive
- ✅ Mem0 facts
- ✅ Cognitive Runtime
- ✅ Данные и дисковое пространство

Если что-то красное — читай вывод, там будет конкретная команда для исправления.

---

## 🧪 Проверка работоспособности

### Тесты

```bash
cd src
python3 -m pytest tests/test_all.py -v
```

### Оценочный фреймворк (вшит)

```bash
cd src
python3 tests/test_evaluation.py
# Прогонит 5 acceptance-тестов и покажет HAES/MES метрики
```

### Ручная проверка

```bash
# Создать сессию
curl -X POST http://127.0.0.1:8765/session/start \
  -H "Content-Type: application/json" \
  -d '{"session_id":"test","project_id":"kettu-demo"}'

# Записать событие
curl -X POST http://127.0.0.1:8765/turn/after \
  -H "Content-Type: application/json" \
  -d '{"events":[{"role":"user","type":"message","content":"Привет, Kettu Mem!"}]}'

# Поискать в памяти
curl "http://127.0.0.1:8765/mem0/search?q=Привет&limit=5"

# Метрики Prometheus
curl http://127.0.0.1:8765/metrics
```

---

## 🔌 Интеграция с OpenClaw (плагин)

Плагин лежит в `src/plugin/`. Чтобы подключить:

```bash
# Скопировать плагин в директорию плагинов OpenClaw
cp -r src/plugin/ /home/ngus/.openclaw/workspace/plugins/hermes-memory/

# Убедиться, что plugin/index.js указывает на правильные пути
# Плагин автоматически подхватится при следующем запуске агента

# Включить (если выключен)
export HERMES_MEMORY_ENABLED=1
```

---

## ❗ Частые проблемы и решения

### "Connection refused" на порту 8765

```bash
# Проверить, запущен ли сервер
pgrep -f "uvicorn api.server"

# Если нет — запустить
cd src && python3 -m uvicorn api.server:app --host 127.0.0.1 --port 8765 &

# Проверить порт
ss -tlnp | grep 8765
```

### "No module named 'faiss'"

```bash
pip install faiss-cpu
# или для GPU: pip install faiss-gpu
```

### "No module named 'openai'"

```bash
pip install openai
```

### FAISS index повреждён

```bash
# Удалить и пересоздать (данные в L3/SQLite сохранятся)
rm ~/.openclaw/memory-store/embeddings/faiss.index
# Сервер пересоздаст индекс при следующем запуске
```

### "API key required"

```bash
# Либо установить ключ
export KETTU_MEM_API_KEY="твой-ключ"

# Либо отключить аутентификацию (только для localhost!)
export KETTU_MEM_API_KEY=""
```

---

## 📊 Мониторинг

```bash
# Health
curl http://127.0.0.1:8765/health

# Deep health (все слои)
curl http://127.0.0.1:8765/ready

# Prometheus метрики
curl http://127.0.0.1:8765/metrics

# Статистика
curl http://127.0.0.1:8765/stats
```

---

## 🔄 Обновление с v0.1.0

```bash
# 1. Остановить старый сервер
kill $(pgrep -f "server.py") 2>/dev/null
kill $(pgrep -f "uvicorn api.server") 2>/dev/null

# 2. Обновить код
cd "/путь/к/Kettu Mem"
git pull origin main

# 3. Обновить зависимости
pip install --upgrade fastapi uvicorn pydantic-settings structlog prometheus-client

# 4. Запустить новый сервер
cd src && python3 -m uvicorn api.server:app --host 127.0.0.1 --port 8765 &

# 5. Проверить
python3 scripts/hermes_doctor.py
```

Данные backward-compatible. Никакой миграции не требуется.

---

## 📚 Документация

| Файл | Описание |
|---|---|
| [README.md](README.md) | Обзор проекта |
| [CHANGELOG.md](CHANGELOG.md) | История версий |
| [RELEASE_NOTES.md](RELEASE_NOTES.md) | Детальные release notes v0.2.0 |
| [SKILL.md](SKILL.md) | Скилл для использования в OpenClaw |
| [docs/TECHNICAL_SPEC.md](docs/TECHNICAL_SPEC.md) | Техническая спецификация |
| [docs/BUILD_GUIDE.md](docs/BUILD_GUIDE.md) | Руководство по сборке |
| [docs/ERROR_CATALOG.md](docs/ERROR_CATALOG.md) | Каталог ошибок |
| [docs/EVALUATION_SPEC.md](docs/EVALUATION_SPEC.md) | Спецификация Evaluation Framework |

---

## 🆘 Нужна помощь?

1. `python3 scripts/hermes_doctor.py` — первое, что нужно запустить
2. `curl http://127.0.0.1:8765/health` — проверить сервер
3. Логи: `journalctl -u kettu-mem` (если systemd) или вывод uvicorn в терминале
