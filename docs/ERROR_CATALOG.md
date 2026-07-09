# Kettu Mem — Error Catalog & Recovery Procedures

## Принципы обработки ошибок

1. **No crash propagation:** отказ слоя памяти не блокирует агента
2. **Graceful degradation:** при отказе компонента — fallback на альтернативу
3. **Data preservation:** существующие данные никогда не теряются
4. **Predictable behaviour:** каждая ошибка имеет документированное поведение

---

## Каталог ошибок

### E001 — MemoryManager API Unreachable

**Симптом:** `curl http://127.0.0.1:8765/health` → Connection refused  
**Причина:** Сервер упал или не запущен  
**Влияние:** Агент продолжает работу без памяти (graceful degradation)  
**Данные:** Не теряются (L3 на диске)  
**Восстановление:**
```bash
cd /home/ngus/.openclaw/workspace/spike-memory-manager
nohup python3 server.py --data-dir ~/.openclaw/memory-store --port 8765 > /tmp/mm-server.log 2>&1 &
```
**Автоматическое:** systemd Restart=always (если настроен сервис)  
**Уведомление:** В логах Gateway: `[hermes-memory] API unreachable`

---

### E002 — Port Already In Use

**Симптом:** `OSError: [Errno 98] Address already in use`  
**Причина:** Старый процесс сервера не завершён  
**Влияние:** Новый сервер не запускается  
**Данные:** Не затрагиваются  
**Восстановление:**
```bash
fuser -k 8765/tcp
sleep 2
python3 server.py --data-dir ~/.openclaw/memory-store --port 8765 &
```

---

### E003 — No Active Session

**Симптом:** `RuntimeError: No active session. Call start_session() first.`  
**Причина:** MemoryManager не получил session_start перед записью  
**Влияние:** События не записываются (текущий turn)  
**Данные:** Потеря событий текущего turn (L3 не записан)  
**Восстановление:** Плагин автоматически вызывает session_start при следующем turn  
**Предотвращение:** Убедиться что `session_start` хук срабатывает (новые сессии после установки плагина)

---

### E004 — Corrupted JSONL Line

**Симптом:** При чтении L3 архива — `JSONDecodeError`  
**Причина:** Повреждение файла (диск, ручное редактирование)  
**Влияние:** Одно событие не читается, остальные OK  
**Данные:** 1 событие потеряно (одна строка JSONL)  
**Восстановление:**
```bash
# Найти и удалить повреждённую строку
grep -n "CORRUPTED" ~/.openclaw/memory-store/l3_archive/session-*.jsonl
# Или пересоздать файл без повреждённой строки
```
**Автоматическое:** Повреждённая строка пропускается при чтении (остальные события доступны)

---

### E005 — SQLite Corruption

**Симптом:** `sqlite3.DatabaseError: database disk image is malformed`  
**Причина:** Сбой записи, неожиданное отключение  
**Влияние:** SQLite недоступен, поиск через Mem0 и FAISS не работает  
**Данные:** Зависят от checkpoint WAL (0 до N событий)  
**Восстановление:**
```bash
# 1. Создать бэкап
cp ~/.openclaw/memory-store/metadata.db ~/.openclaw/memory-store/metadata.db.bak

# 2. Попытаться восстановить
sqlite3 ~/.openclaw/memory-store/metadata.db "PRAGMA integrity_check;"
sqlite3 ~/.openclaw/memory-store/metadata.db ".recover" > /tmp/recover.sql
sqlite3 ~/.openclaw/memory-store/metadata.db.new < /tmp/recover.sql

# 3. Если не помогло — удалить и создать заново (L3 архив не пострадает)
rm ~/.openclaw/memory-store/metadata.db
```
**Автоматическое:** WAL journal обычно восстанавливает состояние

---

### E006 — Missing FAISS Index

**Симптом:** `FileNotFoundError: faiss.index`  
**Причина:** Индекс удалён или не был создан  
**Влияние:** Семантический поиск недоступен, fallback на другие источники  
**Данные:** 0 (FAISS — производный от L3)  
**Восстановление:** Автоматическое — индекс пересоздаётся при следующих записях  
**Ручное:**
```bash
rm ~/.openclaw/memory-store/faiss/faiss_id_map.json
# Перезапустить сервер — FAISS перестроится
```

---

### E007 — Mem0 Read Failure

**Симптом:** Ошибка при чтении mem0.db  
**Причина:** Повреждение БД или конфликт блокировок  
**Влияние:** Mem0 факты недоступны, контекст собирается без них  
**Данные:** 0 (Mem0 можно восстановить из L3 повторным extract_facts)  
**Восстановление:**
```bash
sqlite3 ~/.openclaw/memory-store/mem0.db "PRAGMA integrity_check;"
# Если ok — проблема в блокировках, перезапустить сервер
# Если fail — удалить и пересоздать (факты переизвлекутся)
```

---

### E008 — Disk Full

**Симптом:** `OSError: [Errno 28] No space left on device`  
**Причина:** Диск заполнен  
**Влияние:** Новые события не записываются, существующие данные сохранны  
**Данные:** Новые события теряются до освобождения места  
**Восстановление:**
```bash
# Проверить свободное место
df -h ~/.openclaw/memory-store/

# Освободить — удалить старые сессии
rm ~/.openclaw/memory-store/l3_archive/session-old-*.jsonl

# Очистить SQLite (VACUUM)
sqlite3 ~/.openclaw/memory-store/metadata.db "VACUUM;"
```
**Предотвращение:** Мониторинг свободного места (`hermes_doctor.py` проверяет)

---

### E009 — Concurrent Write Conflict

**Симптом:** `sqlite3.OperationalError: database is locked`  
**Причина:** Одновременная запись из нескольких процессов  
**Влияние:** Одна из записей может быть отложена (WAL mode)  
**Данные:** 0 (WAL разрешает конфликты)  
**Восстановление:** Автоматическое — SQLite WAL + busy timeout  
**Предотвращение:** Использовать один процесс сервера (архитектура гарантирует)

---

### E010 — Plugin Hook Not Firing

**Симптом:** События не записываются в Kettu Mem при работающем сервере  
**Причина:** Сессия создана до установки плагина  
**Влияние:** Текущая сессия без памяти, новые сессии будут работать  
**Данные:** События текущей сессии не записаны  
**Восстановление:**
```bash
# Создать новую сессию (/new в чате)
# Проверить что плагин загружен
openclaw plugins info hermes-memory
# Убедиться что флаги активны
cat /proc/$(pgrep -f "openclaw/dist/index.js gateway" | head -1)/environ | tr '\0' '\n' | grep HERMES
```

---

## Процедура полного восстановления

Если всё сломалось одновременно:

```bash
# 0. Сохранить что можно
python3 hermes_backup.py --output /tmp/emergency-backup

# 1. Остановить всё
systemctl --user stop kettu-mem.service 2>/dev/null
fuser -k 8765/tcp 2>/dev/null

# 2. Восстановить SQLite из бэкапа
cp /tmp/emergency-backup/metadata.db.backup ~/.openclaw/memory-store/metadata.db
cp /tmp/emergency-backup/mem0.db.backup ~/.openclaw/memory-store/mem0.db

# 3. Перезапустить сервер
cd /home/ngus/.openclaw/workspace/spike-memory-manager
python3 server.py --data-dir ~/.openclaw/memory-store --port 8765 &

# 4. Проверить
python3 hermes_doctor.py

# 5. Если doctor FAIL — чистая установка:
rm -rf ~/.openclaw/memory-store/*
mkdir -p ~/.openclaw/memory-store/{l3_archive,faiss,cognitive}
# Перезапустить сервер
```
