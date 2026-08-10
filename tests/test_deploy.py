import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEPLOY_SCRIPT = ROOT / "deploy.sh"


class DeploymentTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("uv"), "uv is required for deployment tests")
    def test_deploy_installs_self_contained_tool(self):
        with tempfile.TemporaryDirectory(prefix="pyrun-deploy-test-") as temporary_directory:
            temporary_root = Path(temporary_directory)
            tool_directory = temporary_root / "tools"
            environment = os.environ.copy()
            environment.pop("PYTHONPATH", None)
            environment.update(
                {
                    "HOME": str(temporary_root / "home"),
                    "UV_CACHE_DIR": str(temporary_root / "cache"),
                    "UV_NO_CONFIG": "1",
                    "UV_PYTHON_DOWNLOADS": "never",
                    "UV_TOOL_BIN_DIR": str(temporary_root / "bin"),
                    "UV_TOOL_DIR": str(tool_directory),
                }
            )

            deployment = subprocess.run(
                [str(DEPLOY_SCRIPT)],
                cwd=ROOT,
                env=environment,
                capture_output=True,
                text=True,
                timeout=180,
            )
            self.assertEqual(deployment.returncode, 0, deployment.stdout + deployment.stderr)

            tool_environment = tool_directory / "pyrun"
            runner = tool_environment / "bin" / "pyrun-jsonl"
            interpreter = tool_environment / "bin" / "python"
            self.assertTrue(runner.is_file(), f"missing deployed runner: {runner}")

            probe = subprocess.run(
                [
                    str(interpreter),
                    "-c",
                    "import json, pathlib, pyrun; print(json.dumps({'module_file': str(pathlib.Path(pyrun.__file__).resolve())}))",
                ],
                cwd=temporary_root,
                env=environment,
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(probe.returncode, 0, probe.stdout + probe.stderr)
            module_file = Path(json.loads(probe.stdout)["module_file"])
            self.assertTrue(
                module_file.is_relative_to(tool_environment),
                f"deployed pyrun imports from outside its tool environment: {module_file}",
            )

            editable_artifacts = sorted(
                str(path.relative_to(tool_environment))
                for path in tool_environment.rglob("__editable__*")
            )
            self.assertEqual(editable_artifacts, [])

            runner_help = subprocess.run(
                [str(runner), "--help"],
                cwd=temporary_root,
                env=environment,
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(runner_help.returncode, 0, runner_help.stdout + runner_help.stderr)
            self.assertIn("usage", runner_help.stdout.lower())


if __name__ == "__main__":
    unittest.main()
