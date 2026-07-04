from __future__ import annotations

import json
import re
import sys
from typing import Any, BinaryIO

from .runtime import SessionStore

TOOL_NAME = "pyrun_eval"
PROTOCOL_VERSION = "2024-11-05"

TOOL_DESCRIPTION = (
    "Evaluate synchronous Python in a persistent Pyrun session. Available globals: "
    "ctx, host, fs, cli, run, http, tools, rg, fd, sqlite."
)

TOOL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "session_id": {
            "type": "string",
            "description": "Optional persistent Pyrun session id.",
        },
        "code": {
            "type": "string",
            "description": "Python code to evaluate synchronously.",
        },
    },
    "required": ["code"],
    "additionalProperties": False,
}


class McpServer:
    def __init__(self, store: SessionStore | None = None) -> None:
        self.store = store or SessionStore()

    def handle(self, request: dict[str, Any]) -> dict[str, Any] | None:
        method = request.get("method")
        if method == "notifications/initialized":
            return None
        if method == "initialize":
            return self._response(
                request, self._initialize_result(request.get("params"))
            )
        if method == "tools/list":
            return self._response(request, {"tools": [tool_definition()]})
        if method == "tools/call":
            return self._response(request, self._call_tool(request.get("params")))
        return self._error(request, -32601, f"method not found: {method}")

    def _initialize_result(self, params: Any) -> dict[str, Any]:
        protocol_version = PROTOCOL_VERSION
        if isinstance(params, dict) and isinstance(params.get("protocolVersion"), str):
            protocol_version = params["protocolVersion"]
        return {
            "protocolVersion": protocol_version,
            "serverInfo": {"name": "pyrun", "version": "0.1.0"},
            "capabilities": {"tools": {}},
        }

    def _call_tool(self, params: Any) -> dict[str, Any]:
        error = validate_tool_call_params(params)
        if error is not None:
            return tool_error(error)
        assert isinstance(params, dict)
        arguments = params["arguments"]
        result = self.store.evaluate(
            arguments["code"], session_id=arguments.get("session_id", "default")
        )
        return tool_result(result)

    def _response(
        self, request: dict[str, Any], result: dict[str, Any]
    ) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": request.get("id"), "result": result}

    def _error(
        self, request: dict[str, Any], code: int, message: str
    ) -> dict[str, Any]:
        return {
            "jsonrpc": "2.0",
            "id": request.get("id"),
            "error": {"code": code, "message": message},
        }


def tool_definition() -> dict[str, Any]:
    return {
        "name": TOOL_NAME,
        "description": TOOL_DESCRIPTION,
        "inputSchema": TOOL_SCHEMA,
    }


def validate_tool_call_params(params: Any) -> str | None:
    if not isinstance(params, dict):
        return "tools/call params must be an object"
    if params.get("name") != TOOL_NAME:
        return f"unknown tool: {params.get('name')}"
    arguments = params.get("arguments")
    if not isinstance(arguments, dict):
        return "arguments must be an object"
    code = arguments.get("code")
    if not isinstance(code, str):
        return "code must be a string"
    session_id = arguments.get("session_id", "default")
    if not isinstance(session_id, str):
        return "session_id must be a string"
    return None


def tool_result(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "content": [{"type": "text", "text": json.dumps(result, indent=2)}],
        "structuredContent": {"result": result},
        "isError": False,
    }


def tool_error(message: str) -> dict[str, Any]:
    return {
        "content": [{"type": "text", "text": message}],
        "structuredContent": {"error": message},
        "isError": True,
    }


def encode_frame(message: dict[str, Any]) -> bytes:
    body = json.dumps(message, separators=(",", ":")).encode("utf-8")
    return f"Content-Length: {len(body)}\r\n\r\n".encode("ascii") + body


def decode_frames(data: bytes) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    offset = 0
    while offset < len(data):
        header_end = data.find(b"\r\n\r\n", offset)
        if header_end == -1:
            raise ValueError("incomplete MCP frame header")
        headers = data[offset:header_end].decode("ascii")
        length = parse_content_length(headers)
        body_start = header_end + 4
        body_end = body_start + length
        if body_end > len(data):
            raise ValueError("incomplete MCP frame body")
        messages.append(json.loads(data[body_start:body_end].decode("utf-8")))
        offset = body_end
    return messages


def parse_content_length(headers: str) -> int:
    for line in headers.split("\r\n"):
        match = re.fullmatch(r"Content-Length: *(\d+)", line, flags=re.IGNORECASE)
        if match:
            return int(match.group(1))
    raise ValueError("missing Content-Length header")


def read_frame(stream: BinaryIO) -> dict[str, Any] | None:
    header = read_until(stream, b"\r\n\r\n")
    if header == b"":
        return None
    length = parse_content_length(header[:-4].decode("ascii"))
    body = stream.read(length)
    if len(body) != length:
        raise ValueError("incomplete MCP frame body")
    return json.loads(body.decode("utf-8"))


def read_until(stream: BinaryIO, marker: bytes) -> bytes:
    data = bytearray()
    while not data.endswith(marker):
        chunk = stream.read(1)
        if chunk == b"":
            if data:
                raise ValueError("incomplete MCP frame header")
            return b""
        data.extend(chunk)
    return bytes(data)


def run_stdio(stdin: BinaryIO, stdout: BinaryIO) -> int:
    server = McpServer()
    while True:
        request = read_frame(stdin)
        if request is None:
            return 0
        response = server.handle(request)
        if response is None:
            continue
        stdout.write(encode_frame(response))
        stdout.flush()


def main() -> int:
    return run_stdio(sys.stdin.buffer, sys.stdout.buffer)


if __name__ == "__main__":
    raise SystemExit(main())
