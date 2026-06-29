from __future__ import annotations

import json
import sys
from typing import Any, TextIO

from .runtime import SessionStore, to_json_value


def main() -> int:
    return run_jsonl(sys.stdin, sys.stdout, sys.stderr)


def run_jsonl(stdin: TextIO, stdout: TextIO, stderr: TextIO) -> int:
    store = SessionStore()
    for line_number, line in enumerate(stdin, start=1):
        if not line.strip():
            continue
        result = handle_line(store, line, line_number)
        stdout.write(json.dumps(result, separators=(",", ":")) + "\n")
        stdout.flush()
    return 0


def handle_line(store: SessionStore, line: str, line_number: int) -> dict[str, Any]:
    try:
        request = json.loads(line)
    except json.JSONDecodeError as exc:
        return {"type": "error", "executed": "", "error": f"line {line_number}: invalid JSON: {exc}"}
    if not isinstance(request, dict):
        return {"type": "error", "executed": "", "error": f"line {line_number}: request must be an object"}
    code = request.get("code")
    if not isinstance(code, str):
        return {"type": "error", "executed": "", "error": f"line {line_number}: code must be a string"}
    session_id = request.get("session_id", "default")
    if not isinstance(session_id, str):
        return {"type": "error", "executed": code, "error": f"line {line_number}: session_id must be a string"}
    return to_json_value(store.evaluate(code, session_id=session_id))


if __name__ == "__main__":
    raise SystemExit(main())
