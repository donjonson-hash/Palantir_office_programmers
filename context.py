"""
Context Broker — сборщик общей картины для агента.

Лечит главную болезнь multi-agent систем: туннельное зрение. Перед КАЖДЫМ
шагом агент получает не только свою подзадачу, но и:
  1) весь план со статусами — кто над чем работает, что уже готово;
  2) доску решений (blackboard) — контракты, обязательные для команды;
  3) дерево файлов workspace — что физически уже создано соседями;
  4) последние действия других агентов — что произошло только что.

Бюджет токенов: дерево ≤ MAX_TREE_ENTRIES записей, события ≤ MAX_PEER_EVENTS,
заметки целиком (они короткие и важные).
"""
from __future__ import annotations

import os
from pathlib import Path

import blackboard
import events

MAX_TREE_ENTRIES = int(os.getenv("CTX_MAX_TREE", "200"))
MAX_PEER_EVENTS = int(os.getenv("CTX_MAX_EVENTS", "8"))
SKIP_DIRS = {"node_modules", ".git", ".next", "dist", "build",
             "venv", ".venv", "__pycache__", ".turbo"}


def file_tree(root: str | None = None) -> str:
    """Дерево файлов workspace (только чтение, мимо шлюза — это наблюдение)."""
    root = root or os.getenv("WORKSPACE_ROOT", "")
    if not root or not Path(root).is_dir():
        return "(workspace пуст или не подключён)"
    base = Path(root).resolve()
    lines: list[str] = []

    def _add(line: str) -> bool:
        if len(lines) >= MAX_TREE_ENTRIES:
            lines.append("… (обрезано)")
            return False
        lines.append(line)
        return True

    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        rel = Path(dirpath).relative_to(base)
        depth = 0 if rel == Path(".") else len(rel.parts)
        if depth and not _add("  " * (depth - 1) + f"{rel.parts[-1]}/"):
            return "\n".join(lines)
        for f in sorted(filenames):
            if not _add("  " * depth + f):
                return "\n".join(lines)
    return "\n".join(lines) or "(workspace пуст)"


def _plan_overview(plan_id: str) -> str:
    import planner  # ленивый импорт: planner сам пользуется этим модулем
    try:
        plan = planner.get_plan(plan_id)
    except KeyError:
        return "(план недоступен)"
    rows = [f"ЦЕЛЬ ПРОЕКТА: {plan['goal']}"]
    for st in plan["subtasks"]:
        rows.append(f"  {st['n']}. [{st['status']}] {st['agent']}: {st['title']}"
                    f" — критерий: {st['acceptance'] or '—'}")
    return "\n".join(rows)


def _notes_block() -> str:
    notes = blackboard.list_notes()
    if not notes:
        return "(доска пуста — публикуй контракты через post_note)"
    return "\n".join(f"- [{n['agent']}] {n['text']}" for n in notes)


def _peer_activity(agent_id: str) -> str:
    recent = events.list_events(after=0, limit=100_000)[-60:]
    peer = [e for e in recent
            if e["kind"] == "action_executed" and e["agent"] != agent_id]
    peer = peer[-MAX_PEER_EVENTS:]
    if not peer:
        return "(пока тихо)"
    return "\n".join(
        f"- {e['agent']}: {e['payload'].get('action')}"
        f"({str(e['payload'].get('target', ''))[:60]}) — {e['payload'].get('reason', '')[:100]}"
        for e in peer)


def build(plan_id: str, agent_id: str) -> str:
    """Полная картина проекта для агента — вставляется в промпт каждого шага."""
    return (f"{_plan_overview(plan_id)}\n\n"
            f"ДОСКА РЕШЕНИЙ КОМАНДЫ (обязательные контракты):\n{_notes_block()}\n\n"
            f"ФАЙЛЫ ПРОЕКТА:\n{file_tree()}\n\n"
            f"ПОСЛЕДНИЕ ДЕЙСТВИЯ КОЛЛЕГ:\n{_peer_activity(agent_id)}")
