"""Prediction evidence and rendering regressions; requires scientific dependencies."""

import copy
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import grouped_prediction_demo as demo


class PredictionEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.base = Path(cls.temporary.name) / "practice" / "original"
        demo.evaluate(cls.base)

    def setUp(self):
        self.case = self.base.parent / self._testMethodName
        shutil.copytree(self.base, self.case)

    def test_complete_prediction_evidence(self):
        state = demo.current_evidence(self.case)
        self.assertEqual({row["check_id"] for row in state["checks"]}, demo.CHECK_IDS)
        metrics = {row["protocol"]: row for row in demo.read_csv(self.case / "results/metrics.csv")}
        self.assertEqual(metrics["row_split"]["valid_for_unseen_entities"], "False")
        self.assertEqual(metrics["group_split"]["valid_for_unseen_entities"], "True")
        self.assertGreater(float(metrics["row_split"]["accuracy"]), float(metrics["group_split"]["accuracy"]) + .2)

    def test_render_reuses_predictions_without_training(self):
        names = (*demo.EVIDENCE_FILES, "verification.json")
        before = {name: demo.digest(self.case / name) for name in names}
        with patch.object(demo.StandardScaler, "fit", side_effect=AssertionError("Unexpected refit")), \
                patch.object(demo.KNeighborsClassifier, "fit", side_effect=AssertionError("Unexpected refit")), \
                patch.object(demo.DummyClassifier, "fit", side_effect=AssertionError("Unexpected refit")):
            demo.render(self.case)
        self.assertEqual(before, {name: demo.digest(self.case / name) for name in names})
        for name in ("report.md", "figures/split-comparison.png", "figures/split-comparison.svg"):
            self.assertGreater((self.case / name).stat().st_size, 0)

    def test_empty_partial_duplicate_or_failed_checks_rejected(self):
        state = demo.read_json(self.case / "verification.json")
        failed = copy.deepcopy(state["checks"])
        failed[0]["status"] = "FAIL"
        for checks in ([], state["checks"][:-1], [state["checks"][0]] * len(state["checks"]), failed):
            with self.subTest(checks=checks):
                demo.json_write(self.case / "verification.json", {**state, "checks": checks})
                with self.assertRaises(ValueError):
                    demo.render(self.case)
                self.assertFalse((self.case / "report.md").exists())

    def test_changed_inputs_or_results_rejected(self):
        for name in demo.EVIDENCE_FILES:
            with self.subTest(file=name):
                path = self.case / name
                original = path.read_bytes()
                path.write_bytes(original + b"\n")
                with self.assertRaises(ValueError):
                    demo.current_evidence(self.case)
                path.write_bytes(original)
        demo.current_evidence(self.case)

    def test_obsolete_code_record_rejected(self):
        state = demo.read_json(self.case / "verification.json")
        demo.json_write(self.case / "verification.json", {**state, "code_sha256": "0" * 64})
        with self.assertRaises(ValueError):
            demo.render(self.case)

    def test_missing_fold_invalidates_previous_pass(self):
        original_inputs = {name: demo.digest(self.case / name) for name in demo.EVIDENCE_FILES[:2]}
        folds = demo.read_json(self.case / "results/folds.json")
        demo.json_write(self.case / "results/folds.json", folds[:-1])
        with self.assertRaises(ValueError):
            demo.verify(self.case, original_inputs)
        self.assertEqual(demo.read_json(self.case / "verification.json")["status"], "FAIL")
        with self.assertRaises(ValueError):
            demo.render(self.case)

    def test_independent_entity_count_checked(self):
        original_inputs = {name: demo.digest(self.case / name) for name in demo.EVIDENCE_FILES[:2]}
        metrics = demo.read_csv(self.case / "results/metrics.csv")
        metrics[0]["independent_entities"] = metrics[0]["test_observations"]
        demo.write_csv(self.case / "results/metrics.csv", metrics)
        with self.assertRaises(RuntimeError):
            demo.verify(self.case, original_inputs)
        checks = {row["check_id"]: row["status"] for row in demo.read_json(self.case / "verification.json")["checks"]}
        self.assertEqual(checks["metric_sample_counts"], "FAIL")

    def test_existing_workspace_preserved(self):
        before = {str(path.relative_to(self.case)): demo.digest(path) for path in self.case.rglob("*") if path.is_file()}
        with self.assertRaises(FileExistsError):
            demo.evaluate(self.case)
        self.assertEqual(before, {str(path.relative_to(self.case)): demo.digest(path)
                                  for path in self.case.rglob("*") if path.is_file()})


if __name__ == "__main__":
    unittest.main()
