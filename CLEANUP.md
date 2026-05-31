# Приведение репозиториев в порядок

Два репозитория, по приоритету. Шаги, которые могу сделать только я (ротация
ключа, переписывание истории, push в твой remote), помечены **[ТОЛЬКО ТЫ]** —
их выполняешь на своей машине; готовые файлы для подстановки лежат рядом.

---

## A. kristina_agent_center — сначала безопасность

### A1. [ТОЛЬКО ТЫ] Ротировать ключ OpenClaw — НЕМЕДЛЕННО
Ключ лежал в `action_service.py` в публичном репозитории, значит он
скомпрометирован. Выпусти новый ключ в OpenClaw и отзови старый. Удаление из
кода (ниже) НЕ делает старый ключ безопасным — его уже могли забрать из истории.

### A2. Убрать ключ из кода (готово)
Подставь патченый файл (ключ читается из окружения, значения в коде нет):
```bash
cd kristina_agent_center
cp cleanup/kristina/action_service.py action_service.py
cp cleanup/kristina/.env.example .env.example
# новый ключ — в локальный .env (он в .gitignore, в git не попадёт):
echo "OPENCLAW_API_KEY=<новый-ключ>" > .env
```

### A3. .gitignore + снять venv с трекинга
```bash
cp cleanup/kristina/.gitignore .gitignore
git rm -r --cached --quiet venv
git rm -r --cached --quiet $(git ls-files | grep __pycache__)
git add .gitignore action_service.py .env.example
git commit -m "chore: ключ в env, .gitignore, убрать venv из трекинга"
```
Проверка: `git ls-files | grep -c '^venv/'` → должно быть `0`.

### A4. Починить CI валидации онтологии
```bash
cp cleanup/kristina/validate-ontology.yml .github/workflows/validate-ontology.yml
git add .github/workflows/validate-ontology.yml
git commit -m "ci: реальная валидация онтологии (валит сборку на ошибке)"
```
Прежний воркфлоу проверял несуществующий ключ `entities` и стоял на
`continue-on-error` — то есть никогда не падал. Новый проверяет схему
(тиры, действия, полномочия) и реально валит сборку.

### A5. [ТОЛЬКО ТЫ] Стереть ключ и venv из ИСТОРИИ
Коммиты A2–A3 убирают их из текущего состояния, но они остаются в старых
коммитах. Переписать историю (на полном клоне, не shallow):
```bash
pip install git-filter-repo

# 1) затереть значение ключа во всей истории, не вписывая его руками:
grep -oE 'sk-[A-Za-z0-9]+' <(git show HEAD~5:action_service.py 2>/dev/null) \
  | head -1 | sed 's/$/==>***REMOVED***/; s/^/literal:/' > /tmp/repl.txt
# если строка выше ничего не нашла — впиши вручную: literal:<старый-ключ>==>***REMOVED***
git filter-repo --replace-text /tmp/repl.txt --force

# 2) выкинуть venv и __pycache__ из всей истории:
git filter-repo --invert-paths --path venv/ --path-glob '*/__pycache__/*' --force
```
> filter-repo удаляет remote `origin` для защиты. Верни его и форсни:
```bash
git remote add origin git@github.com:donjonson-hash/kristina_agent_center.git
git push origin --force --all
git push origin --force --tags
```
**После force-push:** все, у кого есть клон, должны склонировать заново
(старая история переписана). Если работаешь один — неактуально.

---

## B. AI-avatar_command — мусор и мёртвый скрипт

### B1. .gitignore + снять node_modules с трекинга (29 624 файла)
```bash
cd AI-avatar_command
cp cleanup/avatar/.gitignore .gitignore
git rm -r --cached --quiet node_modules
git add .gitignore
git commit -m "chore: .gitignore, убрать node_modules из трекинга"
```
Проверка: `git ls-files | grep -c node_modules/` → `0`.

### B2. Удалить мёртвый setup.sh
`setup.sh` описывает несуществующий Node/TS-бэкенд и копирует из отсутствующей
`../syndi-backend/` (с `set -e` падает на первом `cp`). Заменить на честный SETUP.md:
```bash
git rm setup.sh
cp cleanup/avatar/SETUP.md SETUP.md
git add SETUP.md
git commit -m "docs: заменить нерабочий setup.sh на актуальный SETUP.md"
```

### B3. [ТОЛЬКО ТЫ] Выкинуть node_modules из ИСТОРИИ
```bash
pip install git-filter-repo
git filter-repo --invert-paths --path node_modules/ --force
git remote add origin git@github.com:donjonson-hash/AI-avatar_command.git
git push origin --force --all
```

---

## Итоговая проверка (оба репо)
```bash
git ls-files | grep -cE '^(venv|node_modules)/|__pycache__'   # → 0
git log -p | grep -c 'sk-295'                                  # → 0 (ключ стёрт)
```
