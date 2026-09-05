"""Check final allocations, independent of the algorithm that produced them."""

import copy
import hashlib
import itertools
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


REPOSITORY = Path(__file__).resolve().parents[1]
CHECKER = REPOSITORY / "scripts/verify_linear_solution.py"
MODEL = {
    "version": 1,
    "variables": {key: {"domain": "binary", "lower": 0, "upper": 1} for key in ("x", "y", "z")},
    "constraints": [{"id": "coverage", "coefficients": {"x": 2, "y": 3, "z": 4}, "sense": ">=", "rhs": 4}],
    "objective": {"sense": "min", "coefficients": {"x": 1, "y": 1.2, "z": 3}, "constant": 0},
    "tolerances": {"absolute": 1e-9, "relative": 0, "integrality": 1e-9},
}


class LinearSolutionTests(unittest.TestCase):
    def setUp(self):
        practice = REPOSITORY / "practice"
        practice.mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(prefix="linear audit-", dir=practice)
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.model_path = self.root / "model.json"
        self.solution_path = self.root / "solution.json"
        self.report_path = self.root / "report.json"
        self.model = copy.deepcopy(MODEL)

    def invoke(self, values, objective, expected=0):
        self.model_path.write_text(json.dumps(self.model))
        self.solution_path.write_text(json.dumps({"values": values, "objective": objective}))
        return self.run_cli(expected)

    def run_cli(self, expected, output=None):
        result = subprocess.run([sys.executable, str(CHECKER), "--model", str(self.model_path),
                                 "--solution", str(self.solution_path), "--output", str(output or self.report_path)],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        if output is None:
            return json.loads(self.report_path.read_text())

    def test_relaxation_and_rounded_solution_fail_for_different_reasons(self):
        relaxed = self.invoke({"x": 0.5, "y": 1, "z": 0}, 1.7, expected=1)
        failures = [(row["kind"], row["id"]) for row in relaxed["checks"] if row["status"] == "FAIL"]
        self.assertEqual(failures, [("integrality", "x")])
        rounded = self.invoke({"x": 0, "y": 1, "z": 0}, 1.2, expected=1)
        failures = [row for row in rounded["checks"] if row["status"] == "FAIL"]
        self.assertEqual([(row["kind"], row["id"]) for row in failures], [("constraint", "coverage")])
        self.assertEqual(failures[0]["violation"], "1")
        self.assertTrue(rounded["objective_consistent"])

    def test_all_binary_allocations_match_known_feasible_set(self):
        feasible = {(0, 0, 1), (0, 1, 1), (1, 0, 1), (1, 1, 0), (1, 1, 1)}
        for allocation in itertools.product((0, 1), repeat=3):
            with self.subTest(allocation=allocation):
                cost = allocation[0] + 1.2 * allocation[1] + 3 * allocation[2]
                report = self.invoke(dict(zip(("x", "y", "z"), allocation)), cost,
                                     expected=0 if allocation in feasible else 1)
                self.assertEqual(report["feasible"], allocation in feasible)

    def test_feasible_suboptimal_solution_does_not_claim_optimality(self):
        report = self.invoke({"x": 0, "y": 0, "z": 1}, 3)
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["optimality"], "NOT_ASSESSED")
        self.assertEqual(report["human_validation"], "NOT_PERFORMED")
        self.assertEqual(report["solution_sha256"], hashlib.sha256(self.solution_path.read_bytes()).hexdigest())

    def test_wrong_reported_objective_is_rejected(self):
        report = self.invoke({"x": 0, "y": 0, "z": 1}, 2.2, expected=1)
        self.assertTrue(report["feasible"])
        self.assertFalse(report["objective_consistent"])
        self.assertEqual(report["recomputed_objective"], "3.0")

    def test_negative_coefficients_equalities_and_bounds(self):
        self.model["variables"] = {"x": {"domain": "continuous", "lower": -1, "upper": None},
                                   "y": {"domain": "continuous", "lower": None, "upper": 2}}
        self.model["constraints"] = [
            {"id": "balance", "coefficients": {"x": -1, "y": 1}, "sense": "==", "rhs": 3},
            {"id": "capacity", "coefficients": {"x": 1, "y": 1}, "sense": "<=", "rhs": 1},
        ]
        self.model["objective"] = {"sense": "max", "coefficients": {"x": -2, "y": 0.5}, "constant": 4}
        report = self.invoke({"x": -1, "y": 2}, 7)
        self.assertTrue(report["feasible"])
        report = self.invoke({"x": -2, "y": 3}, 9.5, expected=1)
        failed = {row["id"] for row in report["checks"] if row["status"] == "FAIL"}
        self.assertEqual(failed, {"x.lower", "y.upper", "balance"})

    def test_predeclared_tolerances_do_not_change_recorded_values(self):
        self.model["variables"] = {"x": {"domain": "integer", "lower": 1, "upper": None}}
        self.model["constraints"] = []
        self.model["objective"]["coefficients"] = {"x": 1}
        self.model["tolerances"] = {"absolute": 0.0002, "relative": 0, "integrality": 0.001}
        report = self.invoke({"x": 0.9999}, 0.9999)
        self.assertEqual(report["recomputed_objective"], "0.9999")
        self.assertTrue(all(row["lhs"] == "0.9999" for row in report["checks"]))
        self.invoke({"x": 0.99}, 0.99, expected=1)

    def test_invalid_models_and_nonfinite_values_never_pass(self):
        changes = [
            lambda model: model["objective"]["coefficients"].update(unknown=1),
            lambda model: model["constraints"].append(copy.deepcopy(model["constraints"][0])),
            lambda model: model["constraints"][0].update(sense="<"),
            lambda model: model["variables"]["x"].update(lower=2, upper=1),
            lambda model: model["objective"].update(constant=float("nan")),
            lambda model: model["objective"].update(constant=float("inf")),
            lambda model: model["tolerances"].update(integrality=0.5),
            lambda model: model["objective"].update(quadratic={"x*x": 1}),
        ]
        for change in changes:
            self.model = copy.deepcopy(MODEL)
            change(self.model)
            report = self.invoke({"x": 1, "y": 1, "z": 0}, 2.2, expected=2)
            self.assertEqual(report["status"], "CONFIGURATION_ERROR")
        self.model = copy.deepcopy(MODEL)
        for values in ({"x": 1}, {"x": 1, "y": 1, "z": 0, "unknown": 0}, {"x": True, "y": 1, "z": 0}):
            self.invoke(values, 2.2, expected=2)

    def test_duplicate_keys_replace_old_pass_with_configuration_error(self):
        self.invoke({"x": 1, "y": 1, "z": 0}, 2.2)
        self.solution_path.write_text('{"values":{"x":1,"x":0,"y":1,"z":0},"objective":2.2}')
        report = self.run_cli(2)
        self.assertEqual(report["status"], "CONFIGURATION_ERROR")
        self.assertNotIn("feasible", report)

    def test_output_cannot_overwrite_inputs_or_hard_links(self):
        self.invoke({"x": 1, "y": 1, "z": 0}, 2.2)
        original = self.model_path.read_bytes()
        self.run_cli(2, output=self.model_path)
        alias = self.root / "model-alias.json"
        alias.hardlink_to(self.model_path)
        self.run_cli(2, output=alias)
        self.assertEqual(original, self.model_path.read_bytes())

    def test_large_noninteger_json_number_is_not_rounded_into_integer(self):
        self.model["variables"] = {"x": {"domain": "integer", "lower": None, "upper": None}}
        self.model["constraints"] = []
        self.model["objective"]["coefficients"] = {"x": 1}
        self.model_path.write_text(json.dumps(self.model))
        self.solution_path.write_text('{"values":{"x":9007199254740992.1},"objective":9007199254740992.1}')
        report = self.run_cli(1)
        self.assertEqual(report["recomputed_objective"], "9007199254740992.1")
        failures = [row for row in report["checks"] if row["status"] == "FAIL"]
        self.assertEqual(failures[0]["violation"], "0.1")

    def test_relative_tolerance_and_arithmetic_precision_are_explicit(self):
        self.model["variables"] = {"x": {"domain": "continuous", "lower": None, "upper": None}}
        self.model["constraints"] = [{"id": "target", "coefficients": {"x": 1}, "sense": "==", "rhs": 1000000}]
        self.model["objective"]["coefficients"] = {"x": 1}
        self.model["tolerances"] = {"absolute": 0, "relative": 1e-6, "integrality": 0}
        self.invoke({"x": 1000000.5}, 1000000.5)
        self.invoke({"x": 1000002}, 1000002, expected=1)
        self.model["constraints"] = []
        self.model["objective"]["constant"] = 1
        report = self.invoke({"x": 10**100}, 10**100, expected=2)
        self.assertIn("Inexact", report["error"])

    def test_workflow_stops_before_writing_when_final_solution_is_infeasible(self):
        self.invoke({"x": 0, "y": 1, "z": 0}, 1.2, expected=1)
        manifest = self.root / "workflow.json"
        manifest.write_text(json.dumps({"stages": [
            {"id": "verify", "inputs": ["model.json", "solution.json"], "outputs": ["verification.json"],
             "code": [str(CHECKER)], "command": ["{python}", str(CHECKER), "--model", "model.json",
                                                    "--solution", "solution.json", "--output", "verification.json"]},
            {"id": "write", "inputs": ["verification.json"], "outputs": ["paper.txt"],
             "command": ["{python}", "-c", "from pathlib import Path; Path('paper.txt').write_text('unexpected')"]},
        ]}))
        result = subprocess.run([sys.executable, str(REPOSITORY / "scripts/run_workflow.py"), "run",
                                 "--manifest", str(manifest)], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout)["stage"], "verify")
        self.assertFalse((self.root / "paper.txt").exists())
        archived = list((self.root / ".workflow/stale").rglob("verification.json"))
        self.assertEqual(len(archived), 1)
        self.assertEqual(json.loads(archived[0].read_text())["status"], "FAIL")


if __name__ == "__main__":
    unittest.main()
