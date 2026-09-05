"""Portable manifests must execute unchanged from any caller directory."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


REPOSITORY = Path(__file__).resolve().parents[1]
RUNNER = REPOSITORY / "scripts/run_workflow.py"


class PortableManifestTests(unittest.TestCase):
    def setUp(self):
        practice = REPOSITORY / "practice"
        practice.mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(prefix="portable manifest-", dir=practice)
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.caller = self.root / "unrelated caller"
        self.caller.mkdir()
        self.workspace = self.root / "project"
        self.workspace.mkdir()
        self.manifest = self.workspace / "workflow.json"

    def write_manifest(self, stages):
        self.manifest.write_text(json.dumps({"mode": "SYNTHETIC_PRACTICE", "stages": stages}))

    def invoke(self, command="run"):
        result = subprocess.run([sys.executable, str(RUNNER), command, "--manifest", str(self.manifest)],
                                cwd=self.caller, capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def test_python_dictionary_in_inline_command_is_literal(self):
        self.write_manifest([{"id": "dictionary", "inputs": [], "outputs": ["result.json"],
                              "command": ["{python}", "-c",
                                          "import json,pathlib; pathlib.Path('result.json').write_text(json.dumps({'value': 7}))"]}])
        self.assertEqual(self.invoke()["status"], "COMPLETE")
        self.assertEqual(json.loads((self.workspace / "result.json").read_text()), {"value": 7})

    def test_fstrings_and_regex_quantifiers_are_literal(self):
        self.write_manifest([{"id": "literals", "inputs": [], "outputs": ["result.txt"],
                              "command": ["{python}", "-c",
                                          "import pathlib,re; value='123'; assert re.fullmatch(r'\\d{2,4}',value); "
                                          "pathlib.Path('result.txt').write_text(f'value={value}')"]}])
        self.invoke()
        self.assertEqual((self.workspace / "result.txt").read_text(), "value=123")

    def test_relative_code_fingerprints_follow_manifest_and_invalidate_dependents(self):
        source = self.workspace / "stage.py"
        source.write_text("from pathlib import Path\nPath('value.txt').write_text('first')\n")
        consumer = self.workspace / "consumer.py"
        consumer.write_text("from pathlib import Path\nPath('report.txt').write_text(Path('value.txt').read_text())\n")
        self.write_manifest([
            {"id": "solve", "inputs": [], "outputs": ["value.txt"], "code": ["stage.py"],
             "command": ["{python}", "{workspace}/stage.py"]},
            {"id": "report", "inputs": ["value.txt"], "outputs": ["report.txt"], "code": ["consumer.py"],
             "command": ["{python}", "{workspace}/consumer.py"]},
            {"id": "independent", "inputs": [], "outputs": ["independent.txt"],
             "command": ["{python}", "-c", "from pathlib import Path; Path('independent.txt').write_text('unchanged')"]},
        ])
        self.invoke()
        self.assertTrue(self.invoke("status")["all_current"])
        self.assertTrue(all(event["action"] == "REUSED" for event in self.invoke("resume")["events"]))
        source.write_text("from pathlib import Path\nPath('value.txt').write_text('second')\n")
        status = self.invoke("status")
        self.assertFalse(status["all_current"])
        self.assertEqual(status["current_evidence"]["independent"], "VERIFIED_CURRENT")
        actions = {event["stage"]: event["action"] for event in self.invoke("resume")["events"]}
        self.assertEqual(actions, {"solve": "EXECUTED", "report": "EXECUTED", "independent": "REUSED"})
        self.assertEqual((self.workspace / "report.txt").read_text(), "second")

    def test_existing_absolute_paths_and_skill_token_remain_supported(self):
        source = self.workspace / "stage.py"
        source.write_text("import sys\nfrom pathlib import Path\n"
                          "assert (Path(sys.argv[1]) / 'SKILL.md').is_file()\n"
                          "Path('result.txt').write_text('ok')\n")
        self.write_manifest([{"id": "absolute", "inputs": [], "outputs": ["result.txt"],
                              "code": [str(source)], "command": ["{python}", str(source), "{skill}"]}])
        self.invoke()
        self.assertEqual((self.workspace / "result.txt").read_text(), "ok")

    def test_expanded_paths_are_not_expanded_again(self):
        self.workspace = self.root / "project {skill} with spaces"
        self.workspace.mkdir()
        self.manifest = self.workspace / "workflow.json"
        source = self.workspace / "stage.py"
        source.write_text("import sys\nfrom pathlib import Path\n"
                          "assert Path(sys.argv[1]) == Path.cwd()\n"
                          "Path('result.txt').write_text('ok')\n")
        self.write_manifest([{"id": "single_pass", "inputs": [], "outputs": ["result.txt"],
                              "code": ["{workspace}/stage.py"],
                              "command": ["{python}", "{workspace}/stage.py", "{workspace}"]}])
        self.invoke()
        self.assertTrue(self.invoke("status")["all_current"])


if __name__ == "__main__":
    unittest.main()
