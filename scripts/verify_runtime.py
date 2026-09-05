"""Exercise missing inputs, checkpoints, SIGTERM recovery and invalidation in isolated practice workspaces."""

import argparse
import csv
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time

from run_workflow import alive, digest, json_write, quarantine
import transport_demo


SCRIPTS = Path(__file__).resolve().parent


def execute(script, *arguments, expected=0):
    result = subprocess.run([sys.executable, str(SCRIPTS / script), *map(str, arguments)],
                            capture_output=True, text=True, timeout=180)
    if result.returncode != expected:
        raise AssertionError(f"{script}: expected {expected}, got {result.returncode}\n{result.stdout}\n{result.stderr}")
    return json.loads(result.stdout)


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def state(root):
    return read(root / ".workflow/checkpoint.json")


def attempts(root):
    return {key: value["attempt"] for key, value in state(root)["stages"].items()}


def runner(root, command="run", *arguments, expected=0):
    return execute("run_workflow.py", command, "--manifest", root / "workflow.json", *arguments, expected=expected)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True, type=Path)
    args = parser.parse_args()
    root = args.workspace.resolve()
    if "practice" not in root.parts:
        raise ValueError("Behavioral validation must run below a practice directory")
    if root.exists() and any(root.iterdir()):
        raise FileExistsError("Use a new validation workspace; existing evidence is preserved")
    root.mkdir(parents=True, exist_ok=True)
    records = []

    def check(key, condition, evidence):
        status = "PASS" if condition else "FAIL"
        records.append({"check_id": key, "status": status, "evidence": evidence,
                        "actor": "AUTOMATED_CODE", "human_validation": "NOT_PERFORMED"})
        with (root / "runtime-validation.csv").open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(records[0]))
            writer.writeheader()
            writer.writerows(records)
        if not condition:
            raise AssertionError(key + ": " + evidence)

    for key, stages in [
        ("empty-stages", []),
        ("empty-outputs", [{"id": "empty", "inputs": [], "outputs": [], "code": [],
                            "command": ["{python}", "-c", "__import__('pathlib').Path('should-not-run').write_text('unexpected')"]}])
    ]:
        invalid = root / key
        json_write(invalid / "workflow.json", {"version": 1, "mode": "SYNTHETIC_PRACTICE", "stages": stages})
        invalid_result = runner(invalid, expected=2)
        check(key.replace("-", "_") + "_rejected", invalid_result["status"] == "CONFIGURATION_ERROR"
              and not (invalid / ".workflow/checkpoint.json").exists() and not (invalid / "should-not-run").exists(),
              key + ": invalid manifest rejected before command execution or any COMPLETE checkpoint")

    archive = root / "same-run-archive"
    artifact = archive / "results/output.csv"
    artifact.parent.mkdir(parents=True)
    archive_stages = [{"id": "fixture", "outputs": ["results/output.csv"]}]
    for content in ["version,cost\nfirst,25\n", "version,cost\nsecond,29\n"]:
        artifact.write_text(content, encoding="utf-8")
        quarantine(archive, archive_stages, "same-run-fixture")
    archived = list((archive / ".workflow/stale/same-run-fixture").rglob("output.csv"))
    check("same_run_archive_preserves_versions", len(archived) == 2 and not artifact.exists()
          and {path.read_text(encoding="utf-8") for path in archived} == {"version,cost\nfirst,25\n", "version,cost\nsecond,29\n"},
          "same-run-archive: two quarantine calls for one output path under the same run retain both distinct CSV versions")

    conflict = root / "init-conflict"
    json_write(conflict / "input/acceptance.json", {
        "evidence_class": "SYNTHETIC_PRACTICE", "fixture_owner": "INDEPENDENT_EXISTING_PRESET",
        "absolute_tolerance": 0.000125, "checks": ["retain_this_preexisting_acceptance"]})
    (conflict / "previous-result.csv").write_text("fixture,value\nhistorical,17\n", encoding="utf-8")
    existing = {str(path): (digest(path), path.stat().st_mtime_ns) for path in conflict.rglob("*") if path.is_file()}
    conflict_result = subprocess.run([sys.executable, str(SCRIPTS / "transport_demo.py"), "init", "--workspace", str(conflict)],
                                     capture_output=True, text=True, timeout=20)
    check("initialization_preserves_existing_preset", conflict_result.returncode == 1
          and "FileExistsError" in conflict_result.stderr
          and existing == {str(path): (digest(path), path.stat().st_mtime_ns) for path in conflict.rglob("*") if path.is_file()}
          and not (conflict / "workflow.json").exists() and not (conflict / "input/transport.json").exists(),
          "init-conflict: independently populated acceptance and historical result retain identical hashes and mtimes; init refuses before any demo file is created")

    missing = root / "missing-input"
    execute("transport_demo.py", "init", "--workspace", missing, "--missing-input")
    result = runner(missing, expected=2)
    check("missing_data_blocks", result["status"] == "BLOCKED_MISSING_INPUT" and not (missing / "results").exists(),
          "missing-input/.workflow/checkpoint.json: BLOCKED_MISSING_INPUT; no result files created")

    main = root / "main"
    execute("transport_demo.py", "init", "--workspace", main)
    before_raw = digest(main / "input/transport.json")
    paused = runner(main, "run", "--stop-after", "solve")
    before_attempts, before_csv = attempts(main), digest(main / "results/allocation.csv")
    check("checkpoint_stop_is_real", paused["status"] == "PAUSED_AT_CHECKPOINT" and set(before_attempts) == {"audit", "solve"}
          and not (main / "figures").exists(), "main: only audit and solve finished before resumption")
    resumed = runner(main, "resume")
    check("checkpoint_resume_reuses", resumed["status"] == "COMPLETE" and attempts(main)["solve"] == before_attempts["solve"]
          and digest(main / "results/allocation.csv") == before_csv,
          "main/.workflow/runs: audit and solve REUSED; validate, plot and report EXECUTED")
    files = [main / name for stage in read(main / "workflow.json")["stages"] for name in stage["outputs"]]
    before_files = {str(path): (digest(path), path.stat().st_mtime_ns) for path in files}
    before_attempts = attempts(main)
    repeated = runner(main, "resume")
    check("repeat_does_not_recompute", all(event["action"] == "REUSED" for event in repeated["events"])
          and attempts(main) == before_attempts
          and before_files == {str(path): (digest(path), path.stat().st_mtime_ns) for path in files},
          "main: all 5 stages REUSED; output hashes, modification times and attempt counts unchanged")
    check("raw_input_preserved", digest(main / "input/transport.json") == before_raw,
          "main/input/transport.json SHA-256 unchanged across initialization, run, resume and repeat")

    for name in ("empty-checks", "partial-checks", "changed-summary", "duplicate-check", "invalid-residual"):
        publication = root / ("publication-" + name)
        shutil.copytree(main, publication)
        rows = transport_demo.load_csv(publication, "evidence/validation.csv")
        if name == "changed-summary":
            summary = transport_demo.load_csv(publication, "results/summary.csv")
            summary[0]["objective"] = 1
            transport_demo.write_csv(publication / "results/summary.csv", summary)
        else:
            fields = list(rows[0])
            if name == "empty-checks":
                rows = []
            elif name == "partial-checks":
                rows = rows[:1]
            elif name == "duplicate-check":
                rows[-1] = rows[0].copy()
            else:
                rows[0]["observed_residual"] = "NaN"
            transport_demo.write_csv(publication / "evidence/validation.csv", rows, fields)
            if name in ("partial-checks", "duplicate-check", "invalid-residual"):
                record = read(publication / transport_demo.VALIDATION_STATE)
                record["files"]["evidence/validation.csv"] = digest(publication / "evidence/validation.csv")
                json_write(publication / transport_demo.VALIDATION_STATE, record)
        outputs = ("result.md", "evidence/claims.csv", "figures/transport.png", "figures/transport.svg")
        before_publication = {name: (digest(publication / name), (publication / name).stat().st_mtime_ns) for name in outputs}
        rejected = []
        for action in (transport_demo.report, transport_demo.plot):
            try:
                action(publication)
                rejected.append(False)
            except ValueError:
                rejected.append(True)
        check("publication_rejects_" + name.replace("-", "_"), all(rejected)
              and before_publication == {name: (digest(publication / name), (publication / name).stat().st_mtime_ns) for name in outputs},
              "publication-" + name + ": report and plot reject invalid/stale evidence before overwriting existing artifacts")

    failed_validation = root / "publication-failed-revalidation"
    shutil.copytree(main, failed_validation)
    original_summary = (failed_validation / "results/summary.csv").read_bytes()
    rows = transport_demo.load_csv(failed_validation, "results/summary.csv")
    rows[0]["objective"] = 1
    transport_demo.write_csv(failed_validation / "results/summary.csv", rows)
    try:
        transport_demo.validate(failed_validation)
    except RuntimeError:
        pass
    (failed_validation / "results/summary.csv").write_bytes(original_summary)
    revoked = read(failed_validation / transport_demo.VALIDATION_STATE)["status"] == "FAIL"
    try:
        transport_demo.report(failed_validation)
        revoked = False
    except ValueError:
        pass
    check("failed_revalidation_revokes_publication", revoked,
          "A failed validation writes FAIL state; restoring only the old CSV does not restore publication permission")
    transport_demo.validate(failed_validation)
    evidence_files = (*transport_demo.VALIDATION_SOURCES, *transport_demo.VALIDATION_ARTIFACTS, transport_demo.VALIDATION_STATE)
    evidence_before = {name: (digest(failed_validation / name), (failed_validation / name).stat().st_mtime_ns) for name in evidence_files}
    transport_demo.report(failed_validation)
    transport_demo.plot(failed_validation)
    check("publication_reuses_current_validation", evidence_before == {
        name: (digest(failed_validation / name), (failed_validation / name).stat().st_mtime_ns) for name in evidence_files},
        "Current evidence permits report and plot without solving again or rewriting validation")

    changed = root / "changed-input"
    execute("transport_demo.py", "init", "--workspace", changed)
    runner(changed)
    old_attempts = attempts(changed)
    old_raw = digest(changed / "input/transport.json")
    old_objective = (changed / "results/summary.csv").read_text()
    updated = read(changed / "input/transport.json")
    updated["cost"][0][2] = 5
    json_write(changed / "input/transport.json", updated)
    new_raw = digest(changed / "input/transport.json")
    stale_inspection = runner(changed, "status")
    check("status_detects_changed_input", not stale_inspection["all_current"] and old_raw != new_raw,
          "changed-input: status inspects current hashes and rejects old COMPLETE checkpoint as current evidence")
    rerun = runner(changed, "resume")
    check("changed_input_invalidates_dependents", all(event["action"] == "EXECUTED" for event in rerun["events"])
          and all(attempts(changed)[key] == count + 1 for key, count in old_attempts.items())
          and (changed / "results/summary.csv").read_text() != old_objective
          and digest(changed / "input/transport.json") == new_raw,
          "changed-input: explicit fixture change cost W1-D3 4 to 5; all related stages recomputed; objective 25 to 29; new raw retained")
    check("stale_outputs_preserved_separately", bool(list((changed / ".workflow/stale").rglob("summary.csv"))),
          "changed-input/.workflow/stale retains previous results as historical files")
    plot_before = attempts(changed)
    (changed / "figures/transport.svg").write_text("intentionally corrupted verification fixture\n", encoding="utf-8")
    runner(changed, "resume")
    plot_after = attempts(changed)
    check("corrupt_output_invalidates_only_dependents", all(plot_after[key] == plot_before[key] for key in ("audit", "solve", "validate"))
          and all(plot_after[key] == plot_before[key] + 1 for key in ("plot", "report")),
          "changed-input: corrupted SVG regenerated plot and report; audit, solve and mathematical validation reused")

    for corrupt_value in ("99", "nan"):
        wrong_ratio = root / ("wrong-relative-saving-" + corrupt_value)
        execute("transport_demo.py", "init", "--workspace", wrong_ratio)
        runner(wrong_ratio, "run", "--stop-after", "solve")
        summary_path = wrong_ratio / "results/summary.csv"
        with summary_path.open(newline="", encoding="utf-8") as stream:
            summary_rows = list(csv.DictReader(stream))
        summary_rows[0]["relative_saving"] = corrupt_value
        with summary_path.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(summary_rows[0]))
            writer.writeheader()
            writer.writerows(summary_rows)
        ratio_result = subprocess.run([sys.executable, str(SCRIPTS / "transport_demo.py"), "validate", "--workspace", str(wrong_ratio)],
                                     capture_output=True, text=True, timeout=20)
        with (wrong_ratio / "evidence/validation.csv").open(newline="", encoding="utf-8") as stream:
            ratio_checks = {row["check_id"]: row for row in csv.DictReader(stream)}
        check("relative_saving_" + corrupt_value + "_rejected", ratio_result.returncode == 1
              and ratio_checks["objective_recalculation"]["status"] == "FAIL"
              and float(ratio_checks["objective_recalculation"]["observed_residual"]) > 90
              and not (wrong_ratio / "result.md").exists(),
              "wrong-relative-saving-" + corrupt_value + ": changed only relative_saving; objective_recalculation rejects the value and no result prose is generated")

    def copy_solved_fixture(name):
        fixture = root / name
        for directory in ("input", "results"):
            shutil.copytree(main / directory, fixture / directory)
        return fixture

    for field, corrupt_value in (("warehouse", "WRONG_WAREHOUSE"), ("unit_cost", "999"), ("cost", "999")):
        wrong_allocation = copy_solved_fixture("wrong-allocation-" + field)
        allocation_rows = transport_demo.load_csv(wrong_allocation, "results/allocation.csv")
        allocation_rows[0][field] = corrupt_value
        transport_demo.write_csv(wrong_allocation / "results/allocation.csv", allocation_rows)
        allocation_result = subprocess.run(
            [sys.executable, str(SCRIPTS / "transport_demo.py"), "validate", "--workspace", str(wrong_allocation)],
            capture_output=True, text=True, timeout=20)
        if field == "warehouse":
            rejected = "ValueError: Allocation CSV must contain exactly the declared routes in canonical order" in allocation_result.stderr
            rejected = rejected and not (wrong_allocation / "evidence/validation.csv").exists()
        else:
            allocation_checks = {row["check_id"]: row for row in
                                 transport_demo.load_csv(wrong_allocation, "evidence/validation.csv")}
            rejected = allocation_checks["objective_recalculation"]["status"] == "FAIL"
            rejected = rejected and float(allocation_checks["objective_recalculation"]["observed_residual"]) > 900
        check("allocation_" + field + "_rejected", allocation_result.returncode == 1 and rejected
              and not (wrong_allocation / "result.md").exists(),
              "wrong-allocation-" + field + ": changed only one CSV field in copied solved outputs; validator rejects it and no result prose is generated")

    nonzero_at_zero_demand = copy_solved_fixture("nonzero-at-zero-demand")
    original_solve_lp = transport_demo.solve_lp
    injected_zero_results = []

    def solve_with_invalid_zero_allocation(supply, demand, cost):
        result = original_solve_lp(supply, demand, cost)
        if not demand.any():
            result.fun = 0.0
            result.x = result.x.copy()
            result.x[0] = 1.0
            injected_zero_results.append(result.success and result.fun == 0 and result.x[0] == 1)
        return result

    zero_error = None
    transport_demo.solve_lp = solve_with_invalid_zero_allocation
    try:
        transport_demo.validate(nonzero_at_zero_demand)
    except RuntimeError as error:
        zero_error = str(error)
    finally:
        transport_demo.solve_lp = original_solve_lp
    zero_checks = {row["check_id"]: row for row in
                   transport_demo.load_csv(nonzero_at_zero_demand, "evidence/validation.csv")}
    check("zero_demand_nonzero_allocation_rejected", injected_zero_results == [True]
          and zero_error == "Mathematical validation failed" and zero_checks["zero_demand"]["status"] == "FAIL"
          and float(zero_checks["zero_demand"]["observed_residual"]) == 1
          and not (nonzero_at_zero_demand / "result.md").exists(),
          "nonzero-at-zero-demand: injected success=True, fun=0 and x[0]=1 only for the zero-demand solver call; zero_demand fails with residual 1; original solver restored")

    interrupted = root / "interrupted"
    execute("transport_demo.py", "init", "--workspace", interrupted)
    manifest = read(interrupted / "workflow.json")
    barrier = interrupted / "barrier.py"
    barrier.write_text("import json, pathlib, time\np = pathlib.Path('evidence/barrier-started.json')\np.parent.mkdir(exist_ok=True)\np.write_text(json.dumps({'started': True}))\ntime.sleep(60)\npathlib.Path('evidence/barrier-finished.json').write_text('{}')\n", encoding="utf-8")
    manifest["stages"].insert(2, {"id": "barrier", "inputs": ["results/allocation.csv"],
        "outputs": ["evidence/barrier-started.json", "evidence/barrier-finished.json"],
        "code": [str(barrier)], "command": ["{python}", str(barrier)], "timeout_seconds": 90})
    json_write(interrupted / "workflow.json", manifest)
    command = [sys.executable, str(SCRIPTS / "run_workflow.py"), "run", "--manifest", str(interrupted / "workflow.json")]
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    child_pid = None
    try:
        deadline = time.monotonic() + 40
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise AssertionError("Runner exited before interrupt fixture became live")
            checkpoint = interrupted / ".workflow/checkpoint.json"
            if checkpoint.exists():
                record = state(interrupted)["stages"].get("barrier", {})
                if record.get("pid") and (interrupted / "evidence/barrier-started.json").exists():
                    child_pid = record["pid"]
                    break
            time.sleep(.05)
        if child_pid is None:
            raise AssertionError("No live barrier process observed")
        check("interrupt_target_was_live", alive(process.pid) and alive(child_pid),
              f"Observed runner PID {process.pid} and barrier PID {child_pid} before sending SIGTERM")
        process.send_signal(signal.SIGTERM)
        stdout, stderr = process.communicate(timeout=8)
        check("sigterm_records_interruption", process.returncode == 130 and state(interrupted)["status"] == "INTERRUPTED"
              and not alive(child_pid) and not (interrupted / "evidence/barrier-finished.json").exists(),
              "interrupted/.workflow/checkpoint.json: INTERRUPTED; child terminated; unfinished output absent")
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        if child_pid and alive(child_pid):
            os.kill(child_pid, signal.SIGKILL)
    barrier.write_text("import pathlib\np = pathlib.Path('evidence')\np.mkdir(exist_ok=True)\n(p / 'barrier-started.json').write_text('{}')\n(p / 'barrier-finished.json').write_text('{}')\n", encoding="utf-8")
    interruption_attempts = attempts(interrupted)
    after_interrupt = runner(interrupted, "resume")
    check("sigterm_resume_reuses_completed_work", after_interrupt["status"] == "COMPLETE"
          and all(attempts(interrupted)[key] == interruption_attempts[key] for key in ("audit", "solve"))
          and attempts(interrupted)["barrier"] == interruption_attempts["barrier"] + 1,
          "interrupted: verified audit/solve reused; interrupted barrier rerun after fixture delay removed; remaining stages finished")

    verification = runner(main, "status")
    check("full_chain_current", verification["all_current"] and (main / "result.md").is_file()
          and (main / "evidence/claims.csv").is_file(),
          "main: data, audit, solver CSVs, 12 mathematical checks, SVG/PNG, Chinese result prose and claim mappings all current")
    json_write(root / "runtime-summary.json", {"status": "PASS", "behavioral_checks": len(records),
        "evidence_class": "SYNTHETIC_PRACTICE", "human_validation": "NOT_PERFORMED",
        "runner_sha256": digest(SCRIPTS / "run_workflow.py"), "demo_sha256": digest(SCRIPTS / "transport_demo.py"),
        "verifier_sha256": digest(__file__), "raw_main_sha256": before_raw,
        "main_workflow": str(main / "workflow.json"), "processes_running": [],
        "scope": "Local deterministic transport demonstration and checkpoint runtime; not general scientific correctness or model-effort comparison"})
    print(json.dumps({"status": "PASS", "behavioral_checks": len(records), "workspace": str(root)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
