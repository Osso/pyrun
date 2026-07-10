from __future__ import annotations

import json
import sys
from typing import Any, TextIO

from .runtime import SessionStore, to_json_value


USAGE = "usage: pyrun-jsonl [--help]\n"


def main(
    argv: list[str] | None = None,
    *,
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    args = sys.argv[1:] if argv is None else argv
    input_stream = sys.stdin if stdin is None else stdin
    output_stream = sys.stdout if stdout is None else stdout
    error_stream = sys.stderr if stderr is None else stderr

    if args == ["--help"] or args == ["-h"]:
        output_stream.write(USAGE)
        output_stream.write("Read Pyrun evaluation requests as JSONL from standard input.\n")
        return 0
    if args:
        error_stream.write(USAGE)
        error_stream.write(f"pyrun-jsonl: unrecognized argument: {args[0]}\n")
        return 2
    if input_stream.isatty():
        error_stream.write(USAGE)
        error_stream.write("pyrun-jsonl expects JSONL on standard input.\n")
        return 0

    return run_jsonl(input_stream, output_stream, error_stream)


def run_jsonl(stdin: TextIO, stdout: TextIO, stderr: TextIO) -> int:
    store = SessionStore()
    line_number = 0
    while True:
        line = stdin.readline()
        if line == "":
            break
        line_number += 1
        if not line.strip():
            continue
        result = handle_line(store, line, line_number, stdin=stdin, stdout=stdout)
        stdout.write(json.dumps(result, separators=(",", ":")) + "\n")
        stdout.flush()
    return 0


def handle_line(
    store: SessionStore,
    line: str,
    line_number: int,
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
) -> dict[str, Any]:
    try:
        request = json.loads(line)
    except json.JSONDecodeError as exc:
        return {
            "type": "error",
            "executed": "",
            "error": f"line {line_number}: invalid JSON: {exc}",
        }
    if not isinstance(request, dict):
        return {
            "type": "error",
            "executed": "",
            "error": f"line {line_number}: request must be an object",
        }
    code = request.get("code")
    if not isinstance(code, str):
        return {
            "type": "error",
            "executed": "",
            "error": f"line {line_number}: code must be a string",
        }
    session_id = request.get("session_id", "default")
    if not isinstance(session_id, str):
        return {
            "type": "error",
            "executed": code,
            "error": f"line {line_number}: session_id must be a string",
        }
    pi_bridge = request.get("pi_bridge", False)
    if not isinstance(pi_bridge, bool):
        return {
            "type": "error",
            "executed": code,
            "error": f"line {line_number}: pi_bridge must be a boolean",
        }
    stream_console = request.get("stream_console", False)
    if not isinstance(stream_console, bool):
        return {
            "type": "error",
            "executed": code,
            "error": f"line {line_number}: stream_console must be a boolean",
        }
    try:
        return to_json_value(
            store.evaluate(
                code,
                session_id=session_id,
                pi=request.get("pi"),
                pi_bridge=pi_bridge,
                pi_request_handler=create_pi_request_handler(stdin, stdout)
                if pi_bridge
                else None,
                console_writer=create_console_writer(stdout) if stream_console else None,
            )
        )
    except Exception as exc:  # noqa: BLE001 - protocol errors are returned to JSONL caller.
        return {"type": "error", "executed": code, "error": str(exc)}


def create_console_writer(stdout: TextIO | None):
    if stdout is None:
        return None

    def write(stream: str, text: str) -> None:
        stdout.write(
            json.dumps(
                {"type": "console", "stream": stream, "text": text},
                separators=(",", ":"),
            )
            + "\n"
        )
        stdout.flush()

    return write


def create_pi_request_handler(stdin: TextIO | None, stdout: TextIO | None):
    if stdin is None or stdout is None:
        return None

    def request(method: str, params: Any) -> Any:
        stdout.write(
            json.dumps(
                {"type": "pi_request", "method": method, "params": params},
                separators=(",", ":"),
            )
            + "\n"
        )
        stdout.flush()
        line = stdin.readline()
        if line == "":
            raise RuntimeError("Pi bridge response stream ended")
        try:
            response = json.loads(line)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"invalid Pi bridge response: {exc}") from exc
        if not isinstance(response, dict):
            raise RuntimeError("Pi bridge response must be an object")
        if "error" in response:
            raise RuntimeError(str(response["error"]))
        return response.get("result")

    return request


if __name__ == "__main__":
    raise SystemExit(main())
