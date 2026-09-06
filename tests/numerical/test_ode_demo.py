"""Analytic ODE checks, boundary conditions and stale-evidence rejection."""

import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import ode_demo as demo


class OdeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "ode"
        demo.initialize(self.root)
        demo.solve(self.root)

    def test_analytic_refinement_and_conservation(self):
        report = demo.validate(self.root)
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(len(report["checks"]), 10)
        self.assertLess(report["max_errors_g"]["nominal:0.1"], 1e-4)
        self.assertLess(report["max_mass_residual_g"], 1e-10)
        for case in ("zero_rate", "zero_mass"):
            self.assertEqual(report["max_errors_g"][case + ":0.1"], 0)

    def test_changed_transfer_breaks_mass_conservation(self):
        rows = demo.read_csv(self.root / demo.FILES[1])
        rows[20]["B_g"] = float(rows[20]["B_g"]) + 1
        demo.write_csv(self.root / demo.FILES[1], rows)
        with self.assertRaises(ValueError):
            demo.validate(self.root)
        report = demo.read_json(self.root / "evidence/validation.json")
        self.assertIn({"check_id": "mass_conservation", "status": "FAIL"}, report["checks"])

    def test_duplicate_or_nonfinite_samples_invalidate_previous_pass(self):
        for corruption in ("duplicate", "nonfinite"):
            with self.subTest(corruption=corruption):
                demo.solve(self.root)
                demo.validate(self.root)
                rows = demo.read_csv(self.root / demo.FILES[1])
                if corruption == "duplicate":
                    rows[1] = rows[0].copy()
                else:
                    rows[1]["A_g"] = "nan"
                demo.write_csv(self.root / demo.FILES[1], rows)
                with self.assertRaises(ValueError):
                    demo.validate(self.root)
                self.assertEqual(demo.read_json(self.root / "evidence/validation.json")["status"], "FAIL")

    def test_stale_result_cannot_render(self):
        demo.validate(self.root)
        with (self.root / demo.FILES[1]).open("a") as stream:
            stream.write("\n")
        with self.assertRaises(ValueError):
            demo.render(self.root)
        self.assertFalse((self.root / "report.md").exists())

    def test_partial_validation_cannot_render(self):
        report = demo.validate(self.root)
        report["checks"].pop()
        demo.json_write(self.root / "evidence/validation.json", report)
        with self.assertRaises(ValueError):
            demo.render(self.root)

    def test_altered_validation_summary_cannot_render(self):
        report = demo.validate(self.root)
        report["max_errors_g"]["nominal:0.1"] = 0
        demo.json_write(self.root / "evidence/validation.json", report)
        with self.assertRaises(ValueError):
            demo.render(self.root)

    def test_reordered_samples_keep_numerical_evidence(self):
        before = demo.validate(self.root)
        rows = demo.read_csv(self.root / demo.FILES[1])
        demo.write_csv(self.root / demo.FILES[1], list(reversed(rows)))
        after = demo.validate(self.root)
        self.assertEqual(before["max_errors_g"], after["max_errors_g"])
        self.assertEqual(demo.current_evidence(self.root)["status"], "PASS")

    def test_init_preserves_existing_workspace(self):
        original = (self.root / demo.FILES[0]).read_bytes()
        with self.assertRaises(FileExistsError):
            demo.initialize(self.root)
        self.assertEqual((self.root / demo.FILES[0]).read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
