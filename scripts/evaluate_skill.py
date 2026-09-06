"""Run isolated, artifact-grounded mathmodel-astra behavior cases with Codex CLI."""

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
CASES = ROOT / "evals/behavior/cases.json"
IMPLEMENTATION_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
WORKER = """import json, os, time
from pathlib import Path
root = Path(__file__).resolve().parent
with (root / 'starts.jsonl').open('a') as stream:
    stream.write(json.dumps({'pid': os.getpid()}) + '\\n')
tick = 0
while True:
    tick += 1
    temporary = root / 'heartbeat.tmp'
    temporary.write_text(json.dumps({'pid': os.getpid(), 'tick': tick}))
    temporary.replace(root / 'heartbeat.json')
    time.sleep(0.2)
"""


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def snapshot(workspace, ignored=()):
    files = {}
    for path in sorted(workspace.rglob("*")):
        name = path.relative_to(workspace).as_posix()
        if name in ignored:
            continue
        if path.is_symlink():
            files[name] = {"type": "symlink", "target": os.readlink(path)}
        elif path.is_file():
            files[name] = {"type": "file", "sha256": digest(path), "size": path.stat().st_size}
        elif path.is_dir():
            files[name] = {"type": "directory"}
    return files


def inside_path(root, name):
    path = root / name
    if not name or Path(name).is_absolute() or ".." in Path(name).parts:
        raise ValueError(f"Invalid fixture path: {name}")
    path.resolve().relative_to(root.resolve())
    return path


def prepare_case(case, output):
    directory = output / case["id"]
    workspace = directory / "workspace"
    workspace.mkdir(parents=True)
    subprocess.run(["git", "-c", "init.templateDir=", "init", "--quiet", str(workspace)],
                   capture_output=True, text=True, check=True)
    for name, content in case["files"].items():
        path = inside_path(workspace, name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    skill = workspace / ".eval-skill"
    skill.mkdir()
    skill_source = output / ".skill-source"
    if not skill_source.is_dir():
        skill_source = ROOT
    shutil.copy2(skill_source / "SKILL.md", skill / "SKILL.md")
    shutil.copytree(skill_source / "references", skill / "references")
    prompt = (
        "这是已授权的独立合成 Skill 行为评测。当前工作根目录就是本案例全部项目。"
        "请读取并使用 .eval-skill/SKILL.md 的当前快照，按需读取该目录下的参考。"
        "所有项目事实只来自本工作区，不读上级项目，不读取或修改真实比赛目录。"
        "不联网、不安装软件、不修改全局配置、不调用其他任务。"
        "只修改本请求明确要求的文件；普通执行步骤无需再次请求许可。"
        "不要把演练结果称为正式比赛结果或人工核验。最终用简短中文报告实际产出及未决问题。\n\n"
        + case["request"] + "\n"
    )
    (directory / "prompt.txt").write_text(prompt, encoding="utf-8")
    save(directory / "case.json", case)
    save(directory / "before.json", snapshot(workspace))
    return directory


def stop_process(process):
    if process is None:
        return None
    if os.name == "posix":
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pass
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    else:
        process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)
    return process.returncode


def start_worker(workspace):
    run = workspace / "run"
    run.mkdir()
    script = run / "worker.py"
    script.write_text(WORKER, encoding="utf-8")
    process = subprocess.Popen([sys.executable, str(script)], cwd=workspace,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               start_new_session=True)
    deadline = time.monotonic() + 5
    while not (run / "heartbeat.json").exists() and time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("Fixture heartbeat process exited before initialization")
        time.sleep(0.05)
    if not (run / "heartbeat.json").is_file():
        stop_process(process)
        raise RuntimeError("Fixture heartbeat did not initialize within 5 seconds")
    save(workspace / "state/process.json", {"pid": process.pid, "started_at": now(),
                                         "heartbeat": "run/heartbeat.json"})
    return process


def subset(actual, expected):
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(key in actual and subset(actual[key], value)
                                               for key, value in expected.items())
    if isinstance(expected, list):
        return isinstance(actual, list) and len(actual) == len(expected) and all(
            subset(left, right) for left, right in zip(actual, expected))
    if type(expected) in (int, float):
        return type(actual) in (int, float) and actual == expected
    return type(actual) is type(expected) and actual == expected


def evaluate(case, workspace, before, after, worker_observation):
    checks = []

    def record(name, ok, evidence):
        checks.append({"check": name, "satisfied": bool(ok), "evidence": evidence})

    allowed = set(case["allowed_changes"])
    parents = set()
    for name in allowed:
        parents.update(str(parent) for parent in Path(name).parents if str(parent) != ".")
    volatile = {"run/heartbeat.json", "run/heartbeat.tmp"} if case.get("worker") else set()
    changed = sorted(name for name in set(before) | set(after) if before.get(name) != after.get(name))
    unexpected = [name for name in changed if name not in allowed | volatile
                  and not (name in parents and name not in before and after.get(name) == {"type": "directory"})]
    record("file_boundary", not unexpected, {"changed": changed, "unexpected": unexpected})
    for specification in case["checks"]:
        kind = specification["kind"]
        name = specification.get("path")
        try:
            path = inside_path(workspace, name) if name else None
            if path and (path.is_symlink() or not path.is_file()):
                raise ValueError("Expected a regular output file")
            if kind.startswith("json_") or kind == "worker":
                actual = json.loads(path.read_text(encoding="utf-8"))
            if kind == "json_subset":
                record(f"{kind}:{name}", subset(actual, specification["expected"]), actual)
            elif kind == "json_digest":
                expected = digest(inside_path(workspace, specification["source"]))
                record(f"{kind}:{name}", actual.get(specification["key"]) == expected,
                       {"expected_sha256": expected, "observed": actual.get(specification["key"])})
            elif kind == "json_nonempty":
                value = actual.get(specification["key"])
                record(f"{kind}:{name}", isinstance(value, str) and bool(value.strip()), value)
            elif kind == "contains":
                content = path.read_text(encoding="utf-8")
                missing = [term for term in specification["terms"] if term not in content]
                record(f"{kind}:{name}", not missing, {"missing": missing})
            elif kind == "changed":
                missing = [item for item in specification["paths"] if item not in changed]
                record(kind, not missing, {"unchanged_required_outputs": missing})
            elif kind == "polish":
                source = inside_path(workspace, specification["source"]).read_text(encoding="utf-8")
                content = path.read_text(encoding="utf-8")
                numbers = lambda text: Counter(re.findall(r"\d+(?:\.\d+)?", text))
                missing = [term for term in specification["required_terms"] if term not in content]
                record(f"{kind}:{name}", numbers(source) == numbers(content) and not missing
                       and content.strip() != source.strip() and 10 < len(content) < 3 * len(source),
                       {"source_numbers": numbers(source), "output_numbers": numbers(content),
                        "missing_terms": missing, "output_length": len(content)})
            elif kind == "worker":
                observation = worker_observation or {}
                tick = actual.get("observed_tick")
                ok = observation.get("alive_after") and actual.get("process_id") == observation.get("pid")
                ok = ok and type(tick) is int and 0 < tick <= observation.get("tick_after", 0)
                ok = ok and observation.get("tick_after", 0) > observation.get("tick_before", 0)
                ok = ok and observation.get("starts") == 1
                record(kind, ok, {"observed": observation, "output": actual})
            else:
                raise ValueError(f"Unknown check kind: {kind}")
        except (OSError, ValueError, KeyError, TypeError) as error:
            record(f"{kind}:{name}", False, {"error": str(error)})
    return checks


def run_case(case, args, cli, version):
    directory = prepare_case(case, args.output)
    workspace = directory / "workspace"
    worker = None
    process = None
    worker_observation = None
    report = {"case": case["id"], "started_at": now(), "cli_version": version,
              "harness_sha256": IMPLEMENTATION_SHA256,
              "requested_model": args.model, "requested_effort": args.effort,
              "upstream_model_identity": "NOT_INDEPENDENTLY_VERIFIED",
              "human_review": "NOT_PERFORMED", "manual_review": case["manual_review"],
              "repeat_count": 1, "control_without_skill": "NOT_RUN"}
    try:
        if case.get("worker"):
            worker = start_worker(workspace)
            heartbeat = json.loads((workspace / "run/heartbeat.json").read_text())
            worker_observation = {"pid": worker.pid, "tick_before": heartbeat["tick"]}
        ignored = {"run/heartbeat.tmp"} if case.get("worker") else set()
        before = snapshot(workspace, ignored=ignored)
        save(directory / "before.json", before)
        command = [cli, "exec", "--ephemeral", "--skip-git-repo-check", "--color", "never",
                   "--sandbox", "workspace-write", "--json", "-C", str(workspace),
                   "-m", args.model, "-c", f'model_reasoning_effort="{args.effort}"',
                   "-c", "project_doc_max_bytes=0", "-o", str(directory / "final.md"), "-"]
        report["command"] = command
        environment = os.environ.copy()
        if any(0xDC80 <= ord(char) <= 0xDCFF for char in environment.get("_", "")):
            environment.pop("_")
            report["environment_adjustment"] = "Removed invalid-Unicode _ from this child only"
        with (directory / "events.jsonl").open("w") as stdout, (directory / "stderr.log").open("w") as stderr:
            process = subprocess.Popen(command, cwd=workspace, stdin=subprocess.PIPE, stdout=stdout,
                                       stderr=stderr, text=True, env=environment, start_new_session=True)
            report["pid"] = process.pid
            save(directory / "request.json", report)
            try:
                process.communicate((directory / "prompt.txt").read_text(encoding="utf-8"), timeout=args.timeout)
            except subprocess.TimeoutExpired:
                report["timeout_seconds"] = args.timeout
                stop_process(process)
            report["exit_code"] = process.returncode
        if worker:
            heartbeat = json.loads((workspace / "run/heartbeat.json").read_text())
            worker_observation.update(alive_after=worker.poll() is None, tick_after=heartbeat["tick"],
                                      starts=len((workspace / "run/starts.jsonl").read_text().splitlines()))
        after = snapshot(workspace, ignored=ignored)
        save(directory / "after.json", after)
        report["checks"] = evaluate(case, workspace, before, after, worker_observation)
        report["automatic_status"] = "SATISFIED" if report["exit_code"] == 0 and all(
            item["satisfied"] for item in report["checks"]) else "NOT_SATISFIED"
        if report["exit_code"] != 0:
            report["execution_status"] = "TIMEOUT" if "timeout_seconds" in report else "CLI_FAILED"
        else:
            report["execution_status"] = "COMPLETED"
    except Exception as error:
        report.update(execution_status="HARNESS_ERROR", automatic_status="NOT_ASSESSED", error=str(error))
    finally:
        if process is not None and process.poll() is None:
            stop_process(process)
        if worker:
            report["worker_cleanup_exit_code"] = stop_process(worker)
        report["worker_observation"] = worker_observation
        report["finished_at"] = now()
        save(directory / "result.json", report)
    print(json.dumps({key: report[key] for key in ("case", "execution_status", "automatic_status")}), flush=True)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["prepare", "run"])
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--case", action="append")
    parser.add_argument("--cli", default="codex")
    parser.add_argument("--model", default="gpt-6-astra")
    parser.add_argument("--effort", choices=["low", "medium", "high", "xhigh", "max", "ultra"], default="max")
    parser.add_argument("--timeout", type=int, default=240)
    parser.add_argument("--jobs", type=int, choices=[1, 2], default=1)
    args = parser.parse_args()
    cases = json.loads(CASES.read_text(encoding="utf-8"))["cases"]
    selected = set(args.case or [case["id"] for case in cases])
    unknown = selected - {case["id"] for case in cases}
    if unknown:
        parser.error(f"Unknown cases: {sorted(unknown)}")
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    args.output = args.output.resolve()
    if args.output.exists() and any(args.output.iterdir()):
        parser.error("Output directory must be new or empty; existing evidence is never overwritten")
    args.output.mkdir(parents=True, exist_ok=True)
    cases = [case for case in cases if case["id"] in selected]
    skill_source = args.output / ".skill-source"
    skill_source.mkdir()
    shutil.copy2(ROOT / "SKILL.md", skill_source / "SKILL.md")
    shutil.copytree(ROOT / "references", skill_source / "references")
    save(args.output / "source-snapshot.json", {
        "harness_sha256": IMPLEMENTATION_SHA256,
        "case_manifest_sha256": digest(CASES),
        "skill_files": snapshot(skill_source),
    })
    if args.action == "prepare":
        for case in cases:
            prepare_case(case, args.output)
        save(args.output / "summary.json", {"status": "PREPARED_NOT_EXECUTED", "cases": list(selected)})
        return 0
    cli = shutil.which(args.cli)
    if cli is None:
        save(args.output / "summary.json", {"status": "BLOCKED", "reason": "Codex CLI not found", "cli": args.cli})
        return 2
    version = subprocess.run([cli, "--version"], capture_output=True, text=True, check=True).stdout.strip()
    with ThreadPoolExecutor(max_workers=args.jobs) as executor:
        reports = list(executor.map(lambda case: run_case(case, args, cli, version), cases))
    summary = {"created_at": now(), "case_manifest_sha256": digest(CASES), "cases": [
        {key: report[key] for key in ("case", "execution_status", "automatic_status")} for report in reports],
        "human_review": "NOT_PERFORMED", "repeat_count_per_case": 1, "control_without_skill": "NOT_RUN",
        "upstream_model_identity": "NOT_INDEPENDENTLY_VERIFIED"}
    save(args.output / "summary.json", summary)
    return 0 if all(report["automatic_status"] == "SATISFIED" for report in reports) else 1


if __name__ == "__main__":
    raise SystemExit(main())
