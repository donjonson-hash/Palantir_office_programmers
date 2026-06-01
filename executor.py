"""
Исполнитель действий офиса. Стоит СТРОГО за шлюзом: вызывается только из
action_service.execute(), после проверки легальности, полномочий и тира.

Модель «Palantir на ноутбуке»: офис установлен локально, исполнитель имеет
прямой доступ к файлам и терминалу — но заперт в корне проекта (WORKSPACE_ROOT)
и работает только с разрешёнными командами. Произвольный shell недоступен:
каждый Action Type маппится на конкретную ограниченную операцию.

Без WORKSPACE_ROOT используется StubExecutor — детерминированная заглушка
(для тестов и dry-run).
"""
from __future__ import annotations

import os
import shlex
import subprocess
from pathlib import Path
from typing import Any, Protocol

CMD_TIMEOUT = int(os.getenv("CMD_TIMEOUT", "120"))
MAX_READ_BYTES = 200_000


class Executor(Protocol):
    def run(self, action: str, target: str, params: dict[str, Any]) -> str: ...


# ─── Заглушка: ничего не трогает на диске ────────────────────────────────────

class StubExecutor:
    def run(self, action: str, target: str, params: dict[str, Any]) -> str:
        import json
        return f"[stub] {action} on {target} :: {json.dumps(params, ensure_ascii=False)}"


# ─── Локальный исполнитель: реальные файлы и терминал, заперт в корне проекта ─

class LocalExecutor:
    def __init__(self, root: str | Path, *,
                 test_cmd: str | None = None, lint_cmd: str | None = None) -> None:
        self.root = Path(root).resolve()
        if not self.root.is_dir():
            raise RuntimeError(f"WORKSPACE_ROOT не существует: {self.root}")
        self.test_cmd = test_cmd or os.getenv("TEST_CMD", "npm test")
        self.lint_cmd = lint_cmd or os.getenv("LINT_CMD", "npm run lint")

    # Запирание в корне проекта: путь наружу — это нарушение, не ошибка ввода.
    def _resolve(self, rel: str) -> Path:
        if not rel:
            raise ValueError("не указан path")
        p = (self.root / rel).resolve()
        if p != self.root and self.root not in p.parents:
            raise PermissionError(f"путь вне проекта запрещён: {rel}")
        return p

    def _sh(self, cmd: str) -> str:
        r = subprocess.run(shlex.split(cmd), cwd=self.root, capture_output=True,
                           text=True, timeout=CMD_TIMEOUT)
        out = (r.stdout + r.stderr).strip()
        return f"exit={r.returncode}\n{out[:MAX_READ_BYTES]}"

    def run(self, action: str, target: str, params: dict[str, Any]) -> str:
        handler = getattr(self, f"_{action}", None)
        if handler is None:
            # Действие легально по онтологии, но локально ещё не реализовано
            # (например CRITICAL: deploy/force_push/rotate_secret). Не делаем
            # ничего опасного молча — честно сообщаем.
            raise NotImplementedError(f"действие '{action}' не реализовано локальным исполнителем")
        return handler(params)

    # — Чтение/поиск (AUTO) —
    def _read_file(self, p: dict) -> str:
        path = self._resolve(p["path"])
        return path.read_text(encoding="utf-8", errors="replace")[:MAX_READ_BYTES]

    def _search_code(self, p: dict) -> str:
        pattern = p.get("pattern") or p.get("query") or ""
        if not pattern:
            raise ValueError("не указан pattern/query для поиска")
        hits: list[str] = []
        for f in self.root.rglob("*"):
            if not f.is_file() or ".git" in f.parts or "node_modules" in f.parts:
                continue
            try:
                for i, line in enumerate(f.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
                    if pattern in line:
                        hits.append(f"{f.relative_to(self.root)}:{i}: {line.strip()[:160]}")
                        if len(hits) >= 200:
                            return "\n".join(hits) + "\n… (обрезано)"
            except OSError:
                continue
        return "\n".join(hits) if hits else "совпадений нет"

    # — Команды проекта (LOW/AUTO) —
    def _lint(self, p: dict) -> str:
        return self._sh(self.lint_cmd)

    def _run_tests(self, p: dict) -> str:
        return self._sh(self.test_cmd)

    # — Запись/git (MEDIUM) —
    def _write_file(self, p: dict) -> str:
        path = self._resolve(p["path"])
        path.parent.mkdir(parents=True, exist_ok=True)
        content = p.get("content", "")
        path.write_text(content, encoding="utf-8")
        return f"записано {len(content)} символов → {p['path']}"

    def _create_branch(self, p: dict) -> str:
        return self._argv("git", "checkout", "-b", p["branch"])

    def _commit(self, p: dict) -> str:
        self._argv("git", "add", "-A")
        return self._argv("git", "commit", "-m", p["message"])

    def _argv(self, *args: str) -> str:
        r = subprocess.run(list(args), cwd=self.root, capture_output=True,
                           text=True, timeout=CMD_TIMEOUT)
        out = (r.stdout + r.stderr).strip()
        return f"exit={r.returncode}\n{out[:MAX_READ_BYTES]}"


# ─── Фабрика: читает env свежо (чтобы тесты могли переключать окружение) ──────

def get_executor() -> Executor:
    root = os.getenv("WORKSPACE_ROOT")
    return LocalExecutor(root) if root else StubExecutor()
