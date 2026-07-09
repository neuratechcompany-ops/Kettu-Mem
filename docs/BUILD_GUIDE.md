# Kettu Mem — Build & Deploy Guide

**Версия:** 0.1.0

## Быстрый старт (5 минут)

```bash
# 1. Клонировать/скопировать код
cp -r "Kettu Mem/src" /home/ngus/.openclaw/workspace/spike-memory-manager/

# 2. Создать директорию хранения
mkdir -p ~/.openclaw/memory-store

# 3. Запустить MemoryManager сервер
cd /home/ngus/.openclaw/workspace/spike-memory-manager
nohup python3 server.py --data-dir ~/.openclaw/memory-store --port 8765 > /tmp/mm-server.log 2>&1 &

# 4. Проверить health
sleep 3 && curl http://127.0.0.1:8765/health
# → {"status": "ok"}

# 5. Установить плагин
cp -r "Kettu Mem/src/plugin" /home/ngus/.openclaw/workspace/plugins/hermes-memory/
openclaw plugins install /home/ngus/.openclaw/workspace/plugins/hermes-memory

# 6. Добавить feature flags в systemd
mkdir -p ~/.config/systemd/user/openclaw-gateway.service.d

cat > ~/.config/systemd/user/openclaw-gateway.service.d/hermes-memory.conf << 'EOF'
[Service]
Environment=HERMES_MEMORY_ENABLED=1
EOF

cat > ~/.config/systemd/user/openclaw-gateway.service.d/hermes-cognitive.conf << 'EOF'
[Service]
Environment=HERMES_COGNITIVE_RUNTIME=1
EOF

# 7. Перезапустить Gateway
systemctl --user daemon-reload
systemctl --user restart openclaw-gateway.service

# 8. Проверить
openclaw plugins info hermes-memory
python3 hermes_doctor.py
```

## Полная установка (по шагам)

### Шаг 1: Зависимости Python

```bash
# Проверить наличие
python3 -c "import numpy, faiss, tiktoken, sqlite3; print('OK')"

# Если чего-то нет:
pip3 install --break-system-packages numpy faiss-cpu tiktoken
# sentence-transformers опционально
```

### Шаг 2: Структура директорий

```bash
mkdir -p ~/.openclaw/memory-store/{l3_archive,faiss,cognitive}
```

### Шаг 3: Запуск сервера

```bash
cd /path/to/spike-memory-manager
python3 server.py --data-dir ~/.openclaw/memory-store --port 8765
```

### Шаг 4: Автозапуск сервера (systemd)

```bash
cat > ~/.config/systemd/user/kettu-mem.service << 'EOF'
[Unit]
Description=Kettu Mem Memory API
After=network.target

[Service]
ExecStart=/usr/bin/python3 /home/ngus/.openclaw/workspace/spike-memory-manager/server.py --data-dir /home/ngus/.openclaw/memory-store --port 8765
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=default.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now kettu-mem.service
```

### Шаг 5: Плагин OpenClaw

```bash
# Копировать файлы плагина
cp -r Kettu\ Mem/src/plugin/* ~/.openclaw/workspace/plugins/hermes-memory/

# Установить
openclaw plugins install ~/.openclaw/workspace/plugins/hermes-memory

# Проверить
openclaw plugins list | grep hermes
```

### Шаг 6: Feature flags

Добавить в systemd override файлы (см. Быстрый старт, шаг 6).

### Шаг 7: Верификация

```bash
# Doctor
python3 hermes_doctor.py

# Ручная проверка
curl http://127.0.0.1:8765/health/deep
curl http://127.0.0.1:8765/stats
curl http://127.0.0.1:8765/cognitive/state
```

## Обновление

```bash
# 1. Остановить сервер
systemctl --user stop kettu-mem.service

# 2. Заменить файлы
cp -r Kettu\ Mem/src/* /path/to/spike-memory-manager/

# 3. Переустановить плагин
cp -r Kettu\ Mem/src/plugin/* ~/.openclaw/workspace/plugins/hermes-memory/
openclaw plugins install --force ~/.openclaw/workspace/plugins/hermes-memory

# 4. Запустить
systemctl --user start kettu-mem.service
systemctl --user restart openclaw-gateway.service
```

## Удаление

```bash
# 1. Отключить флаги
rm ~/.config/systemd/user/openclaw-gateway.service.d/hermes-memory.conf
rm ~/.config/systemd/user/openclaw-gateway.service.d/hermes-cognitive.conf

# 2. Остановить сервер
systemctl --user stop kettu-mem.service
systemctl --user disable kettu-mem.service

# 3. Удалить плагин
openclaw plugins uninstall hermes-memory

# 4. Перезапустить Gateway
systemctl --user restart openclaw-gateway.service

# 5. Данные сохраняются в ~/.openclaw/memory-store/ (удалить вручную если нужно)
```

## Диагностика

```bash
# Healthcheck
python3 hermes_doctor.py

# Статистика
python3 -c "import urllib.request,json; print(json.dumps(json.load(urllib.request.urlopen('http://127.0.0.1:8765/stats')), indent=2))"

# Логи сервера
tail -100 /tmp/mm-server.log

# Gateway логи
journalctl --user -u openclaw-gateway.service --since "1 hour ago" | grep hermes

# Размер хранилища
du -sh ~/.openclaw/memory-store/
```
