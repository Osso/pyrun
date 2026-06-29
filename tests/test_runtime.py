import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

from pyrun.runtime import CommandBuilder, Session, SessionStore


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.store = SessionStore()

    def eval(self, code, session_id="default"):
        return self.store.evaluate(code, session_id=session_id)

    def test_expression_eval_returns_value(self):
        result = self.eval("1 + 2")
        self.assertEqual(result["type"], "completed")
        self.assertEqual(result["value"], 3)

    def test_ctx_persists_and_supports_attribute_access(self):
        first = self.eval("ctx.count = 41")
        second = self.eval("ctx.count += 1\nctx.count")
        third = self.eval("ctx['count']")

        self.assertEqual(first["type"], "completed")
        self.assertEqual(second["value"], 42)
        self.assertEqual(third["value"], 42)

    def test_host_cwd_and_cd_are_session_local(self):
        with tempfile.TemporaryDirectory() as tmp:
            original = self.eval("host.cwd()", session_id="a")["value"]
            changed = self.eval(f"host.cd({tmp!r})\nhost.cwd()", session_id="a")["value"]
            other = self.eval("host.cwd()", session_id="b")["value"]

        self.assertNotEqual(original, changed)
        self.assertEqual(changed, tmp)
        self.assertEqual(other, os.getcwd())

    def test_fs_read_write_exists_remove_and_glob_use_session_cwd(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.eval(f"host.cd({tmp!r})")
            self.eval("fs.write('note.txt', 'hello')")
            exists = self.eval("fs.exists('note.txt')")["value"]
            content = self.eval("fs.read('note.txt')")["value"]
            matches = self.eval("fs.glob('*.txt')")["value"]
            removed = self.eval("fs.remove('note.txt')")["value"]
            missing = self.eval("fs.exists('note.txt')")["value"]

        self.assertTrue(exists)
        self.assertEqual(content, "hello")
        self.assertEqual(matches, ["note.txt"])
        self.assertTrue(removed)
        self.assertFalse(missing)

    def test_cli_builder_and_run_execute_without_shell(self):
        code = "cli.python3('-c', 'print(123)').run()"
        result = self.eval(code)["value"]
        text = self.eval("run.python3('-c', 'print(456)').text()")["value"]

        self.assertEqual(result["stdout"], "123\n")
        self.assertEqual(result["stderr"], "")
        self.assertEqual(result["exit_code"], 0)
        self.assertEqual(text, "456\n")

    def test_command_result_helpers(self):
        lines = self.eval("run.python3('-c', 'print(1); print(2)').lines()")["value"]
        data = self.eval("run.python3('-c', 'import json; print(json.dumps({\"a\": 1}))').json()")["value"]

        self.assertEqual(lines, ["1", "2"])
        self.assertEqual(data, {"a": 1})

    def test_command_builder_in_runs_command_in_provided_cwd(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = CommandBuilder(Session(), sys.executable)(
                "-c",
                "import os; print(os.getcwd())",
            ).in_(tmp).run()

        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.stderr, "")
        self.assertEqual(result.stdout, f"{tmp}\n")

    def test_command_builder_stdin_text_passes_stdin_to_command(self):
        result = CommandBuilder(Session(), sys.executable)(
            "-c",
            "import sys; sys.stdout.write(sys.stdin.read().upper())",
        ).stdin_text("hello from stdin").run()

        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.stderr, "")
        self.assertEqual(result.stdout, "HELLO FROM STDIN")

    def test_print_output_is_captured_as_console(self):
        result = self.eval("print('hello')\n7")

        self.assertEqual(result["value"], 7)
        self.assertEqual(result["console"], ["hello"])

    def test_error_shape(self):
        result = self.eval("1 / 0")

        self.assertEqual(result["type"], "error")
        self.assertEqual(result["executed"], "1 / 0")
        self.assertIn("division by zero", result["error"])


class JsonlRunnerTests(unittest.TestCase):
    def run_jsonl(self, requests):
        env = dict(os.environ)
        proc = subprocess.run(
            [sys.executable, "-m", "pyrun.jsonl"],
            input="".join(json.dumps(item) + "\n" for item in requests),
            text=True,
            capture_output=True,
            env=env,
            cwd=ROOT,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return [json.loads(line) for line in proc.stdout.splitlines()]

    def test_jsonl_persists_session_and_defaults_session_id(self):
        responses = self.run_jsonl([
            {"session_id": "s", "code": "ctx.count = 10"},
            {"session_id": "s", "code": "ctx.count + 5"},
            {"code": "ctx.value = 'default'"},
            {"code": "ctx.value"},
        ])

        self.assertEqual([item["type"] for item in responses], ["completed"] * 4)
        self.assertEqual(responses[1]["value"], 15)
        self.assertEqual(responses[3]["value"], "default")

    def test_jsonl_reports_errors(self):
        responses = self.run_jsonl([{"code": "missing_name"}])

        self.assertEqual(responses[0]["type"], "error")
        self.assertEqual(responses[0]["executed"], "missing_name")
        self.assertIn("missing_name", responses[0]["error"])


if __name__ == "__main__":
    unittest.main()
