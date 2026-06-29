import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
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

    def test_fs_write_and_open_json_jsonl_csv_tsv_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.eval(f"host.cd({tmp!r})")
            self.eval("fs.write_json('data.json', {'b': 2, 'a': 1})")
            self.eval("fs.write_json_lines('items.jsonl', [{'id': 1}, {'id': 2}])")
            self.eval("fs.write_csv('dicts.csv', [{'a': '1'}, {'b': '2', 'a': '3'}])")
            self.eval("fs.write_tsv('rows.tsv', [['name', 'count'], ['apples', 2]])")
            self.eval("fs.write('note.txt', 'plain text')")
            json_text = Path(tmp, "data.json").read_text()
            opened = self.eval("[fs.open('data.json'), fs.open('items.jsonl'), fs.open('dicts.csv'), fs.open('rows.tsv'), fs.open('note.txt')]")["value"]

        self.assertEqual(json_text, '{\n  "b": 2,\n  "a": 1\n}\n')
        self.assertEqual(opened[0], {"b": 2, "a": 1})
        self.assertEqual(opened[1], [{"id": 1}, {"id": 2}])
        self.assertEqual(opened[2], [{"a": "1", "b": ""}, {"a": "3", "b": "2"}])
        self.assertEqual(opened[3], [{"name": "apples", "count": "2"}])
        self.assertEqual(opened[4], "plain text")

    def test_fs_write_jsonl_alias_and_list_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.eval(f"host.cd({tmp!r})")
            self.eval("fs.write_jsonl('alias.jsonl', [1, {'two': 2}])")
            self.eval("fs.write_csv('rows.csv', [['a', 'b'], [1, 2]])")
            jsonl_text = Path(tmp, "alias.jsonl").read_text()
            csv_text = Path(tmp, "rows.csv").read_text()

        self.assertEqual(jsonl_text, '1\n{"two": 2}\n')
        self.assertEqual(csv_text, 'a,b\n1,2\n')

    def test_fs_open_rejects_unsupported_format_clearly(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.eval(f"host.cd({tmp!r})")
            self.eval("fs.write('config.yaml', 'a: 1')")
            result = self.eval("fs.open('config.yaml')")

        self.assertEqual(result["type"], "error")
        self.assertIn("Unsupported file format", result["error"])

    def test_tools_file_replace_requires_single_match_and_supports_all_and_occurrence(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.eval(f"host.cd({tmp!r})")
            self.eval("fs.write('note.txt', 'one two one two one')")
            default_error = self.eval("tools.file.replace('note.txt', 'one', 'ONE')")
            second = self.eval("tools.file.replace('note.txt', {'from': 'one', 'to': 'ONE', 'occurrence': 2})")["value"]
            after_second = Path(tmp, "note.txt").read_text()
            all_result = self.eval("tools.file.replace('note.txt', {'from': 'two', 'to': 'TWO', 'all': True})")["value"]
            after_all = Path(tmp, "note.txt").read_text()

        self.assertEqual(default_error["type"], "error")
        self.assertIn("expected exactly one match", default_error["error"])
        self.assertEqual(second, {"replacements": 1})
        self.assertEqual(after_second, "one two ONE two one")
        self.assertEqual(all_result, {"replacements": 2})
        self.assertEqual(after_all, "one TWO ONE TWO one")

    def test_tmp_file_and_dir_handles_write_and_cleanup(self):
        result = self.eval("""
f = tmp.file(prefix='pyrun-', suffix='.json')
d = tmp.dir(prefix='pyrun-dir-')
f.write_json({'ok': True})
file_exists_before = fs.exists(str(f))
dir_exists_before = fs.exists(str(d))
contents = fs.open(str(f))
f.cleanup()
d.cleanup()
[file_exists_before, dir_exists_before, fs.exists(str(f)), fs.exists(str(d)), contents, repr(f).startswith('TmpFile('), repr(d).startswith('TmpDir(')]
""")["value"]

        self.assertEqual(result, [True, True, False, False, {"ok": True}, True, True])

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


class LocalHttpHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/json":
            self.send_response(201)
            self.send_header("Content-Type", "application/json")
            self.send_header("X-Test", "yes")
            self.end_headers()
            self.wfile.write(b'{"ok": true}')
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"hello")

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length).decode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        payload = {"content_type": self.headers.get("Content-Type"), "body": body}
        self.wfile.write(json.dumps(payload).encode())

    def log_message(self, format, *args):
        return


class HttpTests(unittest.TestCase):
    def setUp(self):
        self.store = SessionStore()
        self.server = HTTPServer(("127.0.0.1", 0), LocalHttpHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.thread.join(timeout=5)
        self.server.server_close()

    def eval(self, code, session_id="default"):
        return self.store.evaluate(code, session_id=session_id)

    def test_http_get_run_text_json_bytes_and_headers(self):
        response = self.eval(f"http.get({self.base_url + '/json'!r}).run()") ["value"]
        text = self.eval(f"http.get({self.base_url + '/text'!r}).text()") ["value"]
        data = self.eval(f"http.get({self.base_url + '/json'!r}).json()") ["value"]
        body_bytes = self.eval(f"http.get({self.base_url + '/text'!r}).bytes()") ["value"]

        self.assertEqual(response["status"], 201)
        self.assertEqual(response["headers"]["X-Test"], "yes")
        self.assertEqual(response["body"], [123, 34, 111, 107, 34, 58, 32, 116, 114, 117, 101, 125])
        self.assertEqual(text, "hello")
        self.assertEqual(data, {"ok": True})
        self.assertEqual(body_bytes, [104, 101, 108, 108, 111])

    def test_http_post_json_form_body_and_to_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp, "download.txt")
            json_echo = self.eval(f"http.post({self.base_url + '/echo'!r}, {{'json': {{'a': 1}}}}).json()") ["value"]
            form_echo = self.eval(f"http.post({self.base_url + '/echo'!r}, {{'form': {{'a': '1', 'b': 'two'}}}}).json()") ["value"]
            body_echo = self.eval(f"http.request('POST', {self.base_url + '/echo'!r}, {{'headers': {{'Content-Type': 'text/plain'}}, 'body': 'raw'}}).json()") ["value"]
            to_file = self.eval(f"http.get({self.base_url + '/text'!r}).to_file({str(target)!r})") ["value"]
            saved = target.read_text()

        self.assertEqual(json_echo, {"content_type": "application/json", "body": '{"a": 1}'})
        self.assertEqual(form_echo, {"content_type": "application/x-www-form-urlencoded", "body": "a=1&b=two"})
        self.assertEqual(body_echo, {"content_type": "text/plain", "body": "raw"})
        self.assertEqual(to_file, str(target))
        self.assertEqual(saved, "hello")


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
