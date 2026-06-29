import json
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


if __name__ == "__main__":
    unittest.main()
