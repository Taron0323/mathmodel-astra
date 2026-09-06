"""Analytical counterexamples, scaling invariance and diagnostic CLI behavior."""

import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import numpy as np

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))
from diagnose_identifiability import diagnose


def specification(matrix=None):
    if matrix is None:
        matrix = [[1, 1, 2], [1, -1, 0], [-1, 1, 0], [-1, -1, -2]]
    return {"parameter_names": ["a", "b", "c"][:len(matrix[0])], "jacobian": matrix,
            "parameter_scales": [1] * len(matrix[0]), "residual_scales": [1] * len(matrix),
            "residual_scale_kind": "reference"}


class SensitivityTests(unittest.TestCase):
    def test_pairwise_similarity_does_not_establish_rank(self):
        spec = specification()
        result = diagnose(spec)
        self.assertEqual(result["status"], "DIAGNOSED")
        self.assertEqual(result["rank_status"], "RANK_DEFICIENT")
        self.assertEqual(result["numerical_rank"], 2)
        self.assertLess(result["max_absolute_sensitivity_cosine"], .8)
        direction = np.array(list(result["unresolved_directions"][0]["scaled_coordinates"].values()))
        self.assertAlmostEqual(abs(direction @ np.array([1, 1, -1]) / np.sqrt(3)), 1)
        matrix = np.array(spec["jacobian"])
        np.testing.assert_array_equal(matrix @ [1, 2, 3], matrix @ [2, 3, 2])
        self.assertIsNone(result["scaled_condition_number"])
        self.assertEqual(result["global_identifiability"], "NOT_ASSESSED")

    def test_full_rank_can_still_be_ill_conditioned(self):
        result = diagnose(specification([[1, 0], [0, 1e-8], [0, 0]]))
        self.assertEqual(result["rank_status"], "FULL_COLUMN_RANK")
        self.assertAlmostEqual(result["scaled_condition_number"], 1e8)
        self.assertEqual(result["parameter_names"], ["a", "b"])
        self.assertAlmostEqual(abs(result["weakest_direction"]["scaled_coordinates"]["b"]), 1)
        self.assertEqual(result["statistical_intervals"], "NOT_COMPUTED")

    def test_parameter_units_do_not_change_scaled_diagnosis(self):
        original = specification()
        changed = copy.deepcopy(original)
        conversion = np.array([1000, .001, 100])
        changed["jacobian"] = (np.array(original["jacobian"]) / conversion).tolist()
        changed["parameter_scales"] = conversion.tolist()
        before, after = diagnose(original), diagnose(changed)
        np.testing.assert_allclose(before["scaled_singular_values"], after["scaled_singular_values"], atol=1e-14)
        self.assertEqual(before["numerical_rank"], after["numerical_rank"])

    def test_residual_units_do_not_change_scaled_diagnosis(self):
        original = specification()
        changed = copy.deepcopy(original)
        conversion = np.array([1000, .001, 100, 5])
        changed["jacobian"] = (np.array(original["jacobian"]) * conversion[:, None]).tolist()
        changed["residual_scales"] = conversion.tolist()
        np.testing.assert_allclose(diagnose(original)["scaled_singular_values"], diagnose(changed)["scaled_singular_values"], atol=1e-14)

    def test_underdetermined_and_zero_sensitivity_directions(self):
        for matrix, expected_rank in (([[1, 2, 3]], 1), ([[0, 0, 0], [0, 0, 0]], 0)):
            with self.subTest(matrix=matrix):
                result = diagnose(specification(matrix))
                directions = np.array([list(row["scaled_coordinates"].values()) for row in result["unresolved_directions"]])
                self.assertEqual(result["numerical_rank"], expected_rank)
                self.assertEqual(len(directions), 3 - expected_rank)
                np.testing.assert_allclose(np.array(matrix) @ directions.T, 0, atol=1e-14)
                np.testing.assert_allclose(directions @ directions.T, np.eye(len(directions)), atol=1e-14)

    def test_invalid_inputs_rejected(self):
        for key, value in (("jacobian", [[1, 2]]), ("jacobian", [[True, 1, 2]]),
                           ("jacobian", [[float("nan"), 1, 2]]), ("jacobian", [["1", 1, 2]]),
                           ("parameter_names", ["a", "a", "b"]), ("parameter_scales", [1, 0, 1]),
                           ("residual_scales", [1]), ("residual_scale_kind", "unknown"),
                           ("relative_rank_tolerance", 1e-30), ("relative_rank_tolerance", float("inf"))):
            with self.subTest(key=key, value=value):
                with self.assertRaises(ValueError):
                    diagnose({**specification(), key: value})

    def test_scaling_overflow_is_reported(self):
        spec = specification([[1e308]])
        spec["parameter_scales"] = [1e308]
        with self.assertRaises(FloatingPointError):
            diagnose(spec)


class DiagnosticCliTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / "jacobian.json"
        self.source.write_text(json.dumps(specification()), encoding="utf-8")
        self.output = self.root / "diagnosis.json"

    def run_cli(self, output=None):
        return subprocess.run([sys.executable, str(SCRIPTS / "diagnose_identifiability.py"),
                               "--input", str(self.source), "--output", str(output or self.output)],
                              capture_output=True, text=True, timeout=30)

    def test_rank_deficiency_is_a_successful_diagnosis(self):
        before = self.source.read_bytes()
        result = self.run_cli()
        self.assertEqual(result.returncode, 0, result.stderr)
        record = json.loads(self.output.read_text())
        self.assertEqual(record["rank_status"], "RANK_DEFICIENT")
        self.assertEqual(record["input_sha256"], hashlib.sha256(before).hexdigest())
        self.assertEqual(self.source.read_bytes(), before)

    def test_input_and_hardlink_cannot_be_overwritten(self):
        alias = self.root / "alias.json"
        alias.hardlink_to(self.source)
        before = self.source.read_bytes()
        for output in (self.source, alias):
            with self.subTest(output=output):
                self.assertEqual(self.run_cli(output).returncode, 2)
                self.assertEqual(self.source.read_bytes(), before)

    def test_invalid_recheck_replaces_old_diagnosis_with_error(self):
        self.assertEqual(self.run_cli().returncode, 0)
        self.source.write_text('{"jacobian": [], "jacobian": []}', encoding="utf-8")
        self.assertEqual(self.run_cli().returncode, 2)
        record = json.loads(self.output.read_text())
        self.assertEqual(record["status"], "DIAGNOSTIC_ERROR")
        self.assertNotIn("rank_status", record)
        self.assertIn("Duplicate JSON key", record["error"])


if __name__ == "__main__":
    unittest.main()
