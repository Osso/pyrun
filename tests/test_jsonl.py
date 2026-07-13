import io
import json
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from pyrun.jsonl import handle_line
from pyrun.runtime import SessionStore, StreamingConsole


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
        result = self.handle(
            json.dumps(
                {
                    "code": """
[
    cli.python3('-c', 'print(123)'),
    cli.python3('-c', 'print(123)').stdout_stream(),
    run.python3('-c', 'print(123)'),
    http.get('http://example.invalid'),
    {'wrapped': hr({'a': 1}).select('a')},
]
"""
                }
            )
        )

        self.assertEqual(result["type"], "completed")
        json.dumps(result)
        builder, stream, command_result, http_builder, wrapped = result["value"]
        self.assertEqual(builder["program"], "python3")
        self.assertEqual(stream["stream"], "stdout")
        self.assertEqual(command_result, 0)
        self.assertEqual(http_builder["url"], "http://example.invalid")
        self.assertEqual(wrapped, {"wrapped": {"a": 1}})

    def test_jsonl_store_defaults_to_auto_approve_for_side_effects(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp, "note.txt")
            result = self.handle(
                json.dumps({"code": f"fs.write({str(target)!r}, 'hello')"})
            )

            self.assertEqual(result["type"], "completed")
            self.assertEqual(target.read_text(), "hello")

    def test_pi_footer_snapshot_uses_provided_snapshot(self):
        result = self.handle(
            json.dumps(
                {
                    "code": "pi.footer.snapshot()",
                    "pi": {"footer": {"cwd": "/repo", "model": "test/model"}},
                    "pi_bridge": True,
                }
            )
        )

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

    def test_complete_lines_stream_without_explicit_flush(self):
        protocol = io.StringIO()

        result = handle_line(
            self.store,
            json.dumps({"code": "print('line')", "stream_console": True}),
            1,
            stdout=protocol,
        )

        self.assertEqual(
            json.loads(protocol.getvalue()),
            {"type": "console", "stream": "stdout", "text": "line\n"},
        )
        self.assertEqual(result["console"], ["line"])

    def test_write_streams_complete_lines_and_buffers_trailing_partial_text(self):
        events = []
        console = StreamingConsole(
            "stdout",
            lambda stream, text: events.append((stream, text)),
        )

        console.write("line 1\nline 2\npartial")

        self.assertEqual(events, [("stdout", "line 1\n"), ("stdout", "line 2\n")])

        console.flush()

        self.assertEqual(
            events,
            [("stdout", "line 1\n"), ("stdout", "line 2\n"), ("stdout", "partial")],
        )

    def test_final_history_preserves_non_newline_separators_inside_live_event(self):
        protocol = io.StringIO()

        result = handle_line(
            self.store,
            json.dumps(
                {
                    "code": "import sys\nsys.stdout.write('left\\rright\\n')",
                    "stream_console": True,
                }
            ),
            1,
            stdout=protocol,
        )

        self.assertEqual(
            json.loads(protocol.getvalue()),
            {"type": "console", "stream": "stdout", "text": "left\rright\n"},
        )
        self.assertEqual(result["console"], ["left\rright"])

    def test_explicit_flush_streams_partial_text(self):
        protocol = io.StringIO()

        handle_line(
            self.store,
            json.dumps(
                {
                    "code": "import sys\nsys.stdout.write('partial')\nsys.stdout.flush()",
                    "stream_console": True,
                }
            ),
            1,
            stdout=protocol,
        )

        self.assertEqual(
            json.loads(protocol.getvalue()),
            {"type": "console", "stream": "stdout", "text": "partial"},
        )

    def test_evaluation_completion_streams_partial_final_line(self):
        protocol = io.StringIO()

        handle_line(
            self.store,
            json.dumps(
                {
                    "code": "import sys\nsys.stdout.write('partial final')",
                    "stream_console": True,
                }
            ),
            1,
            stdout=protocol,
        )

        self.assertEqual(
            json.loads(protocol.getvalue()),
            {"type": "console", "stream": "stdout", "text": "partial final"},
        )

    def test_stderr_and_stdout_console_events_preserve_order(self):
        protocol = io.StringIO()

        handle_line(
            self.store,
            json.dumps(
                {
                    "code": (
                        "import sys\n"
                        "print('out 1')\n"
                        "print('err 1', file=sys.stderr)\n"
                        "print('out 2')"
                    ),
                    "stream_console": True,
                }
            ),
            1,
            stdout=protocol,
        )

        messages = [json.loads(line) for line in protocol.getvalue().splitlines()]
        self.assertEqual(
            messages,
            [
                {"type": "console", "stream": "stdout", "text": "out 1\n"},
                {"type": "console", "stream": "stderr", "text": "err 1\n"},
                {"type": "console", "stream": "stdout", "text": "out 2\n"},
            ],
        )

    def test_completion_preserves_order_of_partial_stderr_and_stdout(self):
        protocol = io.StringIO()

        result = handle_line(
            self.store,
            json.dumps(
                {
                    "code": (
                        "import sys\n"
                        "sys.stderr.write('err partial')\n"
                        "sys.stdout.write('out partial')"
                    ),
                    "stream_console": True,
                }
            ),
            1,
            stdout=protocol,
        )

        messages = [json.loads(line) for line in protocol.getvalue().splitlines()]
        self.assertEqual(
            messages,
            [
                {"type": "console", "stream": "stderr", "text": "err partial"},
                {"type": "console", "stream": "stdout", "text": "out partial"},
            ],
        )
        self.assertEqual(result["console"], ["err partial", "out partial"])

    def test_exception_preserves_partial_stderr_and_stdout_order(self):
        protocol = io.StringIO()

        result = handle_line(
            self.store,
            json.dumps(
                {
                    "code": (
                        "import sys\n"
                        "sys.stderr.write('err partial')\n"
                        "sys.stdout.write('out partial')\n"
                        "raise RuntimeError('boom')"
                    ),
                    "stream_console": True,
                }
            ),
            1,
            stdout=protocol,
        )

        messages = [json.loads(line) for line in protocol.getvalue().splitlines()]
        self.assertEqual(
            messages,
            [
                {"type": "console", "stream": "stderr", "text": "err partial"},
                {"type": "console", "stream": "stdout", "text": "out partial"},
            ],
        )
        self.assertEqual(result["type"], "error")
        self.assertEqual(result["console"], ["err partial", "out partial"])

    def test_interleaved_partial_flush_exception_keeps_protocol_outside_console(self):
        protocol = io.StringIO()

        result = handle_line(
            self.store,
            json.dumps(
                {
                    "code": (
                        "import sys\n"
                        "sys.stdout.write('out partial')\n"
                        "sys.stderr.write('err complete\\n')\n"
                        "sys.stdout.flush()\n"
                        "raise RuntimeError('boom')"
                    ),
                    "stream_console": True,
                }
            ),
            1,
            stdout=protocol,
        )

        messages = [json.loads(line) for line in protocol.getvalue().splitlines()]
        self.assertEqual(
            messages,
            [
                {"type": "console", "stream": "stderr", "text": "err complete\n"},
                {"type": "console", "stream": "stdout", "text": "out partial"},
            ],
        )
        self.assertEqual(result["type"], "error")
        self.assertEqual(result["console"], ["err complete", "out partial"])
        self.assertNotIn('"type": "console"', "\n".join(result["console"]))

    def test_exception_streams_partial_text_before_error_result(self):
        protocol = io.StringIO()

        result = handle_line(
            self.store,
            json.dumps(
                {
                    "code": "import sys\nsys.stderr.write('before error')\nraise RuntimeError('boom')",
                    "stream_console": True,
                }
            ),
            1,
            stdout=protocol,
        )

        self.assertEqual(
            json.loads(protocol.getvalue()),
            {"type": "console", "stream": "stderr", "text": "before error"},
        )
        self.assertEqual(result["type"], "error")
        self.assertEqual(result["console"], ["before error"])

    def test_print_sleep_print_streams_first_line_before_sleep_completes(self):
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
            process.stdin.write(
                json.dumps(
                    {
                        "code": "import time\nprint('start')\ntime.sleep(1)\nprint('end')",
                        "stream_console": True,
                    }
                )
                + "\n"
            )
            process.stdin.flush()
            started_at = time.monotonic()

            first = json.loads(process.stdout.readline())
            first_elapsed = time.monotonic() - started_at
            second = json.loads(process.stdout.readline())
            second_elapsed = time.monotonic() - started_at
            result = json.loads(process.stdout.readline())

            self.assertEqual(first, {"type": "console", "stream": "stdout", "text": "start\n"})
            self.assertGreater(second_elapsed - first_elapsed, 0.5)
            self.assertEqual(second, {"type": "console", "stream": "stdout", "text": "end\n"})
            self.assertEqual(result["type"], "completed")
            self.assertEqual(result["console"], ["start", "end"])
        finally:
            process.stdin.close()
            process.stdout.close()
            if process.stderr is not None:
                process.stderr.close()
            process.terminate()
            process.wait(timeout=5)

    def test_pi_agent_selection_requests_roundtrip_through_jsonl_subprocess(self):
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
            process.stdin.write(
                json.dumps(
                    {
                        "code": "[pi.agents.current(), pi.agents.select('agent-1'), pi.messages.last()]",
                        "pi": {"footer": {"cwd": "/repo"}},
                        "pi_bridge": True,
                    }
                )
                + "\n"
            )
            process.stdin.flush()

            current_request = json.loads(process.stdout.readline())
            self.assertEqual(
                current_request,
                {
                    "type": "pi_request",
                    "method": "agents.current",
                    "params": None,
                },
            )
            process.stdin.write(
                json.dumps({"result": {"id": "main", "displayName": "Main thread"}})
                + "\n"
            )
            process.stdin.flush()

            select_request = json.loads(process.stdout.readline())
            self.assertEqual(
                select_request,
                {
                    "type": "pi_request",
                    "method": "agents.select",
                    "params": {"agentId": "agent-1"},
                },
            )
            process.stdin.write(
                json.dumps({"result": {"id": "agent-1", "displayName": "Scout"}}) + "\n"
            )
            process.stdin.flush()

            last_message_request = json.loads(process.stdout.readline())
            self.assertEqual(
                last_message_request,
                {
                    "type": "pi_request",
                    "method": "messages.last",
                    "params": None,
                },
            )
            process.stdin.write(
                json.dumps(
                    {
                        "result": {
                            "entryId": "entry-1",
                            "role": "assistant",
                            "text": "done",
                        }
                    }
                )
                + "\n"
            )
            process.stdin.flush()
            result = json.loads(process.stdout.readline())

            self.assertEqual(result["type"], "completed")
            self.assertEqual(
                result["value"],
                [
                    {"id": "main", "displayName": "Main thread"},
                    {"id": "agent-1", "displayName": "Scout"},
                    {"entryId": "entry-1", "role": "assistant", "text": "done"},
                ],
            )
        finally:
            process.stdin.close()
            process.stdout.close()
            if process.stderr is not None:
                process.stderr.close()
            process.terminate()
            process.wait(timeout=5)

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
            process.stdin.write(
                json.dumps(
                    {
                        "code": "pi.agents.wait('agent-1')",
                        "pi": {"footer": {"cwd": "/repo"}},
                        "pi_bridge": True,
                    }
                )
                + "\n"
            )
            process.stdin.flush()

            request = json.loads(process.stdout.readline())
            self.assertEqual(
                request,
                {
                    "type": "pi_request",
                    "method": "agents.wait",
                    "params": {"agentId": "agent-1"},
                },
            )

            process.stdin.write(
                json.dumps({"result": {"id": "agent-1", "status": "done"}}) + "\n"
            )
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
            process.stdin.write(
                json.dumps({"code": "pi.compact()", "pi": {}, "pi_bridge": True}) + "\n"
            )
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

    def test_pi_models_scoped_request_round_trips(self):
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
            process.stdin.write(
                json.dumps(
                    {"code": "pi.models.scoped()", "pi": {}, "pi_bridge": True}
                )
                + "\n"
            )
            process.stdin.flush()

            request = json.loads(process.stdout.readline())
            self.assertEqual(
                request,
                {
                    "type": "pi_request",
                    "method": "models.scoped",
                    "params": None,
                },
            )

            process.stdin.write(
                json.dumps(
                    {
                        "result": [
                            {
                                "provider": "openai",
                                "id": "gpt-5.5",
                                "name": "GPT-5.5",
                                "thinkingLevel": "high",
                            }
                        ]
                    }
                )
                + "\n"
            )
            process.stdin.flush()
            result = json.loads(process.stdout.readline())

            self.assertEqual(result["type"], "completed")
            self.assertEqual(
                result["value"],
                [
                    {
                        "provider": "openai",
                        "id": "gpt-5.5",
                        "name": "GPT-5.5",
                        "thinkingLevel": "high",
                    }
                ],
            )
        finally:
            process.stdin.close()
            process.stdout.close()
            if process.stderr is not None:
                process.stderr.close()
            process.terminate()
            process.wait(timeout=5)

    def test_pi_web_search_request_round_trips(self):
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
            process.stdin.write(
                json.dumps(
                    {"code": "pi.web_search('current Pi release')", "pi": {}, "pi_bridge": True}
                )
                + "\n"
            )
            process.stdin.flush()

            request = json.loads(process.stdout.readline())
            self.assertEqual(
                request,
                {
                    "type": "pi_request",
                    "method": "tools.call",
                    "params": {"name": "web_search", "params": {"query": "current Pi release"}},
                },
            )

            process.stdin.write(json.dumps({"result": {"text": "Pi release notes"}}) + "\n")
            process.stdin.flush()
            result = json.loads(process.stdout.readline())

            self.assertEqual(result["type"], "completed")
            self.assertEqual(result["value"], {"text": "Pi release notes"})
        finally:
            process.stdin.close()
            process.stdout.close()
            if process.stderr is not None:
                process.stderr.close()
            process.terminate()
            process.wait(timeout=5)

    def test_pi_commands_requests_round_trip(self):
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
            process.stdin.write(
                json.dumps(
                    {"code": "[pi.commands.list(), pi.commands.run('usage', 'reset')]", "pi": {}, "pi_bridge": True}
                )
                + "\n"
            )
            process.stdin.flush()

            list_request = json.loads(process.stdout.readline())
            self.assertEqual(
                list_request,
                {"type": "pi_request", "method": "commands.list", "params": None},
            )
            process.stdin.write(json.dumps({"result": [{"name": "usage"}]}) + "\n")
            process.stdin.flush()

            run_request = json.loads(process.stdout.readline())
            self.assertEqual(
                run_request,
                {
                    "type": "pi_request",
                    "method": "commands.run",
                    "params": {"name": "usage", "args": "reset"},
                },
            )
            process.stdin.write(json.dumps({"result": {"displayed": True}}) + "\n")
            process.stdin.flush()
            result = json.loads(process.stdout.readline())

            self.assertEqual(result["type"], "completed")
            self.assertEqual(result["value"], [[{"name": "usage"}], {"displayed": True}])
        finally:
            process.stdin.close()
            process.stdout.close()
            if process.stderr is not None:
                process.stderr.close()
            process.terminate()
            process.wait(timeout=5)


if __name__ == "__main__":
    unittest.main()
