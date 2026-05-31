# Офис программистов — локальный запуск с подключением к проекту

Модель «Palantir на ноутбуке»: офис установлен локально и работает с файлами
проекта через терминал. Все изменения идут через шлюз (тир + полномочия +
человек на риске), исполнитель заперт в корне проекта.

## Запуск

```bash
cd office
python -m pytest -q                       # 26 тестов

# 1) Подключение к проекту: корень репозитория и команды
export WORKSPACE_ROOT=~/Projects/syndi-vercel
export TEST_CMD="npm test"
export LINT_CMD="npm run lint"

# 2) LLM (твой AITunnel)
export LLM_BASE_URL=...  LLM_API_KEY=...  LLM_MODEL=...

# 3) Сервисы
uvicorn action_service:app --port 8000    # шлюз + локальный исполнитель
uvicorn office_server:app  --port 8100    # офис (Kristina)

# 4) Командный центр (в app/)
npm run dev                               # Vite на :5173
```

Без `WORKSPACE_ROOT` исполнитель работает как заглушка (ничего не трогает на
диске) — удобно для dry-run.

## Что исполнитель умеет локально

| Действие        | Тир      | Операция                          |
|-----------------|----------|-----------------------------------|
| read_file       | AUTO     | чтение файла (в корне проекта)    |
| search_code     | AUTO     | поиск по тексту                   |
| lint            | AUTO     | `$LINT_CMD`                       |
| run_tests       | LOW      | `$TEST_CMD`                       |
| write_file      | HIGH     | запись файла (в корне проекта)    |
| create_branch   | LOW      | `git checkout -b`                 |
| commit          | HIGH     | `git add -A && git commit`        |

`merge_pr`, `delete_branch`, `force_push`, `deploy`, `rotate_secret` —
HIGH/CRITICAL, локально намеренно не реализованы (исполнитель бросает
NotImplementedError, а не делает опасное молча). Подключать осознанно.

## Безопасность

- Пути заперты в `WORKSPACE_ROOT` — выход наружу запрещён (PermissionError).
- Нет действия «произвольный shell»: каждый Action Type — конкретная команда.
- `write_file`/`commit` — тир HIGH: каждое изменение файлов проходит через
  человека в Approvals (исполняется только после одобрения).
