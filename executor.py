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
import shutil
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

    @staticmethod
    def _param(p: dict, *names: str) -> str:
        # Агент может назвать поле по-разному (path/file, branch/name, ...).
        # Принимаем любой из ожидаемых ключей, иначе — внятная ошибка.
        for n in names:
            v = p.get(n)
            if v:
                return str(v)
        raise ValueError(f"не указан параметр (ожидался один из: {', '.join(names)})")

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
        # Некоторые агенты кладут путь/имя в target, а не в params. Даём handler'ам
        # target как ЗАПАСНОЙ источник (явные params всегда в приоритете).
        p = dict(params)
        p.setdefault("_target", target)
        return handler(p)

    # — Чтение/поиск (AUTO) —
    def _read_file(self, p: dict) -> str:
        path = self._resolve(self._param(p, "path", "file", "filename", "_target"))
        return path.read_text(encoding="utf-8", errors="replace")[:MAX_READ_BYTES]

    def _search_code(self, p: dict) -> str:
        pattern = self._param(p, "pattern", "query", "q")
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
        rel = self._param(p, "path", "file", "filename", "_target")
        path = self._resolve(rel)
        path.parent.mkdir(parents=True, exist_ok=True)
        content = p.get("content") or p.get("text") or ""
        path.write_text(content, encoding="utf-8")
        return f"записано {len(content)} символов → {rel}"

    def _create_branch(self, p: dict) -> str:
        return self._argv("git", "checkout", "-b", self._param(p, "branch", "name", "branch_name"))

    def _commit(self, p: dict) -> str:
        self._argv("git", "add", "-A")
        return self._argv("git", "commit", "-m", self._param(p, "message", "msg", "m"))

    # — Pull request через gh CLI (HIGH) —
    def _open_pr(self, p: dict) -> str:
        title = (p.get("title") or p.get("message") or "").strip()
        if not title:
            raise ValueError("не указан title для PR")
        body = p.get("body", "")
        base = p.get("base", "main")
        # текущая ветка
        branch = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=self.root, capture_output=True, text=True, timeout=CMD_TIMEOUT,
        ).stdout.strip()
        # Никогда не публикуем напрямую из защищённых веток.
        if not branch or branch in {base, "main", "master", "HEAD"}:
            raise ValueError(
                f"PR нельзя открыть из ветки '{branch}' — сначала создай отдельную "
                "ветку (create_branch)")
        # Все дешёвые проверки до записи в remote.
        if shutil.which("gh") is None:
            raise RuntimeError("gh CLI не установлен — поставь GitHub CLI и выполни 'gh auth login'")
        push = subprocess.run(["git", "push", "-u", "origin", branch],
                              cwd=self.root, capture_output=True, text=True, timeout=CMD_TIMEOUT)
        if push.returncode != 0:
            raise RuntimeError(f"git push не удался: {(push.stderr or push.stdout).strip()[:400]}")
        pr = subprocess.run(["gh", "pr", "create", "--base", base, "--head", branch,
                             "--title", title, "--body", body],
                            cwd=self.root, capture_output=True, text=True, timeout=CMD_TIMEOUT)
        if pr.returncode != 0:
            raise RuntimeError(f"gh pr create не удался: {(pr.stderr or pr.stdout).strip()[:400]}")
        return f"PR открыт: {pr.stdout.strip()}"

    # — Merge PR через gh CLI (HIGH) —
    def _merge_pr(self, p: dict) -> str:
        pr = self._param(p, "pr", "number", "branch", "head", "_target")
        flag = {"merge":"--merge","squash":"--squash","rebase":"--rebase"}.get(p.get("method","merge"), "--merge")
        if shutil.which("gh") is None:
            raise RuntimeError("gh CLI не установлен")
        r = subprocess.run(["gh","pr","merge",pr,flag,"--delete-branch"],
                           cwd=self.root, capture_output=True, text=True, timeout=CMD_TIMEOUT)
        if r.returncode != 0:
            raise RuntimeError(f"gh pr merge не удался: {(r.stderr or r.stdout).strip()[:400]}")
        return f"PR смёржен: {r.stdout.strip()}"

    def _argv(self, *args: str) -> str:
        r = subprocess.run(list(args), cwd=self.root, capture_output=True,
                           text=True, timeout=CMD_TIMEOUT)
        out = (r.stdout + r.stderr).strip()
        return f"exit={r.returncode}\n{out[:MAX_READ_BYTES]}"


# ─── Фабрика: читает env свежо (чтобы тесты могли переключать окружение) ──────

def get_executor() -> Executor:
    root = os.getenv("WORKSPACE_ROOT")
    return LocalExecutor(root) if root else StubExecutor()
