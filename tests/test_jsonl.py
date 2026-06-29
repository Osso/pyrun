import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from pyrun.jsonl import handle_line
from pyrun.runtime import SessionStore


class JsonlProtocolTests(unittest.TestCase):
    def setUp(self):
        self.store = SessionStore()

    def handle(self, line):
        return handle_line(self.store, line, 7)

    def test_invalid_json_reports_line_number(self):
        response = self.handle("not-json")

        self.assertEqual(response["type"], "error")
        self.assertEqual(response["executed"], "")
        self.assertIn("line 7: invalid JSON", response["error"])

    def test_non_object_request_is_rejected(self):
        response = self.handle(json.dumps([{"code": "1"}]))

        self.assertEqual(response["type"], "error")
        self.assertIn("request must be an object", response["error"])

    def test_missing_or_non_string_code_is_rejected(self):
        missing = self.handle(json.dumps({"session_id": "s"}))
        wrong_type = self.handle(json.dumps({"code": 1}))

        self.assertIn("code must be a string", missing["error"])
        self.assertIn("code must be a string", wrong_type["error"])

    def test_non_string_session_id_is_rejected(self):
        response = self.handle(json.dumps({"session_id": 1, "code": "1 + 1"}))

        self.assertEqual(response["executed"], "1 + 1")
        self.assertIn("session_id must be a string", response["error"])

    def test_helper_outputs_are_json_serializable(self):
        result = self.handle(json.dumps({
            "code": """
[
    cli.python3('-c', 'print(123)'),
    cli.python3('-c', 'print(123)').stdout_stream(),
    run.python3('-c', 'print(123)'),
    http.get('http://example.invalid'),
    {'wrapped': hr({'a': 1}).select('a')},
]
"""
        }))

        self.assertEqual(result["type"], "completed")
        json.dumps(result)
        builder, stream, command_result, http_builder, wrapped = result["value"]
        self.assertEqual(builder["program"], "python3")
        self.assertEqual(stream["stream"], "stdout")
        self.assertEqual(command_result["stdout"], "123\n")
        self.assertEqual(http_builder["url"], "http://example.invalid")
        self.assertEqual(wrapped, {"wrapped": {"a": 1}})

    def test_jsonl_store_defaults_to_auto_approve_for_side_effects(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp, "note.txt")
            result = self.handle(json.dumps({"code": f"fs.write({str(target)!r}, 'hello')"}))

            self.assertEqual(result["type"], "completed")
            self.assertEqual(target.read_text(), "hello")

    def test_pi_footer_snapshot_uses_provided_snapshot(self):
        result = self.handle(json.dumps({
            "code": "pi.footer.snapshot()",
            "pi": {"footer": {"cwd": "/repo", "model": "test/model"}},
            "pi_bridge": True,
        }))

        self.assertEqual(result["type"], "error")
        self.assertIn("pi_bridge requires a Pi request handler", result["error"])

        handled = []
        result = self.store.evaluate(
            "pi.footer.snapshot()",
            pi={"footer": {"cwd": "/repo", "model": "test/model"}},
            pi_bridge=True,
            pi_request_handler=lambda method, params: handled.append((method, params)),
        )

        self.assertEqual(result["type"], "completed")
        self.assertEqual(result["value"], {"cwd": "/repo", "model": "test/model"})
        self.assertEqual(handled, [])

    def test_pi_is_absent_without_bridge(self):
        result = self.handle(json.dumps({"code": "pi.footer.snapshot()"}))

        self.assertEqual(result["type"], "error")
        self.assertIn("name 'pi' is not defined", result["error"])

    def test_pi_request_roundtrip_through_jsonl_subprocess(self):
        process = subprocess.Popen(
            [sys.executable, "-m", "pyrun.jsonl"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        assert process.stdin is not None
        assert process.stdout is not None
        try:
            process.stdin.write(json.dumps({
                "code": "pi.agents.wait('agent-1')",
                "pi": {"footer": {"cwd": "/repo"}},
                "pi_bridge": True,
            }) + "\n")
            process.stdin.flush()

            request = json.loads(process.stdout.readline())
            self.assertEqual(request, {
                "type": "pi_request",
                "method": "agents.wait",
                "params": {"agentId": "agent-1"},
            })

            process.stdin.write(json.dumps({"result": {"id": "agent-1", "status": "done"}}) + "\n")
            process.stdin.flush()
            result = json.loads(process.stdout.readline())

            self.assertEqual(result["type"], "completed")
            self.assertEqual(result["value"], {"id": "agent-1", "status": "done"})
        finally:
            process.stdin.close()
            process.stdout.close()
            if process.stderr is not None:
                process.stderr.close()
            process.terminate()
            process.wait(timeout=5)

    def test_pi_request_error_response_becomes_eval_error(self):
        process = subprocess.Popen(
            [sys.executable, "-m", "pyrun.jsonl"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        assert process.stdin is not None
        assert process.stdout is not None
        try:
            process.stdin.write(json.dumps({"code": "pi.compact()", "pi": {}, "pi_bridge": True}) + "\n")
            process.stdin.flush()
            self.assertEqual(json.loads(process.stdout.readline())["method"], "compact")

            process.stdin.write(json.dumps({"error": "denied"}) + "\n")
            process.stdin.flush()
            result = json.loads(process.stdout.readline())

            self.assertEqual(result["type"], "error")
            self.assertIn("denied", result["error"])
        finally:
            process.stdin.close()
            process.stdout.close()
            if process.stderr is not None:
                process.stderr.close()
            process.terminate()
            process.wait(timeout=5)


if __name__ == "__main__":
    unittest.main()
