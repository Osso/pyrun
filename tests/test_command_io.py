"""Regression tests isolate potentially deadlocking calls behind a watchdog."""
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import textwrap
import unittest


@unittest.skipUnless(os.name == "posix", "process-group watchdog requires POSIX")
class CommandIOTests(unittest.TestCase):
    def run_isolated(self, source):
        with tempfile.TemporaryDirectory() as tmp:
            pid_path = Path(tmp) / "pid"
            prelude = f'''
import contextlib
import io
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from unittest.mock import patch
from pyrun.runtime import CommandBuilder, Session
real_popen = subprocess.Popen
def tracked_popen(*args, **kwargs):
    process = real_popen(*args, **kwargs)
    Path({str(pid_path)!r}).write_text(str(process.pid))
    return process
with patch("pyrun.runtime.subprocess.Popen", side_effect=tracked_popen):
'''
            worker = subprocess.Popen(
                [sys.executable, "-c", textwrap.dedent(prelude) + textwrap.indent(textwrap.dedent(source), "    ")],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            )
            try:
                try:
                    stdout, stderr = worker.communicate(timeout=5)
                except subprocess.TimeoutExpired:
                    self.fail("command exceeded the 5-second regression watchdog")
                self.assertEqual(worker.returncode, 0, stdout + stderr)
            finally:
                if pid_path.exists():
                    try:
                        os.killpg(int(pid_path.read_text()), signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                if worker.poll() is None:
                    worker.kill()
                worker.communicate()

    def test_large_duplex_input_capture(self):
        self.run_isolated('''
            size = 256 * 1024
            code = "import sys; sys.stdout.write('o'*262144); sys.stdout.flush(); sys.stderr.write('e'*262144); sys.stderr.flush(); data=sys.stdin.read(); print(len(data))"
            result = CommandBuilder(Session(), sys.executable)("-c", code).input("i" * size).capture().run(timeout=2)
            assert result.stdout == "o" * size + str(size) + "\\n"
            assert result.stderr == "e" * size
            assert result.exit_code == 0
        ''')

    def test_large_duplex_input_forwarded(self):
        self.run_isolated('''
            size = 256 * 1024
            code = "import sys; sys.stdout.write('o'*262144); sys.stdout.flush(); data=sys.stdin.read(); print(len(data))"
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                result = CommandBuilder(Session(), sys.executable)("-c", code).input("i" * size).run(timeout=2)
            assert result == 0
            assert out.getvalue() == "o" * size + str(size) + "\\n"
        ''')

    def test_large_duplex_input_merged_output(self):
        self.run_isolated('''
            size = 256 * 1024
            code = "import sys; sys.stderr.write('e'*262144); sys.stderr.flush(); data=sys.stdin.read(); print(len(data))"
            result = CommandBuilder(Session(), sys.executable)("-c", code).input("i" * size).combined_text(timeout=2)
            assert result == "e" * size + str(size) + "\\n"
        ''')

    def test_timeout_while_child_does_not_read_input(self):
        self.run_isolated('''
            threads_before = set(threading.enumerate())
            code = "import sys,time; print('ready', flush=True); print('error', file=sys.stderr, flush=True); time.sleep(30)"
            start = time.monotonic()
            try:
                CommandBuilder(Session(), sys.executable)("-c", code).input("i" * 1048576).capture().run(timeout=0.3)
            except subprocess.TimeoutExpired as error:
                assert error.timeout == 0.3
                assert error.output == "ready\\n", repr(error.output)
                assert error.stderr == "error\\n", repr(error.stderr)
            else:
                raise AssertionError("expected TimeoutExpired")
            assert time.monotonic() - start < 2
            assert set(threading.enumerate()) == threads_before
        ''')

    def test_timeout_after_parent_exits_with_inherited_pipes(self):
        self.run_isolated('''
            threads_before = set(threading.enumerate())
            code = "import os,time; pid=os.fork(); time.sleep(30) if pid == 0 else print('parent done', flush=True)"
            start = time.monotonic()
            try:
                CommandBuilder(Session(), sys.executable)("-c", code).capture().run(timeout=0.3)
            except subprocess.TimeoutExpired as error:
                assert error.timeout == 0.3
                assert error.output == "parent done\\n", repr(error.output)
            else:
                raise AssertionError("expected TimeoutExpired")
            assert time.monotonic() - start < 2
            assert set(threading.enumerate()) == threads_before
        ''')

    def test_child_may_close_stdin_early(self):
        self.run_isolated('''
            code = "import os; os.close(0); print('done')"
            result = CommandBuilder(Session(), sys.executable)("-c", code).input("i" * 1048576).capture().run(timeout=2)
            assert result.stdout == "done\\n"
            assert result.exit_code == 0
        ''')

    def test_empty_and_unicode_input_without_timeout(self):
        self.run_isolated('''
            code = "import sys; sys.stdout.write(sys.stdin.read())"
            for value in ("", "héllo 🌍\\n" * 1000):
                result = CommandBuilder(Session(), sys.executable)("-c", code).input(value).timeout(None).capture().run()
                assert result.stdout == value
                assert result.exit_code == 0
        ''')

    def test_large_duplex_input_with_output_file(self):
        self.run_isolated('''
            import tempfile
            size = 256 * 1024
            code = "import sys; sys.stdout.write('o'*262144); sys.stdout.flush(); data=sys.stdin.read(); print(len(data))"
            with tempfile.TemporaryDirectory() as tmp:
                output = Path(tmp) / "out.txt"
                result = CommandBuilder(Session(), sys.executable)("-c", code).input("i" * size).output(output).capture().run(timeout=2)
                expected = "o" * size + str(size) + "\\n"
                assert result.stdout == expected
                assert output.read_text() == expected
        ''')
