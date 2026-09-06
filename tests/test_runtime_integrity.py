"""Exercise dependency drift, filesystem aliases, and surviving stage descendants."""

import importlib.util
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch


REPOSITORY = Path(__file__).resolve().parents[1]
RUNNER = REPOSITORY / "scripts/run_workflow.py"


class RuntimeIntegrityTests(unittest.TestCase):
    def setUp(self):
        practice = REPOSITORY / "practice"
        practice.mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(prefix="runtime integrity-", dir=practice)
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.manifest = self.root / "workflow.json"

    def stage(self, key, outputs, code, inputs=None, sources=None, timeout=10):
        return {"id": key, "inputs": inputs or [], "outputs": outputs, "code": sources or [],
                "command": ["{python}", "-c", code], "timeout_seconds": timeout}

    def write_manifest(self, stages):
        self.manifest.write_text(json.dumps({"mode": "SYNTHETIC_PRACTICE", "stages": stages}))

    def invoke(self, command="run", expected=0, runner=RUNNER):
        process = subprocess.run([sys.executable, str(runner), command, "--manifest", str(self.manifest)],
                                 cwd=self.root, capture_output=True, text=True, timeout=15)
        self.assertEqual(process.returncode, expected, process.stdout + process.stderr)
        return json.loads(process.stdout)

    def checkpoint(self):
        return json.loads((self.root / ".workflow/checkpoint.json").read_text())

    def assert_archived(self, name):
        self.assertFalse((self.root / name).exists(), name)
        self.assertTrue(list((self.root / ".workflow/stale").rglob(Path(name).name)), name)

    def test_downstream_cannot_rewrite_completed_input(self):
        self.write_manifest([
            self.stage("solve", ["value.txt"], "from pathlib import Path; Path('value.txt').write_text('original')"),
            self.stage("report", ["report.txt"], "from pathlib import Path; Path('value.txt').write_text('changed'); "
                       "Path('report.txt').write_text('invalid report')", inputs=["value.txt"]),
            self.stage("publish", ["published.txt"], "from pathlib import Path; Path('published.txt').touch()",
                       inputs=["report.txt"]),
        ])
        self.assertEqual(self.invoke(expected=1)["reason"], "DEPENDENCY_CHANGED")
        state = self.checkpoint()
        self.assertEqual(state["status"], "FAILED")
        self.assertEqual(state["stages"]["solve"]["status"], "INVALIDATED_DEPENDENCY_CHANGE")
        self.assertEqual(state["stages"]["publish"]["status"], "INVALIDATED_DEPENDENCY_CHANGE")
        self.assert_archived("value.txt")
        self.assert_archived("report.txt")
        self.assertFalse((self.root / "published.txt").exists())
        self.assertFalse(self.invoke("status")["all_current"])

    def test_drift_of_own_or_future_registered_code_fails_immediately(self):
        for source in ("own.py", "future.py"):
            with self.subTest(source=source):
                for name in ("own.py", "future.py"):
                    (self.root / name).write_text("# original code\n")
                self.write_manifest([
                    self.stage("mutate", ["value.txt"], "from pathlib import Path; "
                               f"Path('{source}').write_text('# changed code\\n'); Path('value.txt').write_text('invalid')",
                               sources=["own.py"]),
                    self.stage("future", ["future.txt"], "from pathlib import Path; Path('future.txt').touch()",
                               sources=["future.py"]),
                ])
                self.assertEqual(self.invoke(expected=1)["reason"], "DEPENDENCY_CHANGED")
                self.assert_archived("value.txt")
                self.assertFalse((self.root / "future.txt").exists())

    def test_other_stages_raw_input_is_checked_after_each_stage(self):
        (self.root / "raw.txt").write_text("original")
        self.write_manifest([
            self.stage("mutate", ["value.txt"], "from pathlib import Path; Path('raw.txt').write_text('changed'); "
                       "Path('value.txt').write_text('invalid')"),
            self.stage("read", ["future.txt"], "from pathlib import Path; Path('future.txt').touch()", inputs=["raw.txt"]),
        ])
        self.assertEqual(self.invoke(expected=1)["reason"], "DEPENDENCY_CHANGED")
        self.assert_archived("value.txt")
        self.assertFalse((self.root / "future.txt").exists())

    def test_runner_drift_invalidates_all_stages(self):
        runner = self.root / "runner.py"
        shutil.copyfile(RUNNER, runner)
        self.write_manifest([
            self.stage("solve", ["value.txt"], "from pathlib import Path; Path('value.txt').write_text('original')"),
            self.stage("mutate", ["report.txt"], "from pathlib import Path; "
                       "p=Path('runner.py'); p.write_text(p.read_text()+'\\n# changed\\n'); Path('report.txt').touch()"),
        ])
        self.assertEqual(self.invoke(expected=1, runner=runner)["reason"], "DEPENDENCY_CHANGED")
        self.assert_archived("value.txt")
        self.assert_archived("report.txt")

    def test_final_completion_rechecks_files_after_last_checkpoint(self):
        self.write_manifest([self.stage("solve", ["value.txt"],
                                        "from pathlib import Path; Path('value.txt').write_text('original')")])
        spec = importlib.util.spec_from_file_location("runtime_under_test", RUNNER)
        runner = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(runner)
        original_write = runner.json_write
        modified = False

        def external_edit_after_checkpoint(path, value):
            nonlocal modified
            original_write(path, value)
            if (Path(path).name == "checkpoint.json" and not modified and value["status"] == "RUNNING"
                    and value["stages"].get("solve", {}).get("status") == "COMPLETE"):
                (self.root / "value.txt").write_text("external modification")
                modified = True

        with patch.object(runner, "json_write", side_effect=external_edit_after_checkpoint):
            result, code = runner.run(self.manifest)
        self.assertTrue(modified)
        self.assertEqual(code, 1)
        self.assertEqual(result["reason"], "DEPENDENCY_CHANGED")
        self.assert_archived("value.txt")

    def test_double_slash_outputs_have_one_producer(self):
        self.write_manifest([
            self.stage("first", ["results//value.txt"], "raise AssertionError('must not run')"),
            self.stage("second", ["results/value.txt"], "raise AssertionError('must not run')"),
        ])
        self.assertEqual(self.invoke(expected=2)["status"], "CONFIGURATION_ERROR")
        self.assertFalse((self.root / ".workflow").exists())

    def test_normalized_input_tracks_producer_and_reuses_results(self):
        self.write_manifest([
            self.stage("solve", ["results//value.txt"], "from pathlib import Path; Path('results').mkdir(); "
                       "Path('results/value.txt').write_text('original')"),
            self.stage("report", ["report.txt"], "from pathlib import Path; "
                       "Path('report.txt').write_text(Path('results/value.txt').read_text())", inputs=["results/value.txt"]),
        ])
        self.assertEqual(self.invoke()["status"], "COMPLETE")
        self.assertTrue(self.invoke("status")["all_current"])
        self.assertTrue(all(event["action"] == "REUSED" for event in self.invoke("resume")["events"]))

    def test_symlink_and_hardlink_output_aliases_are_rejected(self):
        for link_type in ("symlink", "hardlink"):
            with self.subTest(link_type=link_type):
                original, alias = self.root / "value.txt", self.root / "alias.txt"
                original.write_text("preserve")
                alias.unlink(missing_ok=True)
                if link_type == "symlink":
                    alias.symlink_to(original.name)
                else:
                    os.link(original, alias)
                self.write_manifest([
                    self.stage("first", ["value.txt"], "raise AssertionError('must not run')"),
                    self.stage("second", ["alias.txt"], "raise AssertionError('must not run')"),
                ])
                self.assertEqual(self.invoke(expected=2)["status"], "CONFIGURATION_ERROR")
                self.assertEqual(original.read_text(), "preserve")

    def test_hardlink_output_cannot_overwrite_input(self):
        original = self.root / "raw.txt"
        original.write_text("preserve")
        os.link(original, self.root / "output.txt")
        self.write_manifest([self.stage("solve", ["output.txt"], "raise AssertionError('must not run')", inputs=["raw.txt"])])
        self.assertEqual(self.invoke(expected=2)["status"], "CONFIGURATION_ERROR")
        self.assertEqual(original.read_text(), "preserve")

    def test_symlink_cannot_target_workflow_state(self):
        (self.root / ".workflow").mkdir()
        (self.root / "alias").symlink_to(".workflow", target_is_directory=True)
        self.write_manifest([self.stage("solve", ["alias/result.txt"], "raise AssertionError('must not run')")])
        self.assertEqual(self.invoke(expected=2)["status"], "CONFIGURATION_ERROR")

    def test_outputs_cannot_become_hardlink_aliases_during_execution(self):
        self.write_manifest([
            self.stage("solve", ["value.txt"], "from pathlib import Path; Path('value.txt').write_text('original')"),
            self.stage("alias", ["alias.txt"], "import os; os.link('value.txt', 'alias.txt')"),
        ])
        self.assertEqual(self.invoke(expected=1)["reason"], "DEPENDENCY_CHANGED")
        self.assert_archived("value.txt")
        self.assert_archived("alias.txt")

    def test_quarantine_preserves_raw_target_of_changed_output_directory(self):
        (self.root / "raw").mkdir()
        (self.root / "raw/data.txt").write_text("preserve")
        self.write_manifest([
            self.stage("redirect", ["results/data.txt"], "from pathlib import Path; "
                       "Path('results').symlink_to('raw', target_is_directory=True)"),
            self.stage("read", ["report.txt"], "raise AssertionError('must not run')", inputs=["raw/data.txt"]),
        ])
        self.assertEqual(self.invoke(expected=1)["reason"], "DEPENDENCY_CHANGED")
        self.assertEqual((self.root / "raw/data.txt").read_text(), "preserve")
        self.assertFalse((self.root / "results").is_symlink())
        # Python 3.10's literal-path glob skips dangling symlinks.
        archived = [Path(folder) / name for folder, directories, files in os.walk(self.root / ".workflow/stale")
                    for name in directories + files if name == "results"]
        self.assertEqual(len(archived), 1)
        self.assertTrue(archived[0].is_symlink())
        self.assertEqual(archived[0].readlink(), Path("raw"))

    def test_raw_directory_protection_includes_declared_symlink_paths(self):
        (self.root / "raw").mkdir()
        (self.root / "data.txt").write_text("preserve")
        (self.root / "raw/input.txt").symlink_to("../data.txt")
        self.write_manifest([self.stage("solve", ["raw/result.txt"], "raise AssertionError('must not run')",
                                        inputs=["raw/input.txt"])])
        self.assertEqual(self.invoke(expected=2)["status"], "CONFIGURATION_ERROR")

    def live_pid(self, pid):
        result = subprocess.run(["ps", "-p", str(pid), "-o", "stat="], capture_output=True, text=True, timeout=2)
        return bool(result.stdout.strip()) and not result.stdout.strip().startswith("Z")

    def descendant_fixture(self, timeout, parent_exits=False):
        (self.root / "grandchild.py").write_text(
            "import os, signal, time\nfrom pathlib import Path\n"
            "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
            "Path('grandchild.pid').write_text(str(os.getpid()))\n"
            "while True:\n    Path('result.txt').write_text(str(time.monotonic()))\n    time.sleep(.05)\n")
        (self.root / "child.py").write_text(
            "import subprocess, sys, time\nfrom pathlib import Path\n"
            "subprocess.Popen([sys.executable, 'grandchild.py'])\n"
            "while not Path('grandchild.pid').exists(): time.sleep(.01)\n"
            + ("" if parent_exits else "time.sleep(60)\n"))
        self.write_manifest([{"id": "stage", "inputs": [], "outputs": ["result.txt"],
                              "code": ["child.py", "grandchild.py"], "timeout_seconds": timeout,
                              "command": ["{python}", "{workspace}/child.py"]}])
        process = subprocess.Popen([sys.executable, str(RUNNER), "run", "--manifest", str(self.manifest)],
                                   cwd=self.root, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.addCleanup(self.cleanup_fixture, process)
        deadline = time.monotonic() + 5
        while not (self.root / "grandchild.pid").exists():
            self.assertIsNone(process.poll())
            self.assertLess(time.monotonic(), deadline)
            time.sleep(.01)
        return process

    def cleanup_fixture(self, process):
        if (self.root / ".workflow/checkpoint.json").exists():
            pgid = self.checkpoint()["stages"].get("stage", {}).get("pgid")
            if pgid:
                try:
                    os.killpg(pgid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
        if (self.root / "grandchild.pid").exists():
            pid = int((self.root / "grandchild.pid").read_text())
            if self.live_pid(pid):
                os.kill(pid, signal.SIGKILL)
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=5)

    def assert_descendants_stopped(self, process, expected_code, expected_status):
        stdout, stderr = process.communicate(timeout=12)
        self.assertEqual(process.returncode, expected_code, stdout + stderr)
        self.assertEqual(json.loads(stdout)["status"], expected_status)
        self.assertFalse(self.live_pid(int((self.root / "grandchild.pid").read_text())))
        self.assertFalse((self.root / ".workflow/lock.json").exists())
        self.assertIsNone(self.checkpoint()["stages"]["stage"]["pgid"])
        self.assert_archived("result.txt")
        time.sleep(.15)
        self.assertFalse((self.root / "result.txt").exists())

    @unittest.skipUnless(os.name == "posix", "POSIX process groups required")
    def test_timeout_stops_term_ignoring_grandchild_after_parent_exits(self):
        process = self.descendant_fixture(timeout=.5)
        self.assert_descendants_stopped(process, 1, "FAILED")

    @unittest.skipUnless(os.name == "posix", "POSIX process groups required")
    def test_interrupt_holds_lock_until_term_ignoring_grandchild_stops(self):
        process = self.descendant_fixture(timeout=60)
        process.send_signal(signal.SIGTERM)
        time.sleep(.2)
        self.assertIsNone(process.poll())
        self.assertTrue((self.root / ".workflow/lock.json").exists())
        self.assertEqual(self.invoke("resume", expected=3)["status"], "BLOCKED_RUNNING")
        self.assert_descendants_stopped(process, 130, "INTERRUPTED")

    @unittest.skipUnless(os.name == "posix", "POSIX process groups required")
    def test_successful_parent_cannot_leave_a_background_writer(self):
        process = self.descendant_fixture(timeout=60, parent_exits=True)
        self.assert_descendants_stopped(process, 1, "FAILED")
        self.assertEqual(self.checkpoint()["stages"]["stage"]["returncode"], 125)

    @unittest.skipUnless(os.name == "posix", "POSIX process groups required")
    def test_resume_blocks_orphan_group_after_runner_and_parent_exit(self):
        process = self.descendant_fixture(timeout=60)
        leader = self.checkpoint()["stages"]["stage"]["pid"]
        process.kill()
        process.communicate(timeout=5)
        os.kill(leader, signal.SIGTERM)
        deadline = time.monotonic() + 5
        while self.live_pid(leader):
            self.assertLess(time.monotonic(), deadline)
            time.sleep(.01)
        result = self.invoke("resume", expected=3)
        self.assertEqual(result["status"], "BLOCKED_LIVE_CHILD")
        self.assertEqual(result["pgid"], leader)

    @unittest.skipUnless(os.name == "posix", "POSIX process groups required")
    def test_zombie_only_group_does_not_block_legacy_checkpoint_resume(self):
        child = subprocess.Popen([sys.executable, "-c", "pass"], start_new_session=True)
        self.addCleanup(child.wait, timeout=5)
        deadline = time.monotonic() + 5
        while self.live_pid(child.pid):
            self.assertLess(time.monotonic(), deadline)
            time.sleep(.01)
        self.write_manifest([self.stage("stage", ["result.txt"], "from pathlib import Path; Path('result.txt').touch()")])
        (self.root / ".workflow").mkdir()
        (self.root / ".workflow/checkpoint.json").write_text(json.dumps({"status": "RUNNING", "stages": {
            "stage": {"status": "RUNNING", "pid": child.pid}}}))
        self.assertEqual(self.invoke("resume")["status"], "COMPLETE")


if __name__ == "__main__":
    unittest.main()
