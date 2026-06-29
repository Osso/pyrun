import json
import os
import shutil
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

    def test_tools_file_patch_explicit_path_with_hunk(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.eval(f"host.cd({tmp!r})")
            Path(tmp, "note.txt").write_text("one\ntwo\nthree\n")
            patch = """@@ -1,3 +1,3 @@
 one
-two
+TWO
 three
"""
            result = self.eval(f"tools.file.patch('note.txt', {patch!r})")["value"]
            text = Path(tmp, "note.txt").read_text()

        self.assertEqual(result, [{"path": str(Path(tmp, "note.txt")), "hunks": 1}])
        self.assertEqual(text, "one\nTWO\nthree\n")

    def test_tools_file_patch_multi_file_diff_with_ab_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.eval(f"host.cd({tmp!r})")
            Path(tmp, "alpha.txt").write_text("a\nb\nc\n")
            Path(tmp, "beta.txt").write_text("x\ny\nz\n")
            patch = """--- a/alpha.txt
+++ b/alpha.txt
@@ -1,3 +1,3 @@
 a
-b
+B
 c
--- a/beta.txt
+++ b/beta.txt
@@ -1,3 +1,3 @@
 x
-y
+Y
 z
"""
            result = self.eval(f"tools.file.patch({patch!r})")["value"]
            alpha_text = Path(tmp, "alpha.txt").read_text()
            beta_text = Path(tmp, "beta.txt").read_text()

        self.assertEqual(result, [
            {"path": str(Path(tmp, "alpha.txt")), "hunks": 1},
            {"path": str(Path(tmp, "beta.txt")), "hunks": 1},
        ])
        self.assertEqual(alpha_text, "a\nB\nc\n")
        self.assertEqual(beta_text, "x\nY\nz\n")

    def test_tools_file_patch_creates_new_file_from_dev_null(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.eval(f"host.cd({tmp!r})")
            patch = """--- /dev/null
+++ b/new.txt
@@ -0,0 +1,2 @@
+hello
+world
"""
            result = self.eval(f"tools.file.patch({patch!r})")["value"]
            text = Path(tmp, "new.txt").read_text()

        self.assertEqual(result, [{"path": str(Path(tmp, "new.txt")), "hunks": 1}])
        self.assertEqual(text, "hello\nworld\n")

    def test_tools_file_patch_context_mismatch_returns_error_shape(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.eval(f"host.cd({tmp!r})")
            Path(tmp, "note.txt").write_text("one\nwrong\nthree\n")
            patch = """@@ -1,3 +1,3 @@
 one
-two
+TWO
 three
"""
            result = self.eval(f"tools.file.patch('note.txt', {patch!r})")

        self.assertEqual(result["type"], "error")
        self.assertIn("patch mismatch", result["error"])
        self.assertIn("note.txt", result["error"])

    def test_tools_file_patch_rejects_deletion_diff(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.eval(f"host.cd({tmp!r})")
            Path(tmp, "note.txt").write_text("delete me\n")
            patch = """--- a/note.txt
+++ /dev/null
@@ -1 +0,0 @@
-delete me
"""
            result = self.eval(f"tools.file.patch({patch!r})")

        self.assertEqual(result["type"], "error")
        self.assertIn("deletion is not supported", result["error"])

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

    def test_command_builder_env_override_is_visible_to_child(self):
        result = CommandBuilder(Session(), sys.executable)(
            "-c",
            "import os; print(os.environ['PYRUN_TEST_ENV'])",
        ).env("PYRUN_TEST_ENV", "visible").run()
        inherited = CommandBuilder(Session(), sys.executable)(
            "-c",
            "import os; print(os.environ['PATH'] != '')",
        ).env({"PYRUN_TEST_ENV": "dict-value"}).run()

        self.assertEqual(result.stdout, "visible\n")
        self.assertEqual(result.stderr, "")
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(inherited.stdout, "True\n")

    def test_command_builder_stdin_file_json_lines_csv_and_tsv(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = Session(cwd=Path(tmp))
            Path(tmp, "input.txt").write_text("from file")
            file_result = CommandBuilder(session, sys.executable)("-c", "import sys; print(sys.stdin.read())").stdin_file("input.txt").text()
            json_result = CommandBuilder(session, sys.executable)("-c", "import json, sys; print(json.load(sys.stdin)['ok'])").stdin_json({"ok": True}).text()
            lines_result = CommandBuilder(session, sys.executable)("-c", "import sys; print(repr(sys.stdin.read()))").stdin_lines(["a", "b"]).text()
            csv_result = CommandBuilder(session, sys.executable)("-c", "import sys; print(repr(sys.stdin.read()))").stdin_csv([{"a": 1}, {"b": 2, "a": 3}]).text()
            tsv_result = CommandBuilder(session, sys.executable)("-c", "import sys; print(repr(sys.stdin.read()))").stdin_tsv([["a", "b"], [1, 2]]).text()

        self.assertEqual(file_result, "from file\n")
        self.assertEqual(json_result, "True\n")
        self.assertEqual(lines_result, "'a\\nb\\n'\n")
        self.assertEqual(csv_result, "'a,b\\n1,\\n3,2\\n'\n")
        self.assertEqual(tsv_result, "'a\\tb\\n1\\t2\\n'\n")

    def test_command_builder_stderr_and_combined_helpers(self):
        builder = CommandBuilder(Session(), sys.executable)(
            "-c",
            "import sys; print('{\"out\": 1}', flush=True); print('{\"err\": 2}', file=sys.stderr)",
        )

        self.assertEqual(builder.stderr_text(), '{"err": 2}\n')
        self.assertEqual(builder.stderr_lines(), ['{"err": 2}'])
        self.assertEqual(builder.stderr_json(), {"err": 2})
        self.assertEqual(builder.combined_text(), '{"out": 1}\n{"err": 2}\n')

    def test_command_builder_output_redirects_and_tee(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = Session(cwd=Path(tmp))
            builder = CommandBuilder(session, sys.executable)(
                "-c",
                "import sys; print('out', flush=True); print('err', file=sys.stderr)",
            )
            stdout_result = builder.to_file("stdout.txt")
            stderr_result = builder.stderr_to_file("stderr.txt")
            combined_result = builder.combined_to_file("combined.txt")
            tee_result = builder.tee("tee.txt")
            stderr_tee_result = builder.stderr_tee("stderr-tee.txt")
            combined_tee_result = builder.combined_tee("combined-tee.txt")

            stdout_text = Path(tmp, "stdout.txt").read_text()
            stderr_text = Path(tmp, "stderr.txt").read_text()
            combined_text = Path(tmp, "combined.txt").read_text()
            tee_text = Path(tmp, "tee.txt").read_text()
            stderr_tee_text = Path(tmp, "stderr-tee.txt").read_text()
            combined_tee_text = Path(tmp, "combined-tee.txt").read_text()

        self.assertEqual(stdout_text, "out\n")
        self.assertEqual(stderr_text, "err\n")
        self.assertEqual(combined_text, "out\nerr\n")
        self.assertEqual(tee_text, "out\n")
        self.assertEqual(stderr_tee_text, "err\n")
        self.assertEqual(combined_tee_text, "out\nerr\n")
        self.assertEqual(stdout_result.stdout, "out\n")
        self.assertEqual(stderr_result.stderr, "err\n")
        self.assertEqual(combined_result.stdout, "out\nerr\n")
        self.assertEqual(tee_result.stderr, "err\n")
        self.assertEqual(stderr_tee_result.stdout, "out\n")
        self.assertEqual(combined_tee_result.stdout, "out\nerr\n")

    def test_command_builder_spawn_wait_text_json_and_kill(self):
        handle = CommandBuilder(Session(), sys.executable)(
            "-c",
            "import json; print(json.dumps({'ok': True}))",
        ).spawn()
        waited = handle.wait(timeout=5)
        text_handle = CommandBuilder(Session(), sys.executable)("-c", "print('a'); print('b')").spawn()
        json_handle = CommandBuilder(Session(), sys.executable)("-c", "import json; print(json.dumps({'n': 3}))").spawn()
        sleeper = CommandBuilder(Session(), sys.executable)("-c", "import time; time.sleep(30)").spawn()
        killed = sleeper.kill()
        killed_result = sleeper.wait(timeout=5)

        self.assertGreater(handle.pid, 0)
        self.assertEqual(handle.program, sys.executable)
        self.assertEqual(waited.stdout, '{"ok": true}\n')
        self.assertEqual(waited.exit_code, 0)
        self.assertEqual(text_handle.text(timeout=5), "a\nb\n")
        self.assertEqual(json_handle.json(timeout=5), {"n": 3})
        self.assertTrue(killed)
        self.assertNotEqual(killed_result.exit_code, 0)

    def test_command_builder_does_not_parse_shell_syntax(self):
        result = CommandBuilder(Session(), sys.executable)(
            "-c",
            "import sys; print(sys.argv[1])",
            "hello; echo hacked",
        ).run()

        self.assertEqual(result.stdout, "hello; echo hacked\n")
        self.assertEqual(result.stderr, "")
        self.assertEqual(result.exit_code, 0)

    def test_command_builder_stdin_from_builder_feeds_stdout_to_next_command(self):
        session = Session()
        producer = CommandBuilder(session, sys.executable)("-c", "print('hello stream')")
        consumer = CommandBuilder(session, sys.executable)(
            "-c",
            "import sys; sys.stdout.write(sys.stdin.read().upper())",
        )

        result = consumer.stdin_from(producer).run()

        self.assertEqual(result.stdout, "HELLO STREAM\n")
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(len(result.upstream_results), 1)
        self.assertEqual(result.upstream_results[0].stdout, "hello stream\n")

    def test_command_builder_stdin_from_stream_can_feed_stderr(self):
        session = Session()
        producer = CommandBuilder(session, sys.executable)(
            "-c",
            "import sys; print('ignored'); print('from stderr', file=sys.stderr)",
        )
        consumer = CommandBuilder(session, sys.executable)(
            "-c",
            "import sys; sys.stdout.write(sys.stdin.read().title())",
        )

        result = consumer.stdin_from(producer.stderr_stream()).run()

        self.assertEqual(result.stdout, "From Stderr\n")
        self.assertEqual(result.upstream_results[0].stderr, "from stderr\n")

    def test_command_builder_stdin_from_command_result_feeds_selected_stream(self):
        session = Session()
        source = CommandBuilder(session, sys.executable)(
            "-c",
            "import sys; print('out'); print('err', file=sys.stderr)",
        ).run()
        consumer = CommandBuilder(session, sys.executable)(
            "-c",
            "import sys; sys.stdout.write(sys.stdin.read() + '!')",
        )

        result = consumer.stdin_from(source, stream="stderr").run()

        self.assertEqual(result.stdout, "err\n!")
        self.assertEqual(result.upstream_results[0].stderr, "err\n")

    def test_command_builder_pipe_to_returns_downstream_with_upstream_nonzero_metadata(self):
        session = Session()
        producer = CommandBuilder(session, sys.executable)(
            "-c",
            "import sys; print('still useful'); sys.exit(7)",
        )
        consumer = CommandBuilder(session, sys.executable)(
            "-c",
            "import sys; sys.stdout.write(sys.stdin.read().replace('useful', 'visible'))",
        )

        result = producer.pipe_to(consumer)

        self.assertEqual(result.stdout, "still visible\n")
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(len(result.upstream_results), 1)
        self.assertEqual(result.upstream_results[0].exit_code, 7)

    def test_command_stream_serializes_from_session_evaluation(self):
        value = self.eval("cli.python3('-c', 'print(1)').stderr_stream()")["value"]

        self.assertEqual(value["stream"], "stderr")
        self.assertEqual(value["command"]["program"], "python3")

    def test_command_builder_returns_json_shape_from_session_evaluation(self):
        value = self.eval("cli.echo('hello').in_('/tmp').env('X_TEST', '1').stdin_text('input')")["value"]

        self.assertEqual(value, {
            "program": "echo",
            "args": ["hello"],
            "cwd": "/tmp",
            "env": {"X_TEST": "1"},
            "stdin": "input",
        })

    def test_tool_command_wrappers_build_safe_commands(self):
        code = """
[
    tools.sudo(cli.echo('hello')),
    tools.browser.open('https://example.test'),
    tools.browser.snapshot({'name': 'main', 'full_page': True}),
    kubectl.get('pods', {'name': 'api', 'namespace': 'prod', 'selector': 'app=api'}),
    tools.github.pr_view(12, {'json': ['number', 'title']}),
    tools.github.prView(13),
    tools.tmux.open('pyrun-test'),
    tools.tmux.send('pyrun-test', 'echo hi'),
    tools.tmux.capture('pyrun-test'),
]
"""
        value = self.eval(code)["value"]

        self.assertEqual(value[0]["program"], "authsudo")
        self.assertEqual(value[0]["args"], ["echo", "hello"])
        self.assertEqual(value[1], {"program": "browser-cli", "args": ["open", "https://example.test"], "cwd": None, "env": {}, "stdin": None})
        self.assertEqual(value[2]["args"], ["snapshot", "--name", "main", "--full-page"])
        self.assertEqual(value[3]["program"], "kubectl")
        self.assertEqual(value[3]["args"], ["get", "pods", "api", "--namespace", "prod", "--selector", "app=api", "--output", "json"])
        self.assertEqual(value[4]["args"], ["pr", "view", "12", "--json", "number,title"])
        self.assertEqual(value[5]["args"], ["pr", "view", "13"])
        self.assertEqual(value[6]["args"], ["new-session", "-d", "-s", "pyrun-test"])
        self.assertEqual(value[7]["args"], ["send-keys", "-t", "pyrun-test", "echo hi", "Enter"])
        self.assertEqual(value[8]["args"], ["capture-pane", "-p", "-t", "pyrun-test"])

    def test_powershell_uses_utf16le_encoded_command(self):
        value = self.eval("tools.powershell('Write-Output café', {'executable': 'powershell'})")["value"]

        self.assertEqual(value["program"], "powershell")
        self.assertEqual(value["args"][:2], ["-NoProfile", "-EncodedCommand"])
        encoded = value["args"][2]
        self.assertEqual(__import__('base64').b64decode(encoded).decode('utf-16le'), "Write-Output café")

    def test_ssh_builds_sshpass_and_ssh_args_without_executing(self):
        code = """
remote = tools.ssh({'host': 'server.test', 'user': 'alice', 'port': 2222, 'password': 'secret'})
[
    remote.run(cli.echo('hello')),
    remote.cli('uptime'),
]
"""
        value = self.eval(code)["value"]

        self.assertEqual(value[0]["program"], "sshpass")
        self.assertEqual(value[0]["args"], ["-p", "secret", "ssh", "-p", "2222", "alice@server.test", "--", "echo", "hello"])
        self.assertEqual(value[1]["args"], ["-p", "secret", "ssh", "-p", "2222", "alice@server.test", "--", "uptime"])

    def test_git_status_runs_in_temp_repo_and_commit_builder_validates_message(self):
        if not shutil.which("git"):
            self.skipTest("git unavailable")
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run(["git", "init"], cwd=tmp, check=True, capture_output=True, text=True)
            Path(tmp, "note.txt").write_text("hello\n")
            status = self.eval(f"tools.git.status({{'cwd': {tmp!r}}})")["value"]
            builder = self.eval(f"tools.git.build_commit({{'subject': 'Add note', 'body_lines': ['Details'], 'paths': ['note.txt'], 'cwd': {tmp!r}, 'no_verify': True}})")["value"]
            bad = self.eval("tools.git.build_commit({'subject': 'Bad\\nsubject'})")

        self.assertIn("##", status)
        self.assertIn("?? note.txt", status)
        self.assertEqual(builder["program"], "git")
        self.assertEqual(builder["args"], ["commit", "--file", "-", "--no-verify", "--", "note.txt"])
        self.assertEqual(builder["stdin"], "Add note\n\nDetails\n")
        self.assertEqual(builder["cwd"], tmp)
        self.assertEqual(bad["type"], "error")
        self.assertIn("subject must be a single line", bad["error"])

    def test_fd_find_files_and_dirs_filter_from_session_cwd(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "src").mkdir()
            Path(tmp, "src", "app.py").write_text("print('hi')")
            Path(tmp, "src", "note.txt").write_text("note")
            Path(tmp, "src", ".secret.py").write_text("hidden")
            Path(tmp, ".hidden_dir").mkdir()
            Path(tmp, ".hidden_dir", "hidden.py").write_text("hidden")
            self.eval(f"host.cd({tmp!r})")
            py_files = self.eval("fd.find('*.py', {'root': 'src', 'glob': True, 'extension': 'py'})") ["value"]
            hidden_files = self.eval("fd.find('*.py', {'glob': True, 'hidden': True})") ["value"]
            files = self.eval("fd.files('src')") ["value"]
            dirs = self.eval("fd.dirs('.')") ["value"]
            absolute = self.eval("fd.find('app.py', {'root': 'src', 'absolute_path': True})") ["value"]

        self.assertEqual(py_files, ["src/app.py"])
        self.assertEqual(hidden_files, [".hidden_dir/hidden.py", "src/.secret.py", "src/app.py"])
        self.assertEqual(files, ["src/app.py", "src/note.txt"])
        self.assertEqual(dirs, ["src"])
        self.assertEqual(absolute, [str(Path(tmp, "src", "app.py"))])

    def test_rg_search_files_matches_and_json_use_session_cwd(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "src").mkdir()
            Path(tmp, "src", "alpha.txt").write_text("Hello world\nhello again\n")
            Path(tmp, "src", "beta.log").write_text("HELLO log\n")
            Path(tmp, "src", ".hidden.txt").write_text("hello hidden\n")
            self.eval(f"host.cd({tmp!r})")
            result = self.eval("rg.search('hello', ['src'], {'ignore_case': True, 'glob': '*.txt', 'context': 1})") ["value"]
            fixed = self.eval("rg.search('Hello world', ['src'], {'fixed': True})") ["value"]
            files = self.eval("rg.files('hello', ['src'], {'ignore_case': True})") ["value"]
            matches = self.eval("rg.matches('hello', ['src'], {'ignore_case': True, 'max_count': 1})") ["value"]
            json_rows = self.eval("rg.search('hello', ['src'], {'ignore_case': True, 'json': True}).json()") ["value"]

        self.assertEqual(result["exit_code"], 0)
        self.assertEqual(result["lines"], ["src/alpha.txt:1:Hello world", "src/alpha.txt:2:hello again"])
        self.assertEqual(fixed["text"], "src/alpha.txt:1:Hello world\n")
        self.assertEqual(files, ["src/alpha.txt", "src/beta.log"])
        self.assertEqual(matches[0], {"path": "src/alpha.txt", "line_number": 1, "line": "Hello world", "submatches": [{"text": "Hello", "start": 0, "end": 5}]})
        self.assertEqual(json_rows[0]["type"], "match")
        self.assertEqual(json_rows[0]["data"]["path"], "src/alpha.txt")

    def test_sqlite_query_returns_dict_rows_and_resolves_relative_database(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.eval(f"host.cd({tmp!r})")
            create_result = self.eval("sqlite.query('items.db', 'CREATE TABLE items (id INTEGER PRIMARY KEY, name TEXT)')") ["value"]
            insert_result = self.eval("sqlite.query('items.db', \"INSERT INTO items (name) VALUES ('apple'), ('pear')\")") ["value"]
            rows = self.eval("sqlite.query('items.db', 'SELECT id, name FROM items ORDER BY id')") ["value"]
            database_exists = Path(tmp, "items.db").exists()

        self.assertEqual(create_result, {"rows_affected": -1})
        self.assertEqual(insert_result, {"rows_affected": 2})
        self.assertEqual(rows, [{"id": 1, "name": "apple"}, {"id": 2, "name": "pear"}])
        self.assertTrue(database_exists)

    def test_text_helpers_cover_lines_jsonl_csv_and_bytes(self):
        code = """
[
    text.lines('a\\nb\\nc', 2, 3),
    text.lines('a\\nb\\nc', 2),
    text.range('a\\nb\\nc', 2, 3),
    text.range('a\\nb\\nc', 2),
    text.line_count('a\\nb\\nc'),
    text.word_count(' one two  three '),
    text.head('a\\nb\\nc', 2),
    text.tail('a\\nb\\nc', 2),
    text.split_row('a,b,c', ','),
    text.split_words(' one two  three '),
    text.trim('  hi  '),
    text.trimmed('  hi  '),
    text.replace_text('one two one', 'one', 'ONE'),
    text.json('{"a": 1}'),
    text.json_lines('{"a":1}\\n{"a":2}\\n'),
    text.jsonl('{"a":1}\\n{"a":2}\\n'),
    text.lower('Hi'),
    text.upper('Hi'),
    text.chars('ab'),
    text.bytes_count('é'),
    text.byte_count('é'),
    text.byte_array('é'),
    text.csv([{'a': 1}, {'a': 2, 'b': 3}]),
    text.tsv([['a', 'b'], [1, 2]]),
    text.csv('a,b\\n1,2\\n'),
    text.tsv('a\\tb\\n1\\t2\\n'),
]
"""
        result = self.eval(code)["value"]

        self.assertEqual(result[0], ["b", "c"])
        self.assertEqual(result[1], ["b"])
        self.assertEqual(result[2], ["b", "c"])
        self.assertEqual(result[3], ["b"])
        self.assertEqual(result[4:22], [3, 3, ["a", "b"], ["b", "c"], ["a", "b", "c"], ["one", "two", "three"], "hi", "hi", "ONE two ONE", {"a": 1}, [{"a": 1}, {"a": 2}], [{"a": 1}, {"a": 2}], "hi", "HI", ["a", "b"], 2, 2, [195, 169]])
        self.assertEqual(result[22], "a,b\n1,\n2,3\n")
        self.assertEqual(result[23], "a\tb\n1\t2\n")
        self.assertEqual(result[24], [{"a": "1", "b": "2"}])
        self.assertEqual(result[25], [{"a": "1", "b": "2"}])

    def test_seq_helpers_cover_filters_projections_and_aggregates(self):
        code = """
rows = [
    {'name': 'apple', 'kind': 'fruit', 'count': 2, 'meta': {'rank': 3}},
    {'name': 'pear', 'kind': 'fruit', 'count': 4, 'meta': {'rank': 1}},
    {'name': 'kale', 'kind': 'veg', 'count': None, 'meta': {'rank': 2}},
]
[
    seq.containing(['alpha', 'beta'], 'alp'),
    seq.not_containing(['alpha', 'beta'], 'alp'),
    seq.starts_with(['alpha', 'beta'], 'be'),
    seq.ends_with(['alpha', 'beta'], 'ha'),
    seq.matching(['a1', 'b2'], r'\\d'),
    seq.not_matching(['a1', 'bb'], r'\\d'),
    seq.glob(['a.py', 'b.txt'], '*.py'),
    seq.not_glob(['a.py', 'b.txt'], '*.py'),
    seq.first(rows),
    seq.last(rows),
    seq.take([1, 2, 3], 2),
    seq.tail([1, 2, 3], 2),
    seq.tail([1, 2, 3], 0),
    seq.join_text(['a', 'b'], ','),
    seq.join_text(['a', 'b']),
    seq.unique(['a', 'b', 'a']),
    seq.compact([0, 1, None, '', 'x', False]),
    seq.default([None, '', 'ok', 0], 'fallback'),
    seq.default([], ['fallback']),
    seq.wrap([1, 2], 'id'),
    seq.enumerate(['a', 'b']),
    seq.is_empty([]),
    seq.is_not_empty([1]),
    seq.any([0, 2], 2),
    seq.all([2, 2], 2),
    seq.sum([1, '2', 'bad', None, 4.567]),
    seq.avg([1, '2', 'bad', None, 4.567]),
    seq.min([3, '1', 'bad', None, 2]),
    seq.max([3, '1', 'bad', None, 2]),
    seq.round([1, '2', 'bad', None, 4.567], 1),
    seq.lengths(['aa', [1, 2, 3]]),
    seq.lower(['A', 'B']),
    seq.upper(['a', 'b']),
    seq.sorted([3, 1, 2]),
    seq.reversed([1, 2, 3]),
    seq.get(rows, 'meta.rank'),
    seq.pluck(rows, 'name'),
    seq.values_of(rows, 'kind'),
    seq.select(rows, 'name', 'count'),
    seq.reject(rows, 'meta'),
    seq.where(rows, {'kind': 'fruit'}),
    seq.where(rows, lambda row: (row['count'] or 0) >= 3),
    seq.to_csv(seq.select(rows, 'name', 'kind')),
    seq.to_tsv([["name", "count"], ["apple", 2]]),
    seq.to_json_lines(seq.select(rows, 'name')),
]
"""
        result = self.eval(code)["value"]

        self.assertEqual(result[0:8], [['alpha'], ['beta'], ['beta'], ['alpha'], ['a1', 'b2'], ['bb'], ['a.py'], ['b.txt']])
        self.assertEqual(result[8], {'name': 'apple', 'kind': 'fruit', 'count': 2, 'meta': {'rank': 3}})
        self.assertEqual(result[9], {'name': 'kale', 'kind': 'veg', 'count': None, 'meta': {'rank': 2}})
        self.assertEqual(result[10:21], [[1, 2], [2, 3], [], 'a,b', 'ab', ['a', 'b'], [1, 'x'], ['fallback', 'fallback', 'ok', 0], [], [{'id': 1}, {'id': 2}], [{'index': 0, 'item': 'a'}, {'index': 1, 'item': 'b'}]])
        self.assertEqual(result[21:26], [True, True, True, True, 7.567])
        self.assertAlmostEqual(result[26], 2.522333333333333)
        self.assertEqual(result[27:35], [1, 3, [1, 2, None, None, 4.6], [2, 3], ['a', 'b'], ['A', 'B'], [1, 2, 3], [3, 2, 1]])
        self.assertEqual(result[35], [3, 1, 2])
        self.assertEqual(result[36], ['apple', 'pear', 'kale'])
        self.assertEqual(result[37], ['fruit', 'fruit', 'veg'])
        self.assertEqual(result[38], [{'name': 'apple', 'count': 2}, {'name': 'pear', 'count': 4}, {'name': 'kale', 'count': None}])
        self.assertEqual(result[39], [{'name': 'apple', 'kind': 'fruit', 'count': 2}, {'name': 'pear', 'kind': 'fruit', 'count': 4}, {'name': 'kale', 'kind': 'veg', 'count': None}])
        self.assertEqual([row['name'] for row in result[40]], ['apple', 'pear'])
        self.assertEqual([row['name'] for row in result[41]], ['pear'])
        self.assertEqual(result[42], 'name,kind\napple,fruit\npear,fruit\nkale,veg\n')
        self.assertEqual(result[43], 'name\tcount\napple\t2\n')
        self.assertEqual(result[44], '{"name": "apple"}\n{"name": "pear"}\n{"name": "kale"}\n')

    def test_obj_helpers_cover_projection_update_and_merge(self):
        code = """
item = {'name': 'apple', 'count': 2, 'meta': {'rank': 3}}
[
    obj.get(item, 'meta.rank'),
    obj.select(item, 'name', 'count'),
    obj.reject(item, 'meta'),
    obj.rename(item, {'name': 'label'}),
    obj.insert(item, 'fresh', True),
    obj.update(item, 'count', lambda value: value + 1),
    obj.update(item, 'count', 10),
    obj.merge(item, {'count': 5, 'color': 'red'}),
    obj.columns(item),
    obj.values(item),
    obj.entries(item),
    obj.items(item),
]
"""
        result = self.eval(code)["value"]

        self.assertEqual(result[0], 3)
        self.assertEqual(result[1], {'name': 'apple', 'count': 2})
        self.assertEqual(result[2], {'name': 'apple', 'count': 2})
        self.assertEqual(result[3], {'label': 'apple', 'count': 2, 'meta': {'rank': 3}})
        self.assertEqual(result[4], {'name': 'apple', 'count': 2, 'meta': {'rank': 3}, 'fresh': True})
        self.assertEqual(result[5], {'name': 'apple', 'count': 3, 'meta': {'rank': 3}})
        self.assertEqual(result[6], {'name': 'apple', 'count': 10, 'meta': {'rank': 3}})
        self.assertEqual(result[7], {'name': 'apple', 'count': 5, 'meta': {'rank': 3}, 'color': 'red'})
        self.assertEqual(result[8], ['name', 'count', 'meta'])
        self.assertEqual(result[9], ['apple', 2, {'rank': 3}])
        self.assertEqual(result[10], [['name', 'apple'], ['count', 2], ['meta', {'rank': 3}]])
        self.assertEqual(result[11], [['name', 'apple'], ['count', 2], ['meta', {'rank': 3}]])

    def test_hr_wrapper_dispatches_and_returns_json_values(self):
        code = """
[
    hr('a\\nb\\nc').lines(2, 3),
    hr([{'name': 'apple'}, {'name': 'pear'}]).select('name'),
    hr({'name': 'apple', 'count': 2}).select('name'),
    hr([1, 2, 3]).where(lambda value: value > 1).sum(),
]
"""
        result = self.eval(code)["value"]

        self.assertEqual(result, [['b', 'c'], [{'name': 'apple'}, {'name': 'pear'}], {'name': 'apple'}, 5])

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
        if self.path == "/session":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            payload = {
                "shared": self.headers.get("X-Shared"),
                "override": self.headers.get("X-Override"),
                "path": self.path,
            }
            self.wfile.write(json.dumps(payload).encode())
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"hello")

    def do_POST(self):
        self.send_json_echo()

    def do_PUT(self):
        self.send_json_echo()

    def do_PATCH(self):
        self.send_json_echo()

    def do_DELETE(self):
        self.send_response(204)
        self.send_header("X-Deleted", "yes")
        self.end_headers()

    def do_HEAD(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("X-Head", "yes")
        self.end_headers()

    def send_json_echo(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length).decode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        payload = {"method": self.command, "content_type": self.headers.get("Content-Type"), "body": body}
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

        self.assertEqual(json_echo, {"method": "POST", "content_type": "application/json", "body": '{"a": 1}'})
        self.assertEqual(form_echo, {"method": "POST", "content_type": "application/x-www-form-urlencoded", "body": "a=1&b=two"})
        self.assertEqual(body_echo, {"method": "POST", "content_type": "text/plain", "body": "raw"})
        self.assertEqual(to_file, str(target))
        self.assertEqual(saved, "hello")

    def test_http_put_patch_and_delete_helpers(self):
        put_echo = self.eval(f"http.put({self.base_url + '/echo'!r}, {{'json': {{'a': 1}}}}).json()") ["value"]
        patch_echo = self.eval(f"http.patch({self.base_url + '/echo'!r}, {{'body': 'patched', 'headers': {{'Content-Type': 'text/plain'}}}}).json()") ["value"]
        deleted = self.eval(f"http.delete({self.base_url + '/resource'!r}).run()") ["value"]

        self.assertEqual(put_echo, {"method": "PUT", "content_type": "application/json", "body": '{"a": 1}'})
        self.assertEqual(patch_echo, {"method": "PATCH", "content_type": "text/plain", "body": "patched"})
        self.assertEqual(deleted["status"], 204)
        self.assertEqual(deleted["headers"]["X-Deleted"], "yes")
        self.assertEqual(deleted["body"], [])

    def test_http_head_helper_returns_headers_without_body(self):
        response = self.eval(f"http.head({self.base_url + '/text'!r}).run()") ["value"]

        self.assertEqual(response["status"], 200)
        self.assertEqual(response["headers"]["Content-Type"], "text/plain")
        self.assertEqual(response["headers"]["X-Head"], "yes")
        self.assertEqual(response["body"], [])

    def test_http_session_uses_base_url_and_overrides_headers(self):
        code = f"""
client = http.session({{'base_url': {self.base_url!r}, 'headers': {{'X-Shared': 'default', 'X-Override': 'session'}}}})
client.get('/session', {{'headers': {{'X-Override': 'request'}}}}).json()
"""
        data = self.eval(code)["value"]

        self.assertEqual(data, {"shared": "default", "override": "request", "path": "/session"})


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
