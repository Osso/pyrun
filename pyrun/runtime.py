from __future__ import annotations

import ast
import base64
import builtins
import contextlib
import csv
import fnmatch
import glob as glob_module
import io
import itertools
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from collections.abc import Callable
from typing import Any


class TextNamespace:
    def lines(
        self, value: Any, start: int | None = None, end: int | None = None
    ) -> list[str]:
        lines = str(value).splitlines()
        if start is None and end is None:
            return lines
        first = 1 if start is None else start
        last = first if end is None else end
        return lines[max(first - 1, 0) : last]

    def range(self, value: Any, start: int, end: int | None = None) -> list[str]:
        return self.lines(value, start, end)

    def line_count(self, value: Any) -> int:
        return len(str(value).splitlines())

    def word_count(self, value: Any) -> int:
        return len(str(value).split())

    def head(self, value: Any, count: int = 10) -> list[str]:
        return self.lines(value)[:count]

    def tail(self, value: Any, count: int = 10) -> list[str]:
        return self.lines(value)[-count:]

    def split_row(self, value: Any, separator: str = "\n") -> list[str]:
        return str(value).split(separator)

    def split_words(self, value: Any) -> list[str]:
        return str(value).split()

    def trim(self, value: Any) -> str:
        return str(value).strip()

    def trimmed(self, value: Any) -> str:
        return self.trim(value)

    def replace_text(self, value: Any, old: Any, new: Any) -> str:
        return str(value).replace(str(old), str(new))

    def json(self, value: Any) -> Any:
        return json.loads(str(value))

    def json_lines(self, value: Any) -> list[Any]:
        return [json.loads(line) for line in str(value).splitlines() if line]

    def jsonl(self, value: Any) -> list[Any]:
        return self.json_lines(value)

    def lower(self, value: Any) -> str:
        return str(value).lower()

    def upper(self, value: Any) -> str:
        return str(value).upper()

    def chars(self, value: Any) -> list[str]:
        return list(str(value))

    def bytes_count(self, value: Any) -> int:
        return len(str(value).encode())

    def byte_count(self, value: Any) -> int:
        return self.bytes_count(value)

    def byte_array(self, value: Any) -> list[int]:
        return list(str(value).encode())

    def csv(self, value: Any) -> Any:
        return parse_or_serialize_delimited(value, ",")

    def tsv(self, value: Any) -> Any:
        return parse_or_serialize_delimited(value, "\t")


class SeqNamespace:
    def containing(self, values: list[Any], needle: Any) -> list[Any]:
        return [value for value in values if str(needle) in str(value)]

    def not_containing(self, values: list[Any], needle: Any) -> list[Any]:
        return [value for value in values if str(needle) not in str(value)]

    def starts_with(self, values: list[Any], prefix: Any) -> list[Any]:
        return [value for value in values if str(value).startswith(str(prefix))]

    def ends_with(self, values: list[Any], suffix: Any) -> list[Any]:
        return [value for value in values if str(value).endswith(str(suffix))]

    def matching(self, values: list[Any], pattern: str) -> list[Any]:
        compiled = re.compile(pattern)
        return [value for value in values if compiled.search(str(value))]

    def not_matching(self, values: list[Any], pattern: str) -> list[Any]:
        compiled = re.compile(pattern)
        return [value for value in values if not compiled.search(str(value))]

    def glob(self, values: list[Any], pattern: str) -> list[Any]:
        return [value for value in values if fnmatch.fnmatch(str(value), pattern)]

    def not_glob(self, values: list[Any], pattern: str) -> list[Any]:
        return [value for value in values if not fnmatch.fnmatch(str(value), pattern)]

    def first(self, values: list[Any]) -> Any:
        return values[0] if values else None

    def last(self, values: list[Any]) -> Any:
        return values[-1] if values else None

    def take(self, values: list[Any], count: int) -> list[Any]:
        return values[:count]

    def head(self, values: list[Any], count: int = 10) -> list[Any]:
        return self.take(values, count)

    def tail(self, values: list[Any], count: int = 10) -> list[Any]:
        if count <= 0:
            return []
        return values[-count:]

    def join_text(self, values: list[Any], separator: str = "") -> str:
        return separator.join(str(value) for value in values)

    def unique(self, values: list[Any]) -> list[Any]:
        unique_values: list[Any] = []
        for value in values:
            if value not in unique_values:
                unique_values.append(value)
        return unique_values

    def compact(self, values: list[Any]) -> list[Any]:
        return [value for value in values if value]

    def default(self, values: list[Any], value: Any) -> list[Any]:
        return [value if item is None or item == "" else item for item in values]

    def wrap(self, values: list[Any], name: str) -> list[dict[str, Any]]:
        return [{name: value} for value in values]

    def enumerate(self, values: list[Any]) -> list[dict[str, Any]]:
        return [
            {"index": index, "item": value}
            for index, value in builtins.enumerate(values)
        ]

    def is_empty(self, values: list[Any]) -> bool:
        return len(values) == 0

    def is_not_empty(self, values: list[Any]) -> bool:
        return not self.is_empty(values)

    def any(self, values: list[Any], predicate_or_value: Any = None) -> bool:
        if predicate_or_value is None:
            return builtins.any(values)
        if callable(predicate_or_value):
            return builtins.any(predicate_or_value(value) for value in values)
        return builtins.any(value == predicate_or_value for value in values)

    def all(self, values: list[Any], predicate_or_value: Any = None) -> bool:
        if predicate_or_value is None:
            return builtins.all(values)
        if callable(predicate_or_value):
            return builtins.all(predicate_or_value(value) for value in values)
        return builtins.all(value == predicate_or_value for value in values)

    def sum(self, values: list[Any]) -> Any:
        return builtins.sum(self._numeric_values(values))

    def avg(self, values: list[Any]) -> Any:
        numeric = self._numeric_values(values)
        return builtins.sum(numeric) / len(numeric) if numeric else None

    def min(self, values: list[Any]) -> Any:
        numeric = self._numeric_values(values)
        return builtins.min(numeric) if numeric else None

    def max(self, values: list[Any]) -> Any:
        numeric = self._numeric_values(values)
        return builtins.max(numeric) if numeric else None

    def round(self, values: list[Any], digits: int = 0) -> list[Any]:
        return [self._round_value(value, digits) for value in values]

    def _round_value(self, value: Any, digits: int) -> int | float | None:
        numeric = self._coerce_number(value)
        if numeric is None:
            return None
        return builtins.round(numeric, digits)

    def _numeric_values(self, values: list[Any]) -> list[int | float]:
        return [
            numeric
            for value in values
            if (numeric := self._coerce_number(value)) is not None
        ]

    def _coerce_number(self, value: Any) -> int | float | None:
        if isinstance(value, bool) or value is None:
            return None
        if isinstance(value, int | float):
            return value
        if isinstance(value, str):
            try:
                number = float(value)
            except ValueError:
                return None
            return int(number) if number.is_integer() else number
        return None

    def lengths(self, values: list[Any]) -> list[int]:
        return [len(value) for value in values]

    def lower(self, values: list[Any]) -> list[str]:
        return [str(value).lower() for value in values]

    def upper(self, values: list[Any]) -> list[str]:
        return [str(value).upper() for value in values]

    def sorted(self, values: list[Any]) -> list[Any]:
        return builtins.sorted(values)

    def reversed(self, values: list[Any]) -> list[Any]:
        return list(builtins.reversed(values))

    def get(self, values: list[Any], path: str) -> list[Any]:
        return [get_path(value, path) for value in values]

    def pluck(self, values: list[Any], path: str) -> list[Any]:
        return self.get(values, path)

    def values_of(self, values: list[Any], path: str) -> list[Any]:
        return self.get(values, path)

    def select(self, values: list[Any], *fields: str) -> list[dict[str, Any]]:
        return [select_fields(value, fields) for value in values]

    def reject(self, values: list[Any], *fields: str) -> list[dict[str, Any]]:
        return [reject_fields(value, fields) for value in values]

    def where(self, values: list[Any], predicate_or_dict: Any) -> list[Any]:
        if callable(predicate_or_dict):
            return [value for value in values if predicate_or_dict(value)]
        return [value for value in values if matches_filter(value, predicate_or_dict)]

    def to_csv(self, rows: list[Any]) -> str:
        return serialize_delimited_rows(rows, ",")

    def to_tsv(self, rows: list[Any]) -> str:
        return serialize_delimited_rows(rows, "\t")

    def to_json_lines(self, rows: list[Any]) -> str:
        return "\n".join(json.dumps(row) for row in rows) + ("\n" if rows else "")


class ObjNamespace:
    def get(self, value: dict[Any, Any], path: str) -> Any:
        return get_path(value, path)

    def select(self, value: dict[Any, Any], *fields: str) -> dict[str, Any]:
        return select_fields(value, fields)

    def reject(self, value: dict[Any, Any], *fields: str) -> dict[str, Any]:
        return reject_fields(value, fields)

    def rename(self, value: dict[Any, Any], mapping: dict[str, str]) -> dict[str, Any]:
        return {mapping.get(str(key), str(key)): item for key, item in value.items()}

    def insert(self, value: dict[Any, Any], key: str, item: Any) -> dict[str, Any]:
        return {**value, key: item}

    def update(
        self, value: dict[Any, Any], key: str, value_or_fn: Any
    ) -> dict[str, Any]:
        item = value_or_fn(value.get(key)) if callable(value_or_fn) else value_or_fn
        return {**value, key: item}

    def merge(self, value: dict[Any, Any], other: dict[Any, Any]) -> dict[str, Any]:
        return {**value, **other}

    def columns(self, value: dict[Any, Any]) -> list[str]:
        return [str(key) for key in value.keys()]

    def values(self, value: dict[Any, Any]) -> list[Any]:
        return list(value.values())

    def entries(self, value: dict[Any, Any]) -> list[list[Any]]:
        return [[key, item] for key, item in value.items()]

    def items(self, value: dict[Any, Any]) -> list[list[Any]]:
        return self.entries(value)


@dataclass(frozen=True)
class HelperValue:
    value: Any

    def __getattr__(self, name: str) -> Any:
        namespace = helper_namespace_for(self.value)
        method = getattr(namespace, name)

        def call(*args: Any, **kwargs: Any) -> Any:
            result = method(self.value, *args, **kwargs)
            return wrap_helper_result(result)

        return call


def helper_namespace_for(value: Any) -> Any:
    if isinstance(value, str):
        return TextNamespace()
    if isinstance(value, dict):
        return ObjNamespace()
    if isinstance(value, (list, tuple)):
        return SeqNamespace()
    raise TypeError(f"no helper wrapper for {type(value).__name__}")


def wrap_helper_result(value: Any) -> Any:
    if isinstance(value, (str, list, tuple, dict)):
        return HelperValue(value)
    return value


def hr(value: Any) -> HelperValue:
    helper_namespace_for(value)
    return HelperValue(value)


def get_path(value: Any, path: str) -> Any:
    current = value
    for part in path.split("."):
        current = get_path_part(current, part)
    return current


def get_path_part(value: Any, part: str) -> Any:
    if isinstance(value, dict):
        return value.get(part)
    if isinstance(value, (list, tuple)) and part.isdigit():
        index = int(part)
        return value[index] if index < len(value) else None
    return getattr(value, part, None)


def select_fields(value: Any, fields: tuple[str, ...]) -> dict[str, Any]:
    return {field: get_path(value, field) for field in fields}


def reject_fields(value: dict[Any, Any], fields: tuple[str, ...]) -> dict[str, Any]:
    rejected = set(fields)
    return {str(key): item for key, item in value.items() if str(key) not in rejected}


def matches_filter(value: Any, expected: dict[str, Any]) -> bool:
    return builtins.all(
        get_path(value, path) == item for path, item in expected.items()
    )


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
    upstream_results: tuple[CommandResult, ...] = ()

    def text(self) -> str:
        return self.stdout

    def lines(self) -> list[str]:
        return self.stdout.splitlines()

    def json(self) -> Any:
        return json.loads(self.stdout)


class ApprovalRequired(Exception):
    def __init__(self, approval: dict[str, Any]) -> None:
        super().__init__(approval.get("summary", "approval required"))
        self.approval = approval


@dataclass
class Session:
    cwd: Path = field(default_factory=lambda: Path.cwd())
    ctx: AttrDict = field(default_factory=AttrDict)
    auto_approve: bool = True
    approval_counter: int = 0
    command_history: list[CommandResult] = field(default_factory=list)

    def build_globals(self, pi: Any | None = None) -> dict[str, Any]:
        host = Host(self)
        fs = FileSystem(self)
        cli = CommandNamespace(self, immediate=False)
        run = CommandNamespace(self, immediate=True)
        globals_map = {
            "ctx": self.ctx,
            "host": host,
            "fs": fs,
            "cli": cli,
            "run": run,
            "tools": Tools(self),
            "tmp": TempNamespace(self),
            "http": HttpNamespace(self),
            "fd": FdNamespace(self),
            "rg": RgNamespace(self),
            "sqlite": SqliteNamespace(self),
            "kubectl": KubectlNamespace(self),
            "text": TextNamespace(),
            "seq": SeqNamespace(),
            "obj": ObjNamespace(),
            "hr": hr,
        }
        if pi is not None:
            globals_map["pi"] = pi
        return globals_map

    def require_approval(self, tool: str, summary: str, args: dict[str, Any]) -> None:
        if self.auto_approve:
            return
        self.approval_counter += 1
        raise ApprovalRequired(
            {
                "id": f"approval-{self.approval_counter}",
                "tool": tool,
                "summary": summary,
                "args": to_json_value(args),
            }
        )


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
        approve_fs_write(self._session, target)
        write_text_file(target, content)
        return True

    def write_json(
        self, path: str | os.PathLike[str], value: Any, indent: int = 2
    ) -> bool:
        return self.write(path, json.dumps(value, indent=indent) + "\n")

    def write_json_lines(self, path: str | os.PathLike[str], values: list[Any]) -> bool:
        lines = (json.dumps(value) for value in values)
        return self.write(path, "\n".join(lines) + ("\n" if values else ""))

    def write_jsonl(self, path: str | os.PathLike[str], values: list[Any]) -> bool:
        return self.write_json_lines(path, values)

    def write_csv(self, path: str | os.PathLike[str], rows: list[Any]) -> bool:
        return self.write(path, serialize_delimited_rows(rows, ","))

    def write_tsv(self, path: str | os.PathLike[str], rows: list[Any]) -> bool:
        return self.write(path, serialize_delimited_rows(rows, "\t"))

    def open(self, path: str | os.PathLike[str], format: str | None = None) -> Any:
        return open_data_file(self._resolve(path), format)

    def exists(self, path: str | os.PathLike[str]) -> bool:
        return self._resolve(path).exists()

    def remove(self, path: str | os.PathLike[str]) -> bool:
        target = self._resolve(path)
        self._session.require_approval(
            "fs.remove", f"Remove {target}", {"path": str(target)}
        )
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


def approve_fs_write(session: Session, path: Path) -> None:
    session.require_approval("fs.write", f"Write {path}", {"path": str(path)})


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
    path.write_text(serialize_delimited_rows(rows, delimiter), newline="")


def serialize_delimited_rows(rows: list[Any], delimiter: str) -> str:
    output = io.StringIO()
    writer = csv.writer(output, delimiter=delimiter, lineterminator="\n")
    write_delimited_rows(writer, rows)
    return output.getvalue()


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


def parse_or_serialize_delimited(value: Any, delimiter: str) -> Any:
    if isinstance(value, str):
        return list(csv.DictReader(io.StringIO(value), delimiter=delimiter))
    return serialize_delimited_rows(value, delimiter)


def read_toml_file(path: Path) -> Any:
    try:
        import tomllib
    except ModuleNotFoundError as exc:
        raise ValueError("Unsupported file format: toml") from exc
    with path.open("rb") as handle:
        return tomllib.load(handle)


class Tools:
    def __init__(self, session: Session) -> None:
        self._session = session
        self.file = FileTools(session)
        self.git = GitTools(session)
        self.github = GithubTools(session)
        self.tmux = TmuxTools(session)
        self.browser = BrowserTools(session)

    def sudo(self, command: CommandBuilder) -> CommandBuilder:
        if not isinstance(command, CommandBuilder):
            raise TypeError("tools.sudo expects a CommandBuilder")
        return CommandBuilder(
            self._session,
            "authsudo",
            (command.program, *command.args),
            cwd=command.cwd,
            stdin=command.stdin,
            env_overrides=command.env_overrides,
            inherit_env=command.inherit_env,
        )

    def powershell(
        self, script: str, options: dict[str, Any] | None = None
    ) -> CommandBuilder:
        options = options or {}
        executable = str(options.get("executable", "pwsh"))
        encoded = base64.b64encode(str(script).encode("utf-16le")).decode("ascii")
        return CommandBuilder(self._session, executable)(
            "-NoProfile", "-EncodedCommand", encoded
        )

    def ssh(self, options: dict[str, Any] | None = None) -> SshTools:
        return SshTools(self._session, options or {})


class FileTools:
    def __init__(self, session: Session) -> None:
        self._session = session

    def replace(
        self, path: str | os.PathLike[str], from_or_options: Any, to: str | None = None
    ) -> dict[str, int]:
        target = self._resolve(path)
        options = normalize_replace_options(from_or_options, to)
        original = target.read_text()
        replaced, count = replace_text(original, options)
        approve_fs_write(self._session, target)
        write_text_file(target, replaced)
        return {"replacements": count}

    def patch(
        self, path_or_patch: str | os.PathLike[str], maybe_patch: str | None = None
    ) -> list[dict[str, Any]]:
        patches = parse_patch_input(path_or_patch, maybe_patch)
        return [self._apply_patch(item) for item in patches]

    def _apply_patch(self, patch: FilePatch) -> dict[str, Any]:
        target = self._resolve(patch.path)
        original = [] if patch.is_new else target.read_text().splitlines(keepends=True)
        updated = apply_hunks(original, patch.hunks, str(patch.path))
        approve_fs_write(self._session, target)
        write_text_file(target, "".join(updated))
        return {"path": str(target), "hunks": len(patch.hunks)}

    def _resolve(self, path: str | os.PathLike[str]) -> Path:
        candidate = Path(path).expanduser()
        if not candidate.is_absolute():
            candidate = self._session.cwd / candidate
        return candidate.resolve()


@dataclass(frozen=True)
class FilePatch:
    path: str
    hunks: list[PatchHunk]
    is_new: bool = False


@dataclass(frozen=True)
class PatchHunk:
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    lines: list[str]


def parse_patch_input(
    path_or_patch: str | os.PathLike[str], maybe_patch: str | None
) -> list[FilePatch]:
    if maybe_patch is not None:
        return [FilePatch(str(path_or_patch), parse_hunks(str(maybe_patch)))]
    return parse_file_patches(str(path_or_patch))


def parse_file_patches(patch_text: str) -> list[FilePatch]:
    lines = patch_text.splitlines(keepends=True)
    patches: list[FilePatch] = []
    index = 0
    while index < len(lines):
        if not lines[index].startswith("--- "):
            index += 1
            continue
        old_path = parse_diff_path(lines[index])
        index += 1
        if index >= len(lines) or not lines[index].startswith("+++ "):
            raise ValueError("patch expected +++ header after --- header")
        new_path = parse_diff_path(lines[index])
        path, is_new = choose_patch_target(old_path, new_path)
        index += 1
        hunk_lines: list[str] = []
        while index < len(lines) and not lines[index].startswith("--- "):
            hunk_lines.append(lines[index])
            index += 1
        patches.append(FilePatch(path, parse_hunks("".join(hunk_lines)), is_new))
    if not patches:
        raise ValueError("patch requires unified diff headers or explicit path")
    return patches


def parse_diff_path(header: str) -> str:
    path = header[4:].strip().split("\t", 1)[0]
    if path.startswith("a/") or path.startswith("b/"):
        return path[2:]
    return path


def choose_patch_target(old_path: str, new_path: str) -> tuple[str, bool]:
    if new_path == "/dev/null":
        raise ValueError("patch deletion is not supported")
    if old_path == "/dev/null":
        return new_path, True
    return new_path, False


def parse_hunks(patch_text: str) -> list[PatchHunk]:
    lines = patch_text.splitlines(keepends=True)
    hunks: list[PatchHunk] = []
    index = 0
    while index < len(lines):
        if not lines[index].startswith("@@"):
            if lines[index].strip():
                raise ValueError(
                    f"patch expected hunk header, got {lines[index].rstrip()}"
                )
            index += 1
            continue
        old_start, old_count, new_start, new_count = parse_hunk_header(lines[index])
        index += 1
        body: list[str] = []
        while index < len(lines) and not lines[index].startswith("@@"):
            body.append(lines[index])
            index += 1
        hunks.append(PatchHunk(old_start, old_count, new_start, new_count, body))
    if not hunks:
        raise ValueError("patch requires at least one hunk")
    return hunks


def parse_hunk_header(header: str) -> tuple[int, int, int, int]:
    match = re.match(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", header)
    if not match:
        raise ValueError(f"invalid hunk header: {header.rstrip()}")
    old_start = int(match.group(1))
    old_count = int(match.group(2) or "1")
    new_start = int(match.group(3))
    new_count = int(match.group(4) or "1")
    return old_start, old_count, new_start, new_count


def apply_hunks(original: list[str], hunks: list[PatchHunk], path: str) -> list[str]:
    output: list[str] = []
    cursor = 0
    for hunk in hunks:
        start = max(hunk.old_start - 1, 0)
        if start < cursor:
            raise ValueError(f"patch hunk overlaps previous hunk in {path}")
        output.extend(original[cursor:start])
        cursor = apply_hunk_lines(original, output, hunk, start, path)
    output.extend(original[cursor:])
    return output


def apply_hunk_lines(
    original: list[str], output: list[str], hunk: PatchHunk, cursor: int, path: str
) -> int:
    for line in hunk.lines:
        if line.startswith("\\ No newline"):
            continue
        marker = line[:1]
        text = line[1:]
        if marker == " ":
            cursor = copy_expected_line(original, output, cursor, text, path)
        elif marker == "-":
            cursor = remove_expected_line(original, cursor, text, path)
        elif marker == "+":
            output.append(text)
        else:
            raise ValueError(f"patch invalid hunk line in {path}: {line.rstrip()}")
    return cursor


def copy_expected_line(
    original: list[str], output: list[str], cursor: int, expected: str, path: str
) -> int:
    ensure_expected_line(original, cursor, expected, path)
    output.append(original[cursor])
    return cursor + 1


def remove_expected_line(
    original: list[str], cursor: int, expected: str, path: str
) -> int:
    ensure_expected_line(original, cursor, expected, path)
    return cursor + 1


def ensure_expected_line(
    original: list[str], cursor: int, expected: str, path: str
) -> None:
    if cursor >= len(original):
        raise ValueError(
            f"patch mismatch in {path}: expected {expected.rstrip()} at end of file"
        )
    actual = original[cursor]
    if actual != expected:
        raise ValueError(
            f"patch mismatch in {path}: expected {expected.rstrip()!r}, found {actual.rstrip()!r}"
        )


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
        return replace_occurrence(
            text, needle, replacement, matches, int(options["occurrence"])
        )
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


def replace_occurrence(
    text: str, needle: str, replacement: str, matches: list[int], occurrence: int
) -> tuple[str, int]:
    if occurrence < 1 or occurrence > len(matches):
        raise ValueError(f"replace occurrence {occurrence} not found")
    index = matches[occurrence - 1]
    return text[:index] + replacement + text[index + len(needle) :], 1


class FdNamespace:
    def __init__(self, session: Session) -> None:
        self._session = session

    def find(
        self, pattern: str = ".", options: dict[str, Any] | None = None
    ) -> list[str]:
        normalized = normalize_fd_options(options or {})
        root = resolve_session_path(self._session, normalized.get("root", "."))
        matches = [
            path
            for path in walk_fd_paths(root, normalized)
            if fd_path_matches(path, root, pattern, normalized)
        ]
        return [
            format_path(path, self._session.cwd, bool(normalized.get("absolute_path")))
            for path in sorted(matches)
        ]

    def files(
        self, root: str | os.PathLike[str] = ".", options: dict[str, Any] | None = None
    ) -> list[str]:
        merged = {**(options or {}), "root": root, "type": "file"}
        return self.find(".", merged)

    def dirs(
        self, root: str | os.PathLike[str] = ".", options: dict[str, Any] | None = None
    ) -> list[str]:
        merged = {**(options or {}), "root": root, "type": "directory"}
        return self.find(".", merged)


def normalize_fd_options(options: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(options)
    if "ignored" in normalized:
        normalized.pop("ignored")
    excludes = normalized.get("exclude", [])
    if isinstance(excludes, str):
        excludes = [excludes]
    normalized["exclude"] = [str(item) for item in excludes]
    if normalized.get("type") not in {None, "file", "directory"}:
        raise ValueError("fd type must be 'file' or 'directory'")
    return normalized


def walk_fd_paths(root: Path, options: dict[str, Any]) -> list[Path]:
    if not root.exists():
        return []
    hidden = bool(options.get("hidden"))
    max_depth = options.get("max_depth")
    max_depth_int = int(max_depth) if max_depth is not None else None
    results: list[Path] = []
    for current, dirs, files in os.walk(root):
        current_path = Path(current)
        depth = relative_depth(current_path, root)
        if not hidden:
            dirs[:] = [item for item in dirs if not is_hidden_name(item)]
            files = [item for item in files if not is_hidden_name(item)]
        dirs[:] = [
            item
            for item in dirs
            if not excluded_path(current_path / item, root, options["exclude"])
        ]
        files = [
            item
            for item in files
            if not excluded_path(current_path / item, root, options["exclude"])
        ]
        if max_depth_int is not None and depth >= max_depth_int:
            dirs[:] = []
        if options.get("type") in {None, "directory"} and current_path != root:
            results.append(current_path)
        if options.get("type") in {None, "file"}:
            results.extend(current_path / name for name in files)
    return results


def fd_path_matches(
    path: Path, root: Path, pattern: str, options: dict[str, Any]
) -> bool:
    if extension := options.get("extension"):
        if path.suffix.lstrip(".") != str(extension).lstrip("."):
            return False
    if pattern in {"", "."}:
        return True
    name = path.name
    relative = path.relative_to(root).as_posix()
    if options.get("glob"):
        return fnmatch.fnmatch(name, pattern) or fnmatch.fnmatch(relative, pattern)
    return pattern in name


def excluded_path(path: Path, root: Path, excludes: list[str]) -> bool:
    relative = path.relative_to(root).as_posix()
    return any(
        fnmatch.fnmatch(path.name, pattern) or fnmatch.fnmatch(relative, pattern)
        for pattern in excludes
    )


def relative_depth(path: Path, root: Path) -> int:
    if path == root:
        return 0
    return len(path.relative_to(root).parts)


def is_hidden_name(name: str) -> bool:
    return name.startswith(".")


class RgNamespace:
    def __init__(self, session: Session) -> None:
        self._session = session

    def __call__(
        self, pattern: str, paths: Any = None, options: dict[str, Any] | None = None
    ) -> SearchResult:
        return self.search(pattern, paths, options)

    def search(
        self, pattern: str, paths: Any = None, options: dict[str, Any] | None = None
    ) -> SearchResult:
        normalized = normalize_rg_options(options or {})
        matches = collect_rg_matches(self._session, pattern, paths, normalized)
        stdout = render_rg_stdout(matches, normalized)
        return SearchResult(
            stdout=stdout,
            stderr="",
            exit_code=0 if matches else 1,
            matches=matches,
            json_mode=bool(normalized.get("json")),
        )

    def files(
        self, pattern: str, paths: Any = None, options: dict[str, Any] | None = None
    ) -> list[str]:
        normalized = normalize_rg_options(options or {})
        matches = collect_rg_matches(self._session, pattern, paths, normalized)
        return sorted({match["path"] for match in matches})

    def matches(
        self, pattern: str, paths: Any = None, options: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        normalized = normalize_rg_options(options or {})
        return collect_rg_matches(self._session, pattern, paths, normalized)


@dataclass
class SearchResult:
    stdout: str
    stderr: str
    exit_code: int
    matches: list[dict[str, Any]] = field(default_factory=list)
    json_mode: bool = False

    def text(self) -> str:
        return self.stdout

    def lines(self) -> list[str]:
        return self.stdout.splitlines()

    def json(self) -> Any:
        if self.json_mode:
            return [rg_json_event(match) for match in self.matches]
        return json.loads(self.stdout)


def normalize_rg_options(options: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(options)
    if isinstance(normalized.get("glob"), str):
        normalized["glob"] = [normalized["glob"]]
    elif normalized.get("glob") is None:
        normalized["glob"] = []
    return normalized


def collect_rg_matches(
    session: Session, pattern: str, paths: Any, options: dict[str, Any]
) -> list[dict[str, Any]]:
    compiled = compile_rg_pattern(pattern, options)
    results: list[dict[str, Any]] = []
    per_file_counts: dict[str, int] = {}
    for path in iter_rg_files(session, paths, options):
        display = format_path(path, session.cwd, False)
        for line_number, line in enumerate(read_text_lines(path), start=1):
            submatches = find_rg_submatches(compiled, line, pattern, options)
            if not submatches:
                continue
            limit = options.get("max_count")
            if limit is not None and per_file_counts.get(display, 0) >= int(limit):
                break
            match = {
                "path": display,
                "line_number": line_number,
                "line": line,
                "submatches": submatches,
            }
            results.append(match)
            per_file_counts[display] = per_file_counts.get(display, 0) + 1
    return results


def compile_rg_pattern(pattern: str, options: dict[str, Any]) -> re.Pattern[str] | None:
    if options.get("fixed"):
        return None
    flags = re.IGNORECASE if options.get("ignore_case") else 0
    return re.compile(pattern, flags)


def find_rg_submatches(
    compiled: re.Pattern[str] | None, line: str, pattern: str, options: dict[str, Any]
) -> list[dict[str, Any]]:
    if compiled is not None:
        return [
            {"text": match.group(0), "start": match.start(), "end": match.end()}
            for match in compiled.finditer(line)
        ]
    haystack = line.lower() if options.get("ignore_case") else line
    needle = pattern.lower() if options.get("ignore_case") else pattern
    submatches: list[dict[str, Any]] = []
    start = 0
    while needle:
        index = haystack.find(needle, start)
        if index < 0:
            break
        end = index + len(needle)
        submatches.append({"text": line[index:end], "start": index, "end": end})
        start = end
    return submatches


def iter_rg_files(session: Session, paths: Any, options: dict[str, Any]) -> list[Path]:
    roots = normalize_rg_paths(session, paths)
    files: list[Path] = []
    for root in roots:
        if root.is_file():
            candidates = [root]
        else:
            candidates = list(walk_search_files(root, bool(options.get("hidden"))))
        files.extend(
            path
            for path in candidates
            if rg_glob_matches(path, session.cwd, options.get("glob", []))
        )
    return sorted(dict.fromkeys(files))


def normalize_rg_paths(session: Session, paths: Any) -> list[Path]:
    if paths is None:
        return [session.cwd]
    if isinstance(paths, (str, os.PathLike)):
        paths = [paths]
    return [resolve_session_path(session, path) for path in paths]


def walk_search_files(root: Path, hidden: bool) -> list[Path]:
    results: list[Path] = []
    if not root.exists():
        return results
    for current, dirs, files in os.walk(root):
        if not hidden:
            dirs[:] = [item for item in dirs if not is_hidden_name(item)]
            files = [item for item in files if not is_hidden_name(item)]
        results.extend(Path(current) / name for name in files)
    return results


def rg_glob_matches(path: Path, cwd: Path, patterns: list[str]) -> bool:
    if not patterns:
        return True
    display = format_path(path, cwd, False)
    return any(
        fnmatch.fnmatch(path.name, pattern) or fnmatch.fnmatch(display, pattern)
        for pattern in patterns
    )


def read_text_lines(path: Path) -> list[str]:
    return path.read_text(errors="replace").splitlines()


def render_rg_stdout(matches: list[dict[str, Any]], options: dict[str, Any]) -> str:
    if options.get("json"):
        return "\n".join(json.dumps(rg_json_event(match)) for match in matches) + (
            "\n" if matches else ""
        )
    if options.get("files_with_matches"):
        lines = sorted({match["path"] for match in matches})
    else:
        lines = [
            f"{match['path']}:{match['line_number']}:{match['line']}"
            for match in matches
        ]
    return "\n".join(lines) + ("\n" if lines else "")


def rg_json_event(match: dict[str, Any]) -> dict[str, Any]:
    return {"type": "match", "data": match}


class SqliteNamespace:
    def __init__(self, session: Session) -> None:
        self._session = session

    def query(
        self,
        database: str | os.PathLike[str],
        sql: str,
        options: dict[str, Any] | None = None,
    ) -> Any:
        del options
        target = resolve_session_path(self._session, database)
        target.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(target) as connection:
            connection.row_factory = sqlite3.Row
            cursor = connection.execute(sql)
            if cursor.description is not None:
                return [dict(row) for row in cursor.fetchall()]
            connection.commit()
            return {"rows_affected": cursor.rowcount}


class KubectlNamespace:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(
        self, resource: str, options: dict[str, Any] | None = None
    ) -> CommandBuilder:
        options = options or {}
        args = ["get", str(resource)]
        if name := options.get("name"):
            args.append(str(name))
        if namespace := options.get("namespace"):
            args.extend(["--namespace", str(namespace)])
        if options.get("all_namespaces"):
            args.append("--all-namespaces")
        if selector := options.get("selector"):
            args.extend(["--selector", str(selector)])
        args.extend(["--output", str(options.get("output", "json"))])
        return CommandBuilder(self._session, "kubectl")(*args)


class GitTools:
    def __init__(self, session: Session) -> None:
        self._session = session

    def status(self, options: dict[str, Any] | None = None) -> str:
        return self._git_builder(options or {})("status", "--short", "--branch").text()

    def build_commit(self, options: dict[str, Any]) -> CommandBuilder:
        subject = required_commit_subject(options)
        message = commit_message(subject, options)
        builder = self._git_builder(options)("commit", "--file", "-")
        builder = append_commit_flags(builder, options)
        paths = normalize_paths(options)
        if paths:
            builder = builder("--", *paths)
        return builder.stdin_text(message)

    def commit(self, options: dict[str, Any]) -> CommandResult:
        return self.build_commit(options).run()

    def _git_builder(self, options: dict[str, Any]) -> CommandBuilder:
        builder = CommandBuilder(self._session, "git")
        cwd = options.get("cwd", options.get("repo"))
        return builder.in_(cwd) if cwd else builder


class GithubTools:
    def __init__(self, session: Session) -> None:
        self._session = session

    def pr_view(
        self, number: int | str | None = None, options: dict[str, Any] | None = None
    ) -> CommandBuilder:
        return gh_builder(self._session, ["pr", "view"], number, options)

    def run_view(
        self, run_id: int | str | None = None, options: dict[str, Any] | None = None
    ) -> CommandBuilder:
        return gh_builder(self._session, ["run", "view"], run_id, options)

    def create_pr(self, options: dict[str, Any] | None = None) -> CommandBuilder:
        return append_options(
            CommandBuilder(self._session, "gh")("pr", "create"), options or {}
        )

    prView = pr_view
    runView = run_view
    createPr = create_pr


class TmuxTools:
    def __init__(self, session: Session) -> None:
        self._session = session

    def command(self, *args: object) -> CommandBuilder:
        return CommandBuilder(self._session, "tmux")(*args)

    def open(self, name: str, options: dict[str, Any] | None = None) -> CommandBuilder:
        del options
        return self.command("new-session", "-d", "-s", name)

    def close(self, target: str) -> CommandBuilder:
        return self.command("kill-session", "-t", target)

    def send(self, target: str, keys: str) -> CommandBuilder:
        return self.command("send-keys", "-t", target, keys, "Enter")

    def capture(self, target: str) -> CommandBuilder:
        return self.command("capture-pane", "-p", "-t", target)

    def run(self, target: str, command: str) -> dict[str, CommandBuilder]:
        return {"send": self.send(target, command), "capture": self.capture(target)}


class BrowserTools:
    def __init__(self, session: Session) -> None:
        self._session = session

    def open(self, url: str) -> CommandBuilder:
        return self._browser("open", url)

    def get(self, name: str) -> CommandBuilder:
        return self._browser("get", name)

    def snapshot(self, options: dict[str, Any] | None = None) -> CommandBuilder:
        return append_options(self._browser("snapshot"), options or {})

    def exceptions(self, options: dict[str, Any] | None = None) -> CommandBuilder:
        return append_options(self._browser("exceptions"), options or {})

    def console(self, options: dict[str, Any] | None = None) -> CommandBuilder:
        return append_options(self._browser("console"), options or {})

    def _browser(self, *args: object) -> CommandBuilder:
        return CommandBuilder(self._session, "browser-cli")(*args)


class SshTools:
    def __init__(self, session: Session, options: dict[str, Any]) -> None:
        self._session = session
        self._options = options

    def run(self, command: CommandBuilder) -> CommandBuilder:
        if not isinstance(command, CommandBuilder):
            raise TypeError("ssh.run expects a CommandBuilder")
        return self._build((command.program, *command.args), command)

    def cli(self, command: CommandBuilder | str) -> CommandBuilder:
        if isinstance(command, CommandBuilder):
            return self.run(command)
        return self._build((str(command),), None)

    def _build(
        self, remote_args: tuple[str, ...], source: CommandBuilder | None
    ) -> CommandBuilder:
        args = ssh_base_args(self._options) + ["--", *remote_args]
        program = "ssh"
        if (
            self._options.get("password")
            and self._options.get("password_mode", "plain") == "plain"
        ):
            program = "sshpass"
            args = ["-p", str(self._options["password"]), *args]
        builder = CommandBuilder(self._session, program)(*args)
        if source is not None:
            builder = builder._copy(
                cwd=source.cwd,
                stdin=source.stdin,
                env_overrides=source.env_overrides,
                inherit_env=source.inherit_env,
            )
        return builder


def resolve_session_path(session: Session, path: str | os.PathLike[str]) -> Path:
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        candidate = session.cwd / candidate
    return candidate.resolve()


def format_path(path: Path, cwd: Path, absolute: bool) -> str:
    resolved = path.resolve()
    if absolute:
        return str(resolved)
    try:
        return resolved.relative_to(cwd).as_posix()
    except ValueError:
        return str(resolved)


class TempNamespace:
    def __init__(self, session: Session) -> None:
        self._session = session

    def file(self, prefix: str = "tmp", suffix: str = "") -> TmpFile:
        self._session.require_approval(
            "tmp.file", "Create temporary file", {"prefix": prefix, "suffix": suffix}
        )
        return TmpFile.reserve(self._session, prefix, suffix)

    def dir(self, prefix: str = "tmp") -> TmpDir:
        self._session.require_approval(
            "tmp.dir", "Create temporary directory", {"prefix": prefix}
        )
        return TmpDir.create(self._session, prefix)


@dataclass
class TmpFile:
    path: Path
    session: Session = field(repr=False)

    @classmethod
    def reserve(cls, session: Session, prefix: str, suffix: str) -> TmpFile:
        handle = tempfile.NamedTemporaryFile(prefix=prefix, suffix=suffix, delete=True)
        path = Path(handle.name)
        handle.close()
        return cls(path, session)

    def __str__(self) -> str:
        return str(self.path)

    def __repr__(self) -> str:
        return f"TmpFile({self.path!s})"

    def cleanup(self) -> bool:
        self.session.require_approval(
            "tmp.file.cleanup",
            f"Remove temporary file {self.path}",
            {"path": str(self.path)},
        )
        self.path.unlink(missing_ok=True)
        return True

    def write(self, content: str) -> bool:
        approve_fs_write(self.session, self.path)
        write_text_file(self.path, content)
        return True

    def write_json(self, value: Any, indent: int = 2) -> bool:
        return self.write(json.dumps(value, indent=indent) + "\n")

    def write_json_lines(self, values: list[Any]) -> bool:
        lines = (json.dumps(value) for value in values)
        return self.write("\n".join(lines) + ("\n" if values else ""))

    def write_jsonl(self, values: list[Any]) -> bool:
        return self.write_json_lines(values)

    def write_csv(self, rows: list[Any]) -> bool:
        return self.write(serialize_delimited_rows(rows, ","))

    def write_tsv(self, rows: list[Any]) -> bool:
        return self.write(serialize_delimited_rows(rows, "\t"))


@dataclass
class TmpDir:
    path: Path
    session: Session = field(repr=False)

    @classmethod
    def create(cls, session: Session, prefix: str) -> TmpDir:
        return cls(Path(tempfile.mkdtemp(prefix=prefix)), session)

    def __str__(self) -> str:
        return str(self.path)

    def __repr__(self) -> str:
        return f"TmpDir({self.path!s})"

    def cleanup(self) -> bool:
        self.session.require_approval(
            "tmp.dir.cleanup",
            f"Remove temporary directory {self.path}",
            {"path": str(self.path)},
        )
        shutil.rmtree(self.path, ignore_errors=True)
        return True


class HttpNamespace:
    def __init__(self, session: Session) -> None:
        self._session = session

    def request(
        self, method: str, url: str, options: dict[str, Any] | None = None
    ) -> HttpRequestBuilder:
        return HttpRequestBuilder(self._session, method.upper(), url, options or {})

    def get(
        self, url: str, options: dict[str, Any] | None = None
    ) -> HttpRequestBuilder:
        return self.request("GET", url, options)

    def post(
        self, url: str, options: dict[str, Any] | None = None
    ) -> HttpRequestBuilder:
        return self.request("POST", url, options)

    def put(
        self, url: str, options: dict[str, Any] | None = None
    ) -> HttpRequestBuilder:
        return self.request("PUT", url, options)

    def patch(
        self, url: str, options: dict[str, Any] | None = None
    ) -> HttpRequestBuilder:
        return self.request("PATCH", url, options)

    def delete(
        self, url: str, options: dict[str, Any] | None = None
    ) -> HttpRequestBuilder:
        return self.request("DELETE", url, options)

    def head(
        self, url: str, options: dict[str, Any] | None = None
    ) -> HttpRequestBuilder:
        return self.request("HEAD", url, options)

    def session(self, options: dict[str, Any] | None = None) -> HttpClient:
        return HttpClient.from_options(self._session, options or {})


@dataclass(frozen=True)
class HttpClient:
    session: Session
    base_url: str | None
    headers: dict[str, str]

    @classmethod
    def from_options(cls, session: Session, options: dict[str, Any]) -> HttpClient:
        headers = {
            str(key): str(value) for key, value in options.get("headers", {}).items()
        }
        base_url = options.get("base_url")
        return cls(session, str(base_url) if base_url is not None else None, headers)

    def request(
        self, method: str, url: str, options: dict[str, Any] | None = None
    ) -> HttpRequestBuilder:
        merged = merge_http_options(self.headers, options or {})
        return HttpRequestBuilder(
            self.session, method.upper(), join_base_url(self.base_url, url), merged
        )

    def get(
        self, url: str, options: dict[str, Any] | None = None
    ) -> HttpRequestBuilder:
        return self.request("GET", url, options)

    def post(
        self, url: str, options: dict[str, Any] | None = None
    ) -> HttpRequestBuilder:
        return self.request("POST", url, options)

    def put(
        self, url: str, options: dict[str, Any] | None = None
    ) -> HttpRequestBuilder:
        return self.request("PUT", url, options)

    def patch(
        self, url: str, options: dict[str, Any] | None = None
    ) -> HttpRequestBuilder:
        return self.request("PATCH", url, options)

    def delete(
        self, url: str, options: dict[str, Any] | None = None
    ) -> HttpRequestBuilder:
        return self.request("DELETE", url, options)

    def head(
        self, url: str, options: dict[str, Any] | None = None
    ) -> HttpRequestBuilder:
        return self.request("HEAD", url, options)


def merge_http_options(
    default_headers: dict[str, str], options: dict[str, Any]
) -> dict[str, Any]:
    merged = dict(options)
    request_headers = {
        str(key): str(value) for key, value in options.get("headers", {}).items()
    }
    merged["headers"] = {**default_headers, **request_headers}
    return merged


def join_base_url(base_url: str | None, url: str) -> str:
    if base_url is None or urllib.parse.urlparse(url).scheme:
        return url
    return urllib.parse.urljoin(base_url.rstrip("/") + "/", url.lstrip("/"))


@dataclass(frozen=True)
class HttpRequestBuilder:
    session: Session
    method: str
    url: str
    options: dict[str, Any]

    def run(self) -> HttpResponse:
        self.session.require_approval(
            "http.request",
            f"{self.method} {self.url}",
            {"method": self.method, "url": self.url},
        )
        request = build_url_request(self.method, self.url, self.options)
        try:
            with urllib.request.urlopen(request) as response:
                return http_response_from(
                    response.status, response.headers.items(), response.read()
                )
        except urllib.error.HTTPError as error:
            return http_response_from(error.code, error.headers.items(), error.read())

    def text(self) -> str:
        return self.run().text()

    def json(self) -> Any:
        return self.run().json()

    def bytes(self) -> bytes:
        return self.run().body

    def to_file(self, path: str | os.PathLike[str]) -> str:
        response = self.run()
        target = self._resolve(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(response.body)
        return str(target)

    def _resolve(self, path: str | os.PathLike[str]) -> Path:
        candidate = Path(path).expanduser()
        if not candidate.is_absolute():
            candidate = self.session.cwd / candidate
        return candidate.resolve()


@dataclass(frozen=True)
class HttpResponse:
    status: int
    headers: dict[str, str]
    body: bytes

    def text(self) -> str:
        return self.body.decode()

    def json(self) -> Any:
        return json.loads(self.text())


def build_url_request(
    method: str, url: str, options: dict[str, Any]
) -> urllib.request.Request:
    headers = {
        str(key): str(value) for key, value in options.get("headers", {}).items()
    }
    data = encode_request_body(options, headers)
    return urllib.request.Request(url, data=data, headers=headers, method=method)


def encode_request_body(
    options: dict[str, Any], headers: dict[str, str]
) -> bytes | None:
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
    return HttpResponse(
        status=status,
        headers={str(key): str(value) for key, value in headers},
        body=body,
    )


_process_ids = itertools.count(1)


def validate_command_stream_name(stream: str) -> None:
    if stream not in {"stdout", "stderr"}:
        raise ValueError("command stream must be 'stdout' or 'stderr'")


def select_command_output(result: CommandResult, stream: str) -> str:
    validate_command_stream_name(stream)
    return result.stdout if stream == "stdout" else result.stderr


@dataclass(frozen=True)
class CommandStream:
    builder: CommandBuilder
    stream: str = "stdout"

    def __post_init__(self) -> None:
        validate_command_stream_name(self.stream)


@dataclass(frozen=True)
class CommandBuilder:
    session: Session
    program: str
    args: tuple[str, ...] = ()
    cwd: Path | None = None
    stdin: str | None = None
    env_overrides: dict[str, str] = field(default_factory=dict)
    stdin_source: CommandStream | CommandResult | None = None
    inherit_env: bool = True

    def __call__(self, *args: object) -> CommandBuilder:
        return self._copy(args=self.args + tuple(str(arg) for arg in args))

    def in_(self, cwd: str | os.PathLike[str]) -> CommandBuilder:
        return self._copy(cwd=self._resolve(cwd))

    def env(
        self, name_or_values: str | dict[Any, Any], value: object | None = None
    ) -> CommandBuilder:
        updates = normalize_env_updates(name_or_values, value)
        return self._copy(env_overrides={**self.env_overrides, **updates})

    def env_inherit(self, enabled: bool) -> CommandBuilder:
        return self._copy(inherit_env=bool(enabled))

    def env_clear(self) -> CommandBuilder:
        return self.env_inherit(False)

    def stream(self, stream: str = "stdout") -> CommandStream:
        return CommandStream(self, stream)

    def stdout_stream(self) -> CommandStream:
        return self.stream("stdout")

    def stderr_stream(self) -> CommandStream:
        return self.stream("stderr")

    def stdin_text(self, text: str) -> CommandBuilder:
        return self._copy(stdin=text, stdin_source=None)

    def stdin_from(
        self,
        source: CommandBuilder | CommandStream | CommandResult | str | bytes,
        stream: str = "stdout",
    ) -> CommandBuilder:
        validate_command_stream_name(stream)
        if isinstance(source, CommandBuilder):
            return self._copy(stdin=None, stdin_source=source.stream(stream))
        if isinstance(source, CommandStream):
            return self._copy(stdin=None, stdin_source=source)
        if isinstance(source, CommandResult):
            return self._copy(
                stdin=select_command_output(source, stream), stdin_source=source
            )
        if isinstance(source, bytes):
            return self.stdin_text(source.decode())
        return self.stdin_text(str(source))

    def stdin_file(self, path: str | os.PathLike[str]) -> CommandBuilder:
        return self.stdin_text(self._resolve(path).read_text())

    def stdin_json(self, value: Any) -> CommandBuilder:
        return self.stdin_text(json.dumps(value) + "\n")

    def stdin_lines(self, lines: list[Any]) -> CommandBuilder:
        return self.stdin_text("\n".join(str(line) for line in lines) + "\n")

    def stdin_csv(self, rows: list[Any]) -> CommandBuilder:
        return self.stdin_text(serialize_delimited_rows(rows, ","))

    def stdin_tsv(self, rows: list[Any]) -> CommandBuilder:
        return self.stdin_text(serialize_delimited_rows(rows, "\t"))

    def run(self) -> CommandResult:
        return self._run(merge_stderr=False)

    def pipe_to(
        self, next_builder: CommandBuilder, stream: str = "stdout"
    ) -> CommandResult:
        return next_builder.stdin_from(self.stream(stream)).run()

    def text(self) -> str:
        return self.run().text()

    def lines(self) -> list[str]:
        return self.run().lines()

    def json(self) -> Any:
        return self.run().json()

    def stderr_text(self) -> str:
        return self.run().stderr

    def stderr_lines(self) -> list[str]:
        return self.stderr_text().splitlines()

    def stderr_json(self) -> Any:
        return json.loads(self.stderr_text())

    def combined_text(self) -> str:
        return self._run(merge_stderr=True).stdout

    def to_file(self, path: str | os.PathLike[str]) -> CommandResult:
        result = self.run()
        write_text_file(self._resolve(path), result.stdout)
        return result

    def stderr_to_file(self, path: str | os.PathLike[str]) -> CommandResult:
        result = self.run()
        write_text_file(self._resolve(path), result.stderr)
        return result

    def combined_to_file(self, path: str | os.PathLike[str]) -> CommandResult:
        result = self._run(merge_stderr=True)
        write_text_file(self._resolve(path), result.stdout)
        return result

    def tee(self, path: str | os.PathLike[str]) -> CommandResult:
        return self.to_file(path)

    def stderr_tee(self, path: str | os.PathLike[str]) -> CommandResult:
        return self.stderr_to_file(path)

    def combined_tee(self, path: str | os.PathLike[str]) -> CommandResult:
        return self.combined_to_file(path)

    def spawn(self) -> ProcessHandle:
        self._require_approval()
        stdin, upstream_results = self._resolve_stdin()
        process = subprocess.Popen(
            [self.program, *self.args],
            stdin=subprocess.PIPE if stdin is not None else None,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=self.cwd or self.session.cwd,
            env=self._subprocess_env(),
            shell=False,
        )
        return ProcessHandle(
            id=next(_process_ids),
            pid=process.pid,
            program=self.program,
            args=self.args,
            stdin=stdin,
            process=process,
            upstream_results=upstream_results,
        )

    def _run(self, merge_stderr: bool) -> CommandResult:
        self._require_approval()
        stdin, upstream_results = self._resolve_stdin()
        completed = subprocess.run(
            [self.program, *self.args],
            input=stdin,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT if merge_stderr else subprocess.PIPE,
            cwd=self.cwd or self.session.cwd,
            env=self._subprocess_env(),
            shell=False,
            check=False,
        )
        return CommandResult(
            stdout=completed.stdout,
            stderr="" if merge_stderr else completed.stderr,
            exit_code=completed.returncode,
            upstream_results=upstream_results,
        )

    def _require_approval(self) -> None:
        self.session.require_approval(
            f"cli.{Path(self.program).name}",
            "Run command " + " ".join([self.program, *self.args]),
            {
                "program": self.program,
                "args": list(self.args),
                "cwd": str(self.cwd or self.session.cwd),
            },
        )

    def _resolve_stdin(self) -> tuple[str | None, tuple[CommandResult, ...]]:
        if isinstance(self.stdin_source, CommandStream):
            upstream = self.stdin_source.builder.run()
            return select_command_output(upstream, self.stdin_source.stream), (
                upstream,
            )
        if isinstance(self.stdin_source, CommandResult):
            return self.stdin, (self.stdin_source,)
        return self.stdin, ()

    def _subprocess_env(self) -> dict[str, str]:
        base = dict(os.environ) if self.inherit_env else {}
        return {**base, **self.env_overrides}

    def _copy(self, **changes: Any) -> CommandBuilder:
        values = {
            "session": self.session,
            "program": self.program,
            "args": self.args,
            "cwd": self.cwd,
            "stdin": self.stdin,
            "env_overrides": self.env_overrides,
            "stdin_source": self.stdin_source,
            "inherit_env": self.inherit_env,
        }
        values.update(changes)
        return CommandBuilder(**values)

    def _resolve(self, path: str | os.PathLike[str]) -> Path:
        candidate = Path(path).expanduser()
        if not candidate.is_absolute():
            candidate = self.session.cwd / candidate
        return candidate.resolve()


@dataclass
class ProcessHandle:
    id: int
    pid: int
    program: str
    args: tuple[str, ...]
    stdin: str | None
    process: subprocess.Popen[str]
    upstream_results: tuple[CommandResult, ...] = ()
    _result: CommandResult | None = None

    def wait(self, timeout: float | None = None) -> CommandResult:
        if self._result is not None:
            return self._result
        stdout, stderr = self.process.communicate(input=self.stdin, timeout=timeout)
        self._result = CommandResult(
            stdout=stdout,
            stderr=stderr,
            exit_code=self.process.returncode,
            upstream_results=self.upstream_results,
        )
        return self._result

    def kill(self) -> bool:
        if self.process.poll() is not None:
            return False
        self.process.kill()
        return True

    def text(self, timeout: float | None = None) -> str:
        return self.wait(timeout).text()

    def lines(self, timeout: float | None = None) -> list[str]:
        return self.wait(timeout).lines()

    def json(self, timeout: float | None = None) -> Any:
        return self.wait(timeout).json()


def normalize_env_updates(
    name_or_values: str | dict[Any, Any], value: object | None
) -> dict[str, str]:
    if isinstance(name_or_values, dict):
        return {str(key): str(item) for key, item in name_or_values.items()}
    if value is None:
        raise ValueError("env requires a value when name is provided")
    return {str(name_or_values): str(value)}


def required_commit_subject(options: dict[str, Any]) -> str:
    value = str(options.get("subject", options.get("message", "")))
    if not value:
        raise ValueError("subject or message is required")
    if "\n" in value or "\r" in value:
        raise ValueError("subject must be a single line")
    return value


def commit_message(subject: str, options: dict[str, Any]) -> str:
    body_parts: list[str] = []
    if body := options.get("body"):
        body_parts.append(str(body))
    if body_lines := options.get("body_lines", options.get("bodyLines")):
        body_parts.append("\n".join(str(line) for line in body_lines))
    body = "\n\n".join(part for part in body_parts if part)
    return f"{subject}\n\n{body}\n" if body else f"{subject}\n"


def append_commit_flags(
    builder: CommandBuilder, options: dict[str, Any]
) -> CommandBuilder:
    flag_map = {
        "amend": "--amend",
        "no_edit": "--no-edit",
        "noEdit": "--no-edit",
        "allow_empty": "--allow-empty",
        "allowEmpty": "--allow-empty",
        "no_verify": "--no-verify",
        "noVerify": "--no-verify",
        "signoff": "--signoff",
        "all": "--all",
    }
    for key, flag in flag_map.items():
        if options.get(key):
            builder = builder(flag)
    return builder


def normalize_paths(options: dict[str, Any]) -> list[str]:
    values: list[Any] = []
    for key in ("paths", "files"):
        item = options.get(key)
        if item:
            values.extend(item if isinstance(item, list | tuple) else [item])
    for key in ("path", "file"):
        if item := options.get(key):
            values.append(item)
    return [str(value) for value in values]


def append_options(builder: CommandBuilder, options: dict[str, Any]) -> CommandBuilder:
    for key, value in options.items():
        flag = "--" + str(key).replace("_", "-")
        if isinstance(value, bool):
            if value:
                builder = builder(flag)
        elif isinstance(value, list | tuple):
            builder = builder(flag, ",".join(str(item) for item in value))
        elif value is not None:
            builder = builder(flag, value)
    return builder


def gh_builder(
    session: Session,
    prefix: list[str],
    target: int | str | None,
    options: dict[str, Any] | None,
) -> CommandBuilder:
    builder = CommandBuilder(session, "gh")(*prefix)
    if target is not None:
        builder = builder(str(target))
    return append_options(builder, options or {})


def ssh_base_args(options: dict[str, Any]) -> list[str]:
    host = options.get("host")
    if not host:
        raise ValueError("ssh host is required")
    destination = f"{options['user']}@{host}" if options.get("user") else str(host)
    args = ["ssh"]
    if port := options.get("port"):
        args.extend(["-p", str(port)])
    args.append(destination)
    return args


class CommandNamespace:
    def __init__(self, session: Session, immediate: bool) -> None:
        self._session = session
        self._immediate = immediate

    def __getattr__(self, program: str) -> Any:
        command = CommandBuilder(self._session, program.replace("_", "-"))
        if self._immediate:
            return ImmediateCommand(self._session, command)
        return command

    def history(self) -> list[CommandResult]:
        return list(self._session.command_history)

    def last(self, index: int = -1) -> CommandResult:
        try:
            return self._session.command_history[index]
        except IndexError as exc:
            raise IndexError("no previous command results are available") from exc


class ImmediateCommand:
    def __init__(self, session: Session, command: CommandBuilder) -> None:
        self._session = session
        self._command = command

    def __call__(self, *args: object) -> CommandResult:
        result = self._command(*args).run()
        self._session.command_history.append(result)
        sys.stdout.write(tail_command_output(result))
        sys.stdout.flush()
        return result


def tail_command_output(result: CommandResult, line_limit: int = 300) -> str:
    text_output = result.stdout + result.stderr
    lines = text_output.splitlines(keepends=True)
    return "".join(lines[-line_limit:])


PiRequestHandler = Callable[[str, Any], Any]


class PiFooter:
    def __init__(self, snapshot: Any) -> None:
        self._snapshot = snapshot

    def snapshot(self) -> Any:
        return self._snapshot


class PiAgents:
    def __init__(self, request_handler: PiRequestHandler) -> None:
        self._request_handler = request_handler

    def spawn(self, params: Any) -> Any:
        return self._request_handler("agents.spawn", params)

    def wait(self, params: Any) -> Any:
        if isinstance(params, str):
            params = {"agentId": params}
        return self._request_handler("agents.wait", params)

    def list(self, params: Any = None) -> Any:
        return self._request_handler("agents.list", params)

    def current(self) -> Any:
        return self._request_handler("agents.current", None)

    def select(self, agent_id: str) -> Any:
        return self._request_handler("agents.select", {"agentId": agent_id})


class PiMessages:
    def __init__(self, request_handler: PiRequestHandler) -> None:
        self._request_handler = request_handler

    def enqueue(self, params: Any) -> Any:
        return self._request_handler("messages.enqueue", params)

    def last(self) -> Any:
        return self._request_handler("messages.last", None)


class PiBridge:
    def __init__(self, snapshot: Any, request_handler: PiRequestHandler) -> None:
        footer = snapshot.get("footer") if isinstance(snapshot, dict) else None
        self.footer = PiFooter(footer)
        self.agents = PiAgents(request_handler)
        self.messages = PiMessages(request_handler)
        self._request_handler = request_handler

    def compact(self, params: Any = None) -> Any:
        return self._request_handler("compact", params)

    def restart(self, params: Any = None) -> Any:
        return self._request_handler("restart", params)


class SessionStore:
    def __init__(self, auto_approve: bool = True) -> None:
        self._auto_approve = auto_approve
        self._sessions: dict[str, Session] = {}

    @classmethod
    def pending_approval(cls) -> SessionStore:
        return cls(auto_approve=False)

    @classmethod
    def new_auto_approve(cls) -> SessionStore:
        return cls(auto_approve=True)

    def evaluate(
        self,
        code: str,
        session_id: str = "default",
        pi: Any | None = None,
        pi_bridge: bool = False,
        pi_request_handler: PiRequestHandler | None = None,
    ) -> dict[str, Any]:
        session = self._session(session_id)
        console = io.StringIO()
        pi_global = self._build_pi_global(pi, pi_bridge, pi_request_handler)
        try:
            with contextlib.redirect_stdout(console):
                value = evaluate_python(code, session.build_globals(pi_global))
        except ApprovalRequired as exc:
            return {
                "type": "needs_approval",
                "executed": code,
                "console": console_lines(console),
                "approval": exc.approval,
            }
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

    def _build_pi_global(
        self,
        snapshot: Any | None,
        pi_bridge: bool,
        pi_request_handler: PiRequestHandler | None,
    ) -> PiBridge | None:
        if not pi_bridge:
            return None
        if pi_request_handler is None:
            raise ValueError("pi_bridge requires a Pi request handler")
        return PiBridge(snapshot or {}, pi_request_handler)

    def _session(self, session_id: str) -> Session:
        if not session_id:
            raise ValueError("session_id must not be empty")
        if session_id not in self._sessions:
            self._sessions[session_id] = Session(auto_approve=self._auto_approve)
        return self._sessions[session_id]


DIRECT_PROCESS_MODULES = {"subprocess"}
DIRECT_PROCESS_OS_CALLS = {"system", "popen"}
DIRECT_PROCESS_OS_PREFIXES = ("exec", "spawn")
DIRECT_PROCESS_GUIDANCE = "Use run.<program>(*args) for routine command execution, or cli.<program>(*args) when you need the advanced command builder (cwd, env, stdin, streams, piping, or inspection). Command names are resolved dynamically, so dir(run) and dir(cli) may be empty even when run.<program> works."


class DirectProcessExecutionVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.subprocess_aliases: set[str] = set()
        self.os_aliases: set[str] = set()
        self.imported_process_functions: dict[str, str] = {}
        self.violations: list[str] = []

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            root_name = alias.name.split(".", 1)[0]
            local_name = alias.asname or root_name
            if root_name in DIRECT_PROCESS_MODULES:
                self.subprocess_aliases.add(local_name)
            elif root_name == "os":
                self.os_aliases.add(local_name)
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module == "subprocess":
            for alias in node.names:
                local_name = alias.asname or alias.name
                self.imported_process_functions[local_name] = f"subprocess.{alias.name}"
        elif node.module == "os":
            for alias in node.names:
                if is_direct_os_process_call(alias.name):
                    local_name = alias.asname or alias.name
                    self.imported_process_functions[local_name] = f"os.{alias.name}"
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        call_name = direct_process_call_name(
            node.func,
            self.subprocess_aliases,
            self.os_aliases,
            self.imported_process_functions,
        )
        if call_name is not None:
            self.violations.append(call_name)
        self.generic_visit(node)


def reject_direct_process_execution(code: str) -> None:
    try:
        module = ast.parse(code, mode="exec")
    except SyntaxError:
        return
    visitor = DirectProcessExecutionVisitor()
    visitor.visit(module)
    if visitor.violations:
        call_name = visitor.violations[0]
        raise ValueError(
            f"Direct process execution via {call_name} is not allowed in pyrun_eval. {DIRECT_PROCESS_GUIDANCE}"
        )


def direct_process_call_name(
    func: ast.expr,
    subprocess_aliases: set[str],
    os_aliases: set[str],
    imported_process_functions: dict[str, str],
) -> str | None:
    if isinstance(func, ast.Name):
        return imported_process_functions.get(func.id)
    if not isinstance(func, ast.Attribute):
        return None
    owner = func.value
    if isinstance(owner, ast.Name):
        if owner.id in subprocess_aliases:
            return f"subprocess.{func.attr}"
        if owner.id in os_aliases and is_direct_os_process_call(func.attr):
            return f"os.{func.attr}"
    return None


def is_direct_os_process_call(name: str) -> bool:
    return name in DIRECT_PROCESS_OS_CALLS or name.startswith(
        DIRECT_PROCESS_OS_PREFIXES
    )


def evaluate_python(code: str, globals_map: dict[str, Any]) -> Any:
    reject_direct_process_execution(code)
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


def evaluate_exec_with_trailing_expr(
    module: ast.Module, globals_map: dict[str, Any]
) -> Any:
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
    if isinstance(value, CommandBuilder):
        serialized = {
            "program": value.program,
            "args": list(value.args),
            "cwd": str(value.cwd) if value.cwd is not None else None,
            "env": to_json_value(value.env_overrides),
            "stdin": value.stdin,
        }
        if value.stdin_source is not None:
            serialized["stdin_from"] = to_json_value(value.stdin_source)
        return serialized
    if isinstance(value, CommandStream):
        return {
            "stream": value.stream,
            "command": to_json_value(value.builder),
        }
    if isinstance(value, CommandResult):
        return {
            "stdout": value.stdout,
            "stderr": value.stderr,
            "exit_code": value.exit_code,
            "upstream_results": to_json_value(value.upstream_results),
        }
    if isinstance(value, SearchResult):
        return {
            "stdout": value.stdout,
            "stderr": value.stderr,
            "exit_code": value.exit_code,
            "text": value.text(),
            "lines": value.lines(),
            "json": value.json() if value.json_mode else None,
        }
    if isinstance(value, ProcessHandle):
        return {
            "id": value.id,
            "pid": value.pid,
            "program": value.program,
            "args": list(value.args),
        }
    if isinstance(value, HttpRequestBuilder):
        return {
            "method": value.method,
            "url": value.url,
            "options": to_json_value(value.options),
        }
    if isinstance(value, HttpResponse):
        return {
            "status": value.status,
            "headers": to_json_value(value.headers),
            "body": list(value.body),
        }
    if isinstance(value, HelperValue):
        return to_json_value(value.value)
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
