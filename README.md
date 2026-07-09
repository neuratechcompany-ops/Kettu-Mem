# 🦊 Kettu Mem v0.1.0

**Когнитивный слой памяти для OpenClaw-агентов**

---

## Что внутри

| Компонент | Статус |
|---|---|
| MemoryManager (6 слоёв) | ✅ ACCEPTED |
| Cognitive Runtime | ✅ ACCEPTED |
| OpenClaw Plugin (5 хуков) | ✅ ACCEPTED |
| Hardening (healthcheck, backup, doctor) | ✅ COMPLETE |
| Fault Tolerance (10 сценариев) | ✅ PASSED (10/10) |
| Acceptance (500+ шагов) | ✅ PASSED |

## Быстрый старт

```bash
# Doctor — проверить всё
python3 hermes_doctor.py

# Сервер — если не запущен
cd /home/ngus/.openclaw/workspace/spike-memory-manager
python3 server.py --data-dir ~/.openclaw/memory-store --port 8765 &

# Healthcheck
curl http://127.0.0.1:8765/health
```

## Документация

- `docs/TECHNICAL_SPEC.md` — полная техническая спецификация
- `docs/BUILD_GUIDE.md` — руководство по сборке и развёртыванию
- `docs/ERROR_CATALOG.md` — каталог ошибок и восстановление
- `docs/README_RUNBOOK.md` — эксплуатационная документация
- `docs/PLUGIN_BLUEPRINT.md` — схема интеграции с OpenClaw
- `SKILL.md` — скилл для использования в OpenClaw

## Структура

```
Kettu Mem/
├── README.md
├── SKILL.md                   # Скилл для OpenClaw
├── VERSION.json
├── src/
│   ├── memory_manager.py      # Оркестратор
│   ├── server.py              # HTTP API
│   ├── layers/                # 6 слоёв
│   ├── plugin/                # OpenClaw plugin
│   └── tests/                 # Тесты
├── scripts/
│   ├── hermes_doctor.py       # Диагностика
│   ├── hermes_backup.py       # Бэкап
│   ├── hermes_soak.py         # Нагрузочный тест
│   └── hermes_fault_test.py   # Fault tolerance
├── docs/                      # Документация
└── backup/                    # Бэкапы
```

## Ключевые метрики

- **Экономия токенов:** >90% (359 vs 75 000 на 500 шагах)
- **Latency:** 8.3ms avg, p99=20.5ms
- **Prompt stability:** 1.1x growth (не линейный)
- **Fault tolerance:** 10/10 сценариев
- **Storage:** 4.7 MB на 500 событий
- **Recovery:** 9/10 автоматическое, 1/10 ручное

## Владелец

Aurum Kettunen (Настя Гусева)  
Разработано: Hermes / Жорик  
Дата: 2026-07-09
