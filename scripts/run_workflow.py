"""Run explicit local stages with content-addressed checkpoints and resumable evidence."""

import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import shutil
import signal
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone


def stamp():
    return datetime.now(timezone.utc).isoformat()


def digest(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def json_write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp-" + uuid.uuid4().hex)
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def alive(pid):
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def load_manifest(path):
    manifest = json.loads(path.read_text(encoding="utf-8"))
    root = path.parent.resolve()
    resource_root = Path(__file__).resolve().parent.parent
    tokens = {"python": sys.executable, "workspace": str(root), "skill": str(resource_root)}
    stages = manifest["stages"]
    if not isinstance(stages, list) or not stages:
        raise ValueError("A workflow must contain at least one stage")
    producers, stage_ids = {}, set()
    for stage in stages:
        key = stage["id"]
        if not isinstance(stage["outputs"], list) or not stage["outputs"]:
            raise ValueError("Each stage must declare at least one verifiable output")
        if not key.replace("_", "").replace("-", "").isalnum() or key in stage_ids:
            raise ValueError("Stage IDs must be unique path-free names")
        stage_ids.add(key)
        for field in ("inputs", "outputs"):
            for name in stage[field]:
                target = (root / name).resolve()
                if (Path(name).is_absolute() or any(part in (".", "..") for part in name.split("/"))
                        or not target.is_relative_to(root) or Path(name).parts[0] == ".workflow"):
                    raise ValueError("Stage paths must stay within workspace, outside .workflow")
        for name in stage["outputs"]:
            if name in producers or name in stage["inputs"]:
                raise ValueError("Outputs must have one producer and cannot overwrite an input")
            producers[name] = key
        stage["command"] = [part.format(**tokens) for part in stage["command"]]
        stage["code"] = [part.format(**tokens) for part in stage.get("code", [])]
    available = set()
    for stage in stages:
        for name in stage["inputs"]:
            if name in producers and name not in available:
                raise ValueError("Stages must be listed in dependency order")
        available.update(stage["outputs"])
    raw_inputs = sorted({name for stage in stages for name in stage["inputs"] if name not in producers})
    raw_dirs = {Path(name).parts[0] for name in raw_inputs if len(Path(name).parts) > 1}
    if any(Path(name).parts[0] in raw_dirs for name in producers):
        raise ValueError("Generated outputs cannot be placed in a raw-input directory")
    return manifest, root, stages, producers, raw_inputs


def environment(manifest):
    return {"python": sys.version, "executable": sys.executable, "platform": platform.platform(),
            "packages": {name: importlib.metadata.version(name) for name in manifest.get("packages", [])}}


def stage_signature(stage, root, runtime, checkpoints, producers):
    evidence = {"inputs": {name: digest(root / name) for name in stage["inputs"]},
                "code": {name: digest(name) for name in stage["code"]},
                "runner": digest(__file__), "environment": runtime,
                "command": stage["command"], "outputs": stage["outputs"],
                "upstream": {producers[name]: checkpoints.get(producers[name], {}).get("signature")
                             for name in stage["inputs"] if name in producers}}
    signature = hashlib.sha256(json.dumps(evidence, sort_keys=True).encode()).hexdigest()
    return signature, evidence


def cached(stage, root, record, signature):
    return (record.get("status") == "COMPLETE" and record.get("signature") == signature
            and set(record.get("outputs", {})) == set(stage["outputs"])
            and all((root / name).is_file() and digest(root / name) == value
                    for name, value in record.get("outputs", {}).items()))


def quarantine(root, stages, run_id):
    archive_id = uuid.uuid4().hex
    for stage in stages:
        for name in stage["outputs"]:
            source = root / name
            if source.is_file():
                target = root / ".workflow" / "stale" / run_id / archive_id / name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(source), target)


def dependent_stages(stages, changed):
    affected, outputs = [], set()
    for stage in stages:
        if stage["id"] == changed or set(stage["inputs"]) & outputs:
            affected.append(stage)
            outputs.update(stage["outputs"])
    return affected


def inspect(path):
    manifest, root, stages, producers, raw = load_manifest(path)
    checkpoint_path = root / ".workflow/checkpoint.json"
    state = json.loads(checkpoint_path.read_text()) if checkpoint_path.exists() else {"stages": {}}
    checks, runtime = {}, environment(manifest)
    for stage in stages:
        try:
            signature, _ = stage_signature(stage, root, runtime, state["stages"], producers)
            valid = cached(stage, root, state["stages"].get(stage["id"], {}), signature)
            valid = valid and all(checks[producers[name]] == "VERIFIED_CURRENT" for name in stage["inputs"] if name in producers)
            checks[stage["id"]] = "VERIFIED_CURRENT" if valid else "NEEDS_RUN"
        except FileNotFoundError:
            checks[stage["id"]] = "MISSING_DEPENDENCY"
    return {"recorded_status": state.get("status", "NOT_STARTED"), "current_evidence": checks,
            "all_current": all(value == "VERIFIED_CURRENT" for value in checks.values()),
            "missing_raw_inputs": [name for name in raw if not (root / name).is_file()]}


def run(path, stop_after=None):
    manifest, root, stages, producers, raw = load_manifest(path)
    if stop_after is not None and stop_after not in {stage["id"] for stage in stages}:
        raise ValueError("Unknown stop-after stage")
    folder = root / ".workflow"
    folder.mkdir(exist_ok=True)
    checkpoint_path, lock_path = folder / "checkpoint.json", folder / "lock.json"
    if lock_path.exists():
        lock = json.loads(lock_path.read_text())
        if alive(lock.get("pid")):
            return {"status": "BLOCKED_RUNNING", "pid": lock["pid"]}, 3
        lock_path.unlink()
    state = json.loads(checkpoint_path.read_text()) if checkpoint_path.exists() else {"stages": {}}
    for key, record in state["stages"].items():
        if record.get("status") == "RUNNING" and alive(record.get("pid")):
            return {"status": "BLOCKED_LIVE_CHILD", "stage": key, "pid": record["pid"]}, 3
    try:
        fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        return {"status": "BLOCKED_RUNNING"}, 3
    with os.fdopen(fd, "w") as stream:
        json.dump({"pid": os.getpid(), "started_at": stamp()}, stream)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:8]
    metadata = {"run_id": run_id, "started_at": stamp(), "runner_pid": os.getpid(),
                "mode": manifest.get("mode", "UNSPECIFIED"), "human_validation": "NOT_PERFORMED",
                "manifest_sha256": digest(path), "events": []}
    child = None

    def save(status):
        state.update({"status": status, "updated_at": stamp(), "run_id": run_id})
        json_write(checkpoint_path, state)
        metadata["status"] = status
        json_write(folder / "runs" / (run_id + ".json"), metadata)

    def on_signal(signum, frame):
        raise InterruptedError("Received signal " + str(signum))

    old_handlers = {sig: signal.signal(sig, on_signal) for sig in (signal.SIGINT, signal.SIGTERM)}
    try:
        runtime = environment(manifest)
        metadata["environment"] = runtime
        missing = [name for name in raw if not (root / name).is_file()]
        if missing:
            quarantine(root, stages, run_id)
            for record in state["stages"].values():
                record["status"] = "INVALIDATED_MISSING_INPUT"
            metadata["missing_inputs"] = missing
            save("BLOCKED_MISSING_INPUT")
            return {"status": metadata["status"], "missing_inputs": missing, "run_id": run_id}, 2
        raw_hashes = {name: digest(root / name) for name in raw}
        metadata["raw_inputs"] = raw_hashes
        save("RUNNING")
        for index, stage in enumerate(stages):
            key = stage["id"]
            record = state["stages"].get(key, {})
            signature, evidence = stage_signature(stage, root, runtime, state["stages"], producers)
            if cached(stage, root, record, signature):
                metadata["events"].append({"stage": key, "action": "REUSED", "at": stamp()})
            else:
                affected = dependent_stages(stages, key)
                quarantine(root, affected, run_id)
                for downstream in affected[1:]:
                    if downstream["id"] in state["stages"]:
                        state["stages"][downstream["id"]]["status"] = "INVALIDATED_UPSTREAM"
                record = {"status": "RUNNING", "signature": signature, "evidence": evidence,
                          "attempt": record.get("attempt", 0) + 1, "started_at": stamp(), "pid": None}
                state["stages"][key] = record
                save("RUNNING")
                log_path = folder / "logs" / run_id / (key + ".log")
                log_path.parent.mkdir(parents=True, exist_ok=True)
                with log_path.open("w", encoding="utf-8") as stream:
                    child = subprocess.Popen(stage["command"], cwd=root, stdout=stream, stderr=subprocess.STDOUT,
                                             start_new_session=True)
                    record["pid"] = child.pid
                    save("RUNNING")
                    try:
                        returncode = child.wait(timeout=stage.get("timeout_seconds", 120))
                    except subprocess.TimeoutExpired:
                        os.killpg(child.pid, signal.SIGTERM)
                        try:
                            child.wait(timeout=3)
                        except subprocess.TimeoutExpired:
                            os.killpg(child.pid, signal.SIGKILL)
                            child.wait()
                        returncode = 124
                child = None
                record.update({"finished_at": stamp(), "pid": None, "returncode": returncode,
                               "log": str(log_path.relative_to(root))})
                unchanged = all((root / name).is_file() and digest(root / name) == value for name, value in raw_hashes.items())
                outputs_exist = all((root / name).is_file() for name in stage["outputs"])
                if returncode or not unchanged or not outputs_exist:
                    record["status"] = "FAILED"
                    record["reason"] = "RAW_INPUT_CHANGED" if not unchanged else "COMMAND_OR_OUTPUT_FAILURE"
                    quarantine(root, affected, run_id)
                    metadata["events"].append({"stage": key, "action": "FAILED", "returncode": returncode})
                    save("FAILED")
                    return {"status": "FAILED", "stage": key, "log": str(log_path), "run_id": run_id}, 1
                record.update({"status": "COMPLETE", "outputs": {name: digest(root / name) for name in stage["outputs"]}})
                metadata["events"].append({"stage": key, "action": "EXECUTED", "at": stamp()})
            save("RUNNING")
            if key == stop_after:
                save("PAUSED_AT_CHECKPOINT")
                return {"status": "PAUSED_AT_CHECKPOINT", "stage": key, "run_id": run_id}, 0
        metadata["finished_at"] = stamp()
        save("COMPLETE")
        return {"status": "COMPLETE", "run_id": run_id, "events": metadata["events"]}, 0
    except (InterruptedError, KeyboardInterrupt) as exc:
        if child is not None and child.poll() is None:
            os.killpg(child.pid, signal.SIGTERM)
            try:
                child.wait(timeout=3)
            except subprocess.TimeoutExpired:
                os.killpg(child.pid, signal.SIGKILL)
                child.wait()
        for key, record in state["stages"].items():
            if record.get("status") == "RUNNING":
                record.update({"status": "INTERRUPTED", "pid": None, "finished_at": stamp()})
                quarantine(root, [stage for stage in stages if stage["id"] == key], run_id)
        metadata["interruption"] = str(exc)
        save("INTERRUPTED")
        return {"status": "INTERRUPTED", "run_id": run_id}, 130
    except Exception as exc:
        metadata["error"] = type(exc).__name__ + ": " + str(exc)
        save("FAILED")
        return {"status": "FAILED", "error": metadata["error"], "run_id": run_id}, 1
    finally:
        for sig, handler in old_handlers.items():
            signal.signal(sig, handler)
        lock_path.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["run", "resume", "status"])
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--stop-after", help="Pause after a completed stage; resume reuses verified stages")
    args = parser.parse_args()
    try:
        result, code = (inspect(args.manifest.resolve()), 0) if args.command == "status" else run(args.manifest.resolve(), args.stop_after)
    except (ValueError, KeyError, FileNotFoundError, importlib.metadata.PackageNotFoundError) as exc:
        result, code = {"status": "CONFIGURATION_ERROR", "error": str(exc)}, 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
