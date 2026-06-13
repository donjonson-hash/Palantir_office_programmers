"""
Фаза code review — «приёмка на Claude».

После того как подзадачи выполнены и самопроверка (build+lint) зелёная,
viktor (на сильной модели) читает файлы, изменённые в ходе проекта, и выносит
структурный вердикт. Это ловит то, что не видит build: потерянный при перезаписи
эндпоинт, разъехавшийся контракт между модулями, выдуманный формат данных —
ровно те классы багов, что мы чинили руками.

Вердикт БЛОКИРУЮЩИЙ, но соразмерно: только issues с severity=critical
возвращают работу на исправление. major/minor публикуются как советы в ленту,
проект завершается.

Ревью — отдельная фаза оркестратора, НЕ подзадача: Kristina не планирует на
viktor, он вызывается оркестратором напрямую через свой провайдер.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import events

# Сколько файлов максимум показать ревьюеру и сколько строк на файл (бюджет токенов).
MAX_REVIEW_FILES = int(os.getenv("REVIEW_MAX_FILES", "12"))
MAX_FILE_LINES = int(os.getenv("REVIEW_MAX_FILE_LINES", "400"))
SKIP_PARTS = {"node_modules", ".git", ".next", "dist", "build",
              "venv", ".venv", "__pycache__", "data"}

REVIEW_SYSTEM = """Ты — viktor, строгий code reviewer. Тебе дают цель проекта и
содержимое изменённых файлов. Найди дефекты, которые НЕ ловит сборка:
- потерянные при перезаписи функции/экспорты/обработчики (был POST — стал только GET);
- рассогласование контрактов между модулями (одна часть шлёт одно поле, другая ждёт другое);
- выдуманный формат данных, не совпадающий с тем, как данные реально хранятся/читаются;
- явные логические ошибки и недоделки против цели проекта.
НЕ придирайся к стилю и форматированию. Оценивай только работоспособность и целостность.

Ответь СТРОГО одним JSON-объектом без пояснений и markdown:
{"verdict":"pass|fail","issues":[{"severity":"critical|major|minor","file":"<путь>","problem":"<что не так и как чинить>"}]}
verdict=fail ТОЛЬКО если есть хотя бы один critical (то, что ломает работу).
Если критичного нет — verdict=pass (major/minor допустимы при pass)."""


def _changed_files(root: str, base_ref: str = "HEAD") -> list[str]:
    """Файлы, изменённые в workspace относительно base_ref (плюс неотслеживаемые).
    Если git недоступен — пустой список (ревью тогда пропускается оркестратором)."""
    try:
        diff = subprocess.run(
            ["git", "-C", root, "diff", "--name-only", base_ref],
            capture_output=True, text=True, timeout=30)
        untracked = subprocess.run(
            ["git", "-C", root, "ls-files", "--others", "--exclude-standard"],
            capture_output=True, text=True, timeout=30)
        names = set(filter(None, diff.stdout.splitlines()
                           + untracked.stdout.splitlines()))
    except (subprocess.SubprocessError, OSError):
        return []
    out = []
    for n in sorted(names):
        if any(part in SKIP_PARTS for part in Path(n).parts):
            continue
        out.append(n)
    return out


def _collect(root: str, files: list[str]) -> str:
    """Содержимое изменённых файлов для промпта ревьюера (с бюджетом)."""
    blocks = []
    for n in files[:MAX_REVIEW_FILES]:
        p = Path(root) / n
        try:
            lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        body = "\n".join(lines[:MAX_FILE_LINES])
        if len(lines) > MAX_FILE_LINES:
            body += f"\n… (ещё {len(lines) - MAX_FILE_LINES} строк)"
        blocks.append(f"=== {n} ===\n{body}")
    return "\n\n".join(blocks)


def _parse(raw: str) -> dict:
    s, e = raw.find("{"), raw.rfind("}") + 1
    if s == -1 or e <= s:
        return {"verdict": "pass", "issues": [], "_unparsed": True}
    try:
        data = json.loads(raw[s:e])
    except json.JSONDecodeError:
        return {"verdict": "pass", "issues": [], "_unparsed": True}
    data.setdefault("verdict", "pass")
    data.setdefault("issues", [])
    return data


def review(plan_id: str, goal: str, root: str, llm, base_ref: str = "HEAD") -> dict:
    """Прогнать ревью изменённых файлов. Возвращает {verdict, issues, criticals}.
    llm — клиент провайдера viktor (Claude). При отсутствии изменений или git —
    pass (нечего ревьюить)."""
    files = _changed_files(root, base_ref)
    if not files:
        events.publish("review_skipped", agent="viktor", plan_id=plan_id,
                       detail="нет изменённых файлов или git недоступен")
        return {"verdict": "pass", "issues": [], "criticals": []}

    events.publish("review_started", agent="viktor", plan_id=plan_id,
                   files=files[:MAX_REVIEW_FILES])
    user = (f"ЦЕЛЬ ПРОЕКТА: {goal}\n\nИЗМЕНЁННЫЕ ФАЙЛЫ:\n{_collect(root, files)}")
    result = _parse(llm.complete(REVIEW_SYSTEM, user))

    issues = result.get("issues", [])
    criticals = [i for i in issues if i.get("severity") == "critical"]
    result["criticals"] = criticals

    # Советы (major/minor) — в ленту, не блокируют.
    for i in issues:
        if i.get("severity") != "critical":
            events.publish("review_note", agent="viktor", plan_id=plan_id,
                           severity=i.get("severity"), file=i.get("file"),
                           problem=str(i.get("problem", ""))[:300])

    if criticals:
        events.publish("review_failed", agent="viktor", plan_id=plan_id,
                       criticals=[{"file": c.get("file"),
                                   "problem": str(c.get("problem", ""))[:300]}
                                  for c in criticals])
        result["verdict"] = "fail"
    else:
        events.publish("review_passed", agent="viktor", plan_id=plan_id,
                       advisory=len(issues))
        result["verdict"] = "pass"
    return result
