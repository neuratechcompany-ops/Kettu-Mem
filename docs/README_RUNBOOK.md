# Hermes MemoryManager — Runbook

Версия: **0.2.0** (Production Release) • 2026-07-09

## Быстрый старт

### Включить

```bash
# 1. Убедиться что флаги в systemd
cat /home/ngus/.config/systemd/user/openclaw-gateway.service.d/hermes-memory.conf
# Должно быть: Environment=HERMES_MEMORY_ENABLED=1

cat /home/ngus/.config/systemd/user/openclaw-gateway.service.d/hermes-cognitive.conf
# Должно быть: Environment=HERMES_COGNITIVE_RUNTIME=1

# 2. Запустить MemoryManager сервер
cd /home/ngus/.openclaw/workspace/spike-memory-manager
nohup python3 server.py --data-dir ~/.openclaw/memory-store --port 8765 > /tmp/mm-server.log 2>&1 &

# 3. Проверить health
curl http://127.0.0.1:8765/health
# → {"status": "ok", "session": null}

# 4. Перезапустить Gateway
systemctl --user restart openclaw-gateway.service

# 5. Проверить plugin
openclaw plugins info hermes-memory
# → Status: loaded
```

### Выключить

```bash
# Cognitive Runtime OFF, MemoryManager продолжает работать:
# Установить HERMES_COGNITIVE_RUNTIME=0 в systemd override и перезапустить gateway

# Полное отключение:
# Установить HERMES_MEMORY_ENABLED=0 и перезапустить gateway
```

### Rollback

```bash
# Удалить cognitive флаг
rm /home/ngus/.config/systemd/user/openclaw-gateway.service.d/hermes-cognitive.conf

# Или добавить/изменить:
# Environment=HERMES_COGNITIVE_RUNTIME=0

systemctl --user daemon-reload
systemctl --user restart openclaw-gateway.service
```

## Где лежат данные

```
~/.openclaw/memory-store/
├── l3_archive/           # JSONL — иммутабельный архив всех событий
│   └── session-*.jsonl   # Один файл на сессию
├── metadata.db           # SQLite — метаданные (события, summaries, артефакты)
├── mem0.db               # SQLite — долговременная память (факты, сущности)
├── faiss/                # FAISS — векторный индекс
│   ├── faiss.index
│   └── faiss_id_map.json
└── cognitive/            # Cognitive Runtime — planning state
    └── planning_state.json
```

## Healthcheck

```bash
# Быстрая проверка
curl http://127.0.0.1:8765/health
# → {"status": "ok"}

# Полный healthcheck (все слои)
python3 -m hermes.memory doctor
# или
curl http://127.0.0.1:8765/health/deep
```

## Восстановление после сбоя

### Сервер упал

```bash
# Проверить что порт свободен
fuser 8765/tcp

# Если занят старым процессом:
fuser -k 8765/tcp

# Перезапустить
cd /home/ngus/.openclaw/workspace/spike-memory-manager
nohup python3 server.py --data-dir ~/.openclaw/memory-store --port 8765 > /tmp/mm-server.log 2>&1 &
```

### Gateway перезапущен

```bash
# Gateway сам восстановится через systemd Restart=always
# Проверить статус:
systemctl --user status openclaw-gateway.service

# Если плагин не загрузился:
openclaw plugins install --force /home/ngus/.openclaw/workspace/plugins/hermes-memory
systemctl --user restart openclaw-gateway.service
```

### Session state recovery

```bash
# Cognitive Runtime автоматически восстанавливает PlanningState из planning_state.json
# L3 архив читается из JSONL файлов при start_session()
# Mem0 читается из mem0.db
# FAISS индекс загружается из faiss/

# Ручная проверка:
curl http://127.0.0.1:8765/cognitive/state
curl http://127.0.0.1:8765/stats
```

## Очистка памяти

### Очистить сессию (сохраняя global/user память)

```bash
# Удалить L3 файл сессии
rm ~/.openclaw/memory-store/l3_archive/session-<session-id>.jsonl

# Очистить метаданные сессии из SQLite
sqlite3 ~/.openclaw/memory-store/metadata.db "DELETE FROM events WHERE session_id='<session-id>';"
sqlite3 ~/.openclaw/memory-store/metadata.db "DELETE FROM summaries WHERE session_id='<session-id>';"

# Очистить Mem0 факты сессии
sqlite3 ~/.openclaw/memory-store/mem0.db "DELETE FROM mem0_facts WHERE source_session='<session-id>';"
```

### Очистить проект

```bash
# Удалить L3 файлы проекта
rm ~/.openclaw/memory-store/l3_archive/session-<project-prefix>*.jsonl

# Mem0 факты проекта (если использовался space=project)
sqlite3 ~/.openclaw/memory-store/mem0.db "DELETE FROM mem0_facts WHERE type='project';"
```

### Полный сброс (только session memory, сохраняя global/user)

```bash
# Удалить все сессионные L3 файлы
rm ~/.openclaw/memory-store/l3_archive/session-*.jsonl

# Очистить events/summaries
sqlite3 ~/.openclaw/memory-store/metadata.db "DELETE FROM events; DELETE FROM summaries; VACUUM;"

# Перестроить FAISS
rm ~/.openclaw/memory-store/faiss/faiss.index
rm ~/.openclaw/memory-store/faiss/faiss_id_map.json
```

## Backup

```bash
BACKUP_DIR=~/backups/hermes-$(date +%Y%m%d-%H%M)
mkdir -p $BACKUP_DIR

# SQLite дампы
sqlite3 ~/.openclaw/memory-store/metadata.db ".dump" > $BACKUP_DIR/metadata.sql
sqlite3 ~/.openclaw/memory-store/mem0.db ".dump" > $BACKUP_DIR/mem0.sql

# L3 архив
cp -r ~/.openclaw/memory-store/l3_archive $BACKUP_DIR/

# FAISS индекс
cp -r ~/.openclaw/memory-store/faiss $BACKUP_DIR/

# Cognitive state
cp ~/.openclaw/memory-store/cognitive/planning_state.json $BACKUP_DIR/

echo "Backup: $BACKUP_DIR"
```

## Диагностика

```bash
# Статистика всех слоёв
curl http://127.0.0.1:8765/stats | python3 -m json.tool

# Поиск в памяти
curl "http://127.0.0.1:8765/mem0/search?q=<запрос>"

# Состояние cognitive runtime
curl http://127.0.0.1:8765/cognitive/state | python3 -m json.tool

# Gateway логи
journalctl --user -u openclaw-gateway.service --since "1 hour ago" | grep hermes-memory

# Логи сервера
tail -100 /tmp/mm-server.log
```

## Мониторинг

```bash
# Размер хранилища
du -sh ~/.openclaw/memory-store/

# Количество событий по сессиям
ls ~/.openclaw/memory-store/l3_archive/ | wc -l

# Mem0 факты по типам
sqlite3 ~/.openclaw/memory-store/mem0.db "SELECT type, COUNT(*) FROM mem0_facts GROUP BY type;"
```
