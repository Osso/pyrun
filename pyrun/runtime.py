from __future__ import annotations

import ast
import contextlib
import glob as glob_module
import io
import json
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class AttrDict(dict):
    """Dictionary with attribute access for ctx."""

    def __getattr__(self, name: str) -> Any:
        try:
            return self[name]
        except KeyError as exc:
            raise AttributeError(name) from exc

    def __setattr__(self, name: str, value: Any) -> None:
        self[name] = value

    def __delattr__(self, name: str) -> None:
        try:
            del self[name]
        except KeyError as exc:
            raise AttributeError(name) from exc


@dataclass
class CommandResult:
    stdout: str
    stderr: str
    exit_code: int

    def text(self) -> str:
        return self.stdout

    def lines(self) -> list[str]:
        return self.stdout.splitlines()

    def json(self) -> Any:
        return json.loads(self.stdout)


@dataclass
class Session:
    cwd: Path = field(default_factory=lambda: Path.cwd())
    ctx: AttrDict = field(default_factory=AttrDict)

    def build_globals(self) -> dict[str, Any]:
        host = Host(self)
        fs = FileSystem(self)
        cli = CommandNamespace(self, immediate=False)
        run = CommandNamespace(self, immediate=True)
        return {
            "ctx": self.ctx,
            "host": host,
            "fs": fs,
            "cli": cli,
            "run": run,
        }


class Host:
    def __init__(self, session: Session) -> None:
        self._session = session

    def cwd(self) -> str:
        return str(self._session.cwd)

    def cd(self, path: str | os.PathLike[str]) -> str:
        resolved = self._resolve(path)
        if not resolved.is_dir():
            raise NotADirectoryError(str(resolved))
        self._session.cwd = resolved
        return str(resolved)

    def _resolve(self, path: str | os.PathLike[str]) -> Path:
        candidate = Path(path).expanduser()
        if not candidate.is_absolute():
            candidate = self._session.cwd / candidate
        return candidate.resolve()


class FileSystem:
    def __init__(self, session: Session) -> None:
        self._session = session

    def read(self, path: str | os.PathLike[str]) -> str:
        return self._resolve(path).read_text()

    def write(self, path: str | os.PathLike[str], content: str) -> bool:
        target = self._resolve(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
        return True

    def exists(self, path: str | os.PathLike[str]) -> bool:
        return self._resolve(path).exists()

    def remove(self, path: str | os.PathLike[str]) -> bool:
        target = self._resolve(path)
        target.unlink()
        return True

    def glob(self, pattern: str) -> list[str]:
        matches = glob_module.glob(pattern, root_dir=self._session.cwd)
        return sorted(matches)

    def _resolve(self, path: str | os.PathLike[str]) -> Path:
        candidate = Path(path).expanduser()
        if not candidate.is_absolute():
            candidate = self._session.cwd / candidate
        return candidate.resolve()


@dataclass(frozen=True)
class CommandBuilder:
    session: Session
    program: str
    args: tuple[str, ...] = ()
    cwd: Path | None = None
    stdin: str | None = None

    def __call__(self, *args: object) -> CommandBuilder:
        return CommandBuilder(
            session=self.session,
            program=self.program,
            args=self.args + tuple(str(arg) for arg in args),
            cwd=self.cwd,
            stdin=self.stdin,
        )

    def in_(self, cwd: str | os.PathLike[str]) -> CommandBuilder:
        return CommandBuilder(
            session=self.session,
            program=self.program,
            args=self.args,
            cwd=self._resolve(cwd),
            stdin=self.stdin,
        )

    def stdin_text(self, text: str) -> CommandBuilder:
        return CommandBuilder(
            session=self.session,
            program=self.program,
            args=self.args,
            cwd=self.cwd,
            stdin=text,
        )

    def run(self) -> CommandResult:
        completed = subprocess.run(
            [self.program, *self.args],
            input=self.stdin,
            text=True,
            capture_output=True,
            cwd=self.cwd or self.session.cwd,
            shell=False,
            check=False,
        )
        return CommandResult(
            stdout=completed.stdout,
            stderr=completed.stderr,
            exit_code=completed.returncode,
        )

    def text(self) -> str:
        return self.run().text()

    def lines(self) -> list[str]:
        return self.run().lines()

    def json(self) -> Any:
        return self.run().json()

    def _resolve(self, path: str | os.PathLike[str]) -> Path:
        candidate = Path(path).expanduser()
        if not candidate.is_absolute():
            candidate = self.session.cwd / candidate
        return candidate.resolve()


class CommandNamespace:
    def __init__(self, session: Session, immediate: bool) -> None:
        self._session = session
        self._immediate = immediate

    def __getattr__(self, program: str) -> Any:
        command = CommandBuilder(self._session, program.replace("_", "-"))
        if self._immediate:
            return ImmediateCommand(command)
        return command


class ImmediateCommand:
    def __init__(self, command: CommandBuilder) -> None:
        self._command = command

    def __call__(self, *args: object) -> CommandResult:
        return self._command(*args).run()


class SessionStore:
    def __init__(self) -> None:
        self._sessions: dict[str, Session] = {}

    def evaluate(self, code: str, session_id: str = "default") -> dict[str, Any]:
        session = self._session(session_id)
        console = io.StringIO()
        try:
            with contextlib.redirect_stdout(console):
                value = evaluate_python(code, session.build_globals())
        except Exception as exc:  # noqa: BLE001 - error is returned to JSONL caller.
            return {
                "type": "error",
                "executed": code,
                "error": str(exc),
            }
        return {
            "type": "completed",
            "executed": code,
            "console": console_lines(console),
            "value": to_json_value(value),
        }

    def _session(self, session_id: str) -> Session:
        if not session_id:
            raise ValueError("session_id must not be empty")
        if session_id not in self._sessions:
            self._sessions[session_id] = Session()
        return self._sessions[session_id]


def evaluate_python(code: str, globals_map: dict[str, Any]) -> Any:
    try:
        compiled = compile(code, "<pyrun>", "eval")
    except SyntaxError:
        return evaluate_statements(code, globals_map)
    return eval(compiled, globals_map)  # noqa: S307 - prototype intentionally evaluates caller code.


def evaluate_statements(code: str, globals_map: dict[str, Any]) -> Any:
    module = ast.parse(code, mode="exec")
    if module.body and isinstance(module.body[-1], ast.Expr):
        return evaluate_exec_with_trailing_expr(module, globals_map)
    compiled = compile(module, "<pyrun>", "exec")
    exec(compiled, globals_map)  # noqa: S102 - prototype intentionally executes caller code.
    return None


def evaluate_exec_with_trailing_expr(module: ast.Module, globals_map: dict[str, Any]) -> Any:
    prefix = ast.Module(body=module.body[:-1], type_ignores=module.type_ignores)
    ast.fix_missing_locations(prefix)
    if prefix.body:
        exec(compile(prefix, "<pyrun>", "exec"), globals_map)  # noqa: S102
    expression = ast.Expression(module.body[-1].value)
    ast.fix_missing_locations(expression)
    return eval(compile(expression, "<pyrun>", "eval"), globals_map)  # noqa: S307


def console_lines(console: io.StringIO) -> list[str]:
    text = console.getvalue()
    return text.splitlines()


def to_json_value(value: Any) -> Any:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, CommandResult):
        return {
            "stdout": value.stdout,
            "stderr": value.stderr,
            "exit_code": value.exit_code,
        }
    if isinstance(value, dict):
        return {str(key): to_json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_json_value(item) for item in value]
    try:
        json.dumps(value)
    except TypeError:
        return repr(value)
    return value
