import json
import tempfile
import unittest
from pathlib import Path

from pyrun.runtime import SessionStore


class McpHandlerTests(unittest.TestCase):
    def setUp(self):
        from pyrun.mcp import McpServer

        self.server = McpServer(SessionStore())

    def request(self, method, params=None, request_id=1):
        request = {"jsonrpc": "2.0", "id": request_id, "method": method}
        if params is not None:
            request["params"] = params
        return self.server.handle(request)

    def test_initialize_reports_tools_capability(self):
        response = self.request("initialize", {"protocolVersion": "2024-11-05"})

        self.assertEqual(response["jsonrpc"], "2.0")
        self.assertEqual(response["id"], 1)
        result = response["result"]
        self.assertIn("protocolVersion", result)
        self.assertEqual(result["serverInfo"]["name"], "pyrun")
        self.assertIn("tools", result["capabilities"])

    def test_tools_list_returns_pyrun_eval_schema(self):
        response = self.request("tools/list")
        tools = response["result"]["tools"]

        self.assertEqual(len(tools), 1)
        tool = tools[0]
        self.assertEqual(tool["name"], "pyrun_eval")
        self.assertIn("ctx", tool["description"])
        self.assertEqual(tool["inputSchema"]["required"], ["code"])
        self.assertEqual(tool["inputSchema"]["properties"]["code"]["type"], "string")
        self.assertEqual(
            tool["inputSchema"]["properties"]["session_id"]["type"], "string"
        )

    def test_tools_call_evaluates_and_returns_text_and_structured_content(self):
        response = self.request(
            "tools/call", {"name": "pyrun_eval", "arguments": {"code": "1 + 2"}}
        )
        result = response["result"]

        self.assertFalse(result["isError"])
        self.assertEqual(result["structuredContent"]["result"]["value"], 3)
        pretty = result["content"][0]["text"]
        self.assertEqual(json.loads(pretty)["value"], 3)
        self.assertIn("\n", pretty)

    def test_tools_call_persists_named_session(self):
        first = self.request(
            "tools/call",
            {
                "name": "pyrun_eval",
                "arguments": {"session_id": "s", "code": "ctx.count = 41"},
            },
            1,
        )
        second = self.request(
            "tools/call",
            {
                "name": "pyrun_eval",
                "arguments": {"session_id": "s", "code": "ctx.count + 1"},
            },
            2,
        )

        self.assertFalse(first["result"]["isError"])
        self.assertEqual(second["result"]["structuredContent"]["result"]["value"], 42)

    def test_tools_call_unknown_tool_and_invalid_params_are_tool_errors(self):
        unknown = self.request(
            "tools/call", {"name": "missing", "arguments": {"code": "1"}}
        )
        invalid = self.request(
            "tools/call", {"name": "pyrun_eval", "arguments": {"code": 1}}
        )

        self.assertTrue(unknown["result"]["isError"])
        self.assertIn("unknown tool", unknown["result"]["content"][0]["text"])
        self.assertTrue(invalid["result"]["isError"])
        self.assertIn("code must be a string", invalid["result"]["content"][0]["text"])

    def test_initialized_notification_returns_no_response(self):
        self.assertIsNone(
            self.server.handle(
                {"jsonrpc": "2.0", "method": "notifications/initialized"}
            )
        )

    def test_default_server_store_auto_approves_side_effects(self):
        from pyrun.mcp import McpServer

        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp, "note.txt")
            server = McpServer()
            response = server.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {
                        "name": "pyrun_eval",
                        "arguments": {"code": f"fs.write({str(target)!r}, 'hello')"},
                    },
                }
            )

            result = response["result"]["structuredContent"]["result"]
            self.assertEqual(result["type"], "completed")
            self.assertEqual(target.read_text(), "hello")


class McpFramingTests(unittest.TestCase):
    def test_content_length_frame_round_trip(self):
        from pyrun.mcp import decode_frames, encode_frame

        messages = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
        ]
        stream = b"".join(encode_frame(message) for message in messages)

        self.assertEqual(decode_frames(stream), messages)


if __name__ == "__main__":
    unittest.main()
