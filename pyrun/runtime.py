from __future__ import annotations

import ast
import contextlib
import csv
import glob as glob_module
import io
import json
import os
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.parse
import urllib.request
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
            "tools": Tools(self),
            "tmp": TempNamespace(self),
            "http": HttpNamespace(),
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
        write_text_file(self._resolve(path), content)
        return True

    def write_json(self, path: str | os.PathLike[str], value: Any, indent: int = 2) -> bool:
        write_json_file(self._resolve(path), value, indent)
        return True

    def write_json_lines(self, path: str | os.PathLike[str], values: list[Any]) -> bool:
        write_json_lines_file(self._resolve(path), values)
        return True

    def write_jsonl(self, path: str | os.PathLike[str], values: list[Any]) -> bool:
        return self.write_json_lines(path, values)

    def write_csv(self, path: str | os.PathLike[str], rows: list[Any]) -> bool:
        write_delimited_file(self._resolve(path), rows, ",")
        return True

    def write_tsv(self, path: str | os.PathLike[str], rows: list[Any]) -> bool:
        write_delimited_file(self._resolve(path), rows, "\t")
        return True

    def open(self, path: str | os.PathLike[str], format: str | None = None) -> Any:
        return open_data_file(self._resolve(path), format)

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


def write_text_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def write_json_file(path: Path, value: Any, indent: int = 2) -> None:
    write_text_file(path, json.dumps(value, indent=indent) + "\n")


def write_json_lines_file(path: Path, values: list[Any]) -> None:
    lines = (json.dumps(value) for value in values)
    write_text_file(path, "\n".join(lines) + ("\n" if values else ""))


def write_delimited_file(path: Path, rows: list[Any], delimiter: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as handle:
        writer = csv.writer(handle, delimiter=delimiter, lineterminator="\n")
        write_delimited_rows(writer, rows)


def write_delimited_rows(writer: csv.writer, rows: list[Any]) -> None:
    if not rows:
        return
    if all(isinstance(row, dict) for row in rows):
        headers = ordered_union_keys(rows)
        writer.writerow(headers)
        for row in rows:
            writer.writerow([row.get(header, "") for header in headers])
        return
    writer.writerows(rows)


def ordered_union_keys(rows: list[dict[Any, Any]]) -> list[str]:
    headers: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for key in row:
            header = str(key)
            if header not in seen:
                seen.add(header)
                headers.append(header)
    return headers


def open_data_file(path: Path, format: str | None) -> Any:
    normalized = detect_format(path, format)
    if normalized == "json":
        return json.loads(path.read_text())
    if normalized == "jsonl":
        return [json.loads(line) for line in path.read_text().splitlines() if line]
    if normalized == "csv":
        return read_delimited_file(path, ",")
    if normalized == "tsv":
        return read_delimited_file(path, "\t")
    if normalized in {"txt", "text"}:
        return path.read_text()
    if normalized == "toml":
        return read_toml_file(path)
    raise ValueError(f"Unsupported file format: {normalized}")


def detect_format(path: Path, explicit_format: str | None) -> str:
    if explicit_format:
        return explicit_format.lower().lstrip(".")
    suffix = path.suffix.lower().lstrip(".")
    if suffix == "jsonlines":
        return "jsonl"
    return suffix or "text"


def read_delimited_file(path: Path, delimiter: str) -> list[dict[str, str]]:
    with path.open(newline="") as handle:
        return list(csv.DictReader(handle, delimiter=delimiter))


def read_toml_file(path: Path) -> Any:
    try:
        import tomllib
    except ModuleNotFoundError as exc:
        raise ValueError("Unsupported file format: toml") from exc
    with path.open("rb") as handle:
        return tomllib.load(handle)


class Tools:
    def __init__(self, session: Session) -> None:
        self.file = FileTools(session)


class FileTools:
    def __init__(self, session: Session) -> None:
        self._session = session

    def replace(self, path: str | os.PathLike[str], from_or_options: Any, to: str | None = None) -> dict[str, int]:
        target = self._resolve(path)
        options = normalize_replace_options(from_or_options, to)
        original = target.read_text()
        replaced, count = replace_text(original, options)
        target.write_text(replaced)
        return {"replacements": count}

    def _resolve(self, path: str | os.PathLike[str]) -> Path:
        candidate = Path(path).expanduser()
        if not candidate.is_absolute():
            candidate = self._session.cwd / candidate
        return candidate.resolve()


def normalize_replace_options(from_or_options: Any, to: str | None) -> dict[str, Any]:
    if isinstance(from_or_options, dict):
        options = dict(from_or_options)
    else:
        options = {"from": from_or_options, "to": to}
    if options.get("from") is None or options.get("to") is None:
        raise ValueError("replace requires from and to text")
    return options


def replace_text(text: str, options: dict[str, Any]) -> tuple[str, int]:
    needle = str(options["from"])
    replacement = str(options["to"])
    matches = find_match_offsets(text, needle)
    if options.get("all"):
        return text.replace(needle, replacement), len(matches)
    if "occurrence" in options:
        return replace_occurrence(text, needle, replacement, matches, int(options["occurrence"]))
    if len(matches) != 1:
        raise ValueError(f"replace expected exactly one match, found {len(matches)}")
    return text.replace(needle, replacement, 1), 1


def find_match_offsets(text: str, needle: str) -> list[int]:
    if needle == "":
        raise ValueError("replace from text must not be empty")
    offsets: list[int] = []
    start = 0
    while True:
        index = text.find(needle, start)
        if index < 0:
            return offsets
        offsets.append(index)
        start = index + len(needle)


def replace_occurrence(text: str, needle: str, replacement: str, matches: list[int], occurrence: int) -> tuple[str, int]:
    if occurrence < 1 or occurrence > len(matches):
        raise ValueError(f"replace occurrence {occurrence} not found")
    index = matches[occurrence - 1]
    return text[:index] + replacement + text[index + len(needle):], 1


class TempNamespace:
    def __init__(self, session: Session) -> None:
        self._session = session

    def file(self, prefix: str = "tmp", suffix: str = "") -> TmpFile:
        return TmpFile.reserve(prefix, suffix)

    def dir(self, prefix: str = "tmp") -> TmpDir:
        return TmpDir.create(prefix)


@dataclass
class TmpFile:
    path: Path

    @classmethod
    def reserve(cls, prefix: str, suffix: str) -> TmpFile:
        handle = tempfile.NamedTemporaryFile(prefix=prefix, suffix=suffix, delete=True)
        path = Path(handle.name)
        handle.close()
        return cls(path)

    def __str__(self) -> str:
        return str(self.path)

    def __repr__(self) -> str:
        return f"TmpFile({self.path!s})"

    def cleanup(self) -> bool:
        self.path.unlink(missing_ok=True)
        return True

    def write(self, content: str) -> bool:
        write_text_file(self.path, content)
        return True

    def write_json(self, value: Any, indent: int = 2) -> bool:
        write_json_file(self.path, value, indent)
        return True

    def write_json_lines(self, values: list[Any]) -> bool:
        write_json_lines_file(self.path, values)
        return True

    def write_jsonl(self, values: list[Any]) -> bool:
        return self.write_json_lines(values)

    def write_csv(self, rows: list[Any]) -> bool:
        write_delimited_file(self.path, rows, ",")
        return True

    def write_tsv(self, rows: list[Any]) -> bool:
        write_delimited_file(self.path, rows, "\t")
        return True


@dataclass
class TmpDir:
    path: Path

    @classmethod
    def create(cls, prefix: str) -> TmpDir:
        return cls(Path(tempfile.mkdtemp(prefix=prefix)))

    def __str__(self) -> str:
        return str(self.path)

    def __repr__(self) -> str:
        return f"TmpDir({self.path!s})"

    def cleanup(self) -> bool:
        shutil.rmtree(self.path, ignore_errors=True)
        return True


class HttpNamespace:
    def request(self, method: str, url: str, options: dict[str, Any] | None = None) -> HttpRequestBuilder:
        return HttpRequestBuilder(method.upper(), url, options or {})

    def get(self, url: str, options: dict[str, Any] | None = None) -> HttpRequestBuilder:
        return self.request("GET", url, options)

    def post(self, url: str, options: dict[str, Any] | None = None) -> HttpRequestBuilder:
        return self.request("POST", url, options)

    def put(self, url: str, options: dict[str, Any] | None = None) -> HttpRequestBuilder:
        return self.request("PUT", url, options)

    def patch(self, url: str, options: dict[str, Any] | None = None) -> HttpRequestBuilder:
        return self.request("PATCH", url, options)

    def delete(self, url: str, options: dict[str, Any] | None = None) -> HttpRequestBuilder:
        return self.request("DELETE", url, options)

    def head(self, url: str, options: dict[str, Any] | None = None) -> HttpRequestBuilder:
        return self.request("HEAD", url, options)


@dataclass(frozen=True)
class HttpRequestBuilder:
    method: str
    url: str
    options: dict[str, Any]

    def run(self) -> HttpResponse:
        request = build_url_request(self.method, self.url, self.options)
        try:
            with urllib.request.urlopen(request) as response:
                return http_response_from(response.status, response.headers.items(), response.read())
        except urllib.error.HTTPError as error:
            return http_response_from(error.code, error.headers.items(), error.read())

    def text(self) -> str:
        return self.run().text()

    def json(self) -> Any:
        return self.run().json()

    def bytes(self) -> bytes:
        return self.run().body

    def to_file(self, path: str | os.PathLike[str]) -> str:
        target = Path(path).expanduser()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(self.run().body)
        return str(target)


@dataclass(frozen=True)
class HttpResponse:
    status: int
    headers: dict[str, str]
    body: bytes

    def text(self) -> str:
        return self.body.decode()

    def json(self) -> Any:
        return json.loads(self.text())


def build_url_request(method: str, url: str, options: dict[str, Any]) -> urllib.request.Request:
    headers = {str(key): str(value) for key, value in options.get("headers", {}).items()}
    data = encode_request_body(options, headers)
    return urllib.request.Request(url, data=data, headers=headers, method=method)


def encode_request_body(options: dict[str, Any], headers: dict[str, str]) -> bytes | None:
    if "json" in options:
        headers.setdefault("Content-Type", "application/json")
        return json.dumps(options["json"]).encode()
    if "form" in options:
        headers.setdefault("Content-Type", "application/x-www-form-urlencoded")
        return urllib.parse.urlencode(options["form"]).encode()
    if "body" in options:
        body = options["body"]
        return body if isinstance(body, bytes) else str(body).encode()
    return None


def http_response_from(status: int, headers: Any, body: bytes) -> HttpResponse:
    return HttpResponse(status=status, headers={str(key): str(value) for key, value in headers}, body=body)


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
    if isinstance(value, HttpResponse):
        return {
            "status": value.status,
            "headers": to_json_value(value.headers),
            "body": list(value.body),
        }
    if isinstance(value, bytes):
        return list(value)
    if isinstance(value, dict):
        return {str(key): to_json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_json_value(item) for item in value]
    try:
        json.dumps(value)
    except TypeError:
        return repr(value)
    return value
