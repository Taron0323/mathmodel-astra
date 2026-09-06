"""Run explicit local stages with content-addressed checkpoints and resumable evidence."""

import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import re
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


def group_alive(pgid):
    if not pgid:
        return False
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        pass
    # Orphan zombies can remain under container PID 1 but cannot write outputs.
    try:
        result = subprocess.run(["ps", "-A", "-o", "pgid=", "-o", "stat="],
                                capture_output=True, text=True, timeout=2, check=True)
        members = [line.split()[1] for line in result.stdout.splitlines()
                   if len(line.split()) == 2 and line.split()[0] == str(pgid)]
        return any(not status.startswith("Z") for status in members)
    except (OSError, subprocess.SubprocessError):
        return True


def terminate_group(child):
    previous = {sig: signal.signal(sig, signal.SIG_IGN) for sig in (signal.SIGINT, signal.SIGTERM)}
    try:
        for sig in (signal.SIGTERM, signal.SIGKILL):
            child.poll()
            if not group_alive(child.pid):
                return True
            try:
                os.killpg(child.pid, sig)
            except ProcessLookupError:
                pass
            except PermissionError:
                return not group_alive(child.pid)
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                child.poll()
                if not group_alive(child.pid):
                    return True
                time.sleep(.05)
        return False
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


def load_manifest(path):
    manifest = json.loads(path.read_text(encoding="utf-8"))
    root = path.parent.resolve()
    resource_root = Path(__file__).resolve().parent.parent
    tokens = {"python": sys.executable, "workspace": str(root), "skill": str(resource_root)}

    def expand(value):
        return re.sub(r"\{(python|workspace|skill)\}", lambda match: tokens[match.group(1)], value)

    stages = manifest["stages"]
    if not isinstance(stages, list) or not stages:
        raise ValueError("A workflow must contain at least one stage")
    producers, stage_ids, identities = {}, set(), {}
    declared_paths = {"inputs": [], "outputs": []}
    for stage in stages:
        key = stage["id"]
        if not isinstance(stage["outputs"], list) or not stage["outputs"]:
            raise ValueError("Each stage must declare at least one verifiable output")
        if not key.replace("_", "").replace("-", "").isalnum() or key in stage_ids:
            raise ValueError("Stage IDs must be unique path-free names")
        stage_ids.add(key)
        for field in ("inputs", "outputs"):
            normalized = []
            for name in stage[field]:
                target = (root / name).resolve()
                if (Path(name).is_absolute() or any(part in (".", "..") for part in name.split("/"))
                        or not target.is_relative_to(root) or target == root
                        or Path(name).parts[0] == ".workflow"
                        or target.relative_to(root).parts[0] == ".workflow"):
                    raise ValueError("Stage paths must stay within workspace, outside .workflow")
                canonical = target.relative_to(root).as_posix()
                normalized.append(canonical)
                declared_paths[field].append((name, canonical))
                if target.exists():
                    info = target.stat()
                    identity = (info.st_dev, info.st_ino)
                    identities.setdefault(identity, []).append((canonical, field))
            stage[field] = normalized
        for name in stage["outputs"]:
            if name in producers or name in stage["inputs"]:
                raise ValueError("Outputs must have one producer and cannot overwrite an input")
            producers[name] = key
        stage["command"] = [expand(part) for part in stage["command"]]
        stage["code"] = [str((root / expand(part)).resolve()) for part in stage.get("code", [])]
    for aliases in identities.values():
        if len({name for name, _ in aliases}) > 1 and any(field == "outputs" for _, field in aliases):
            raise ValueError("An output cannot alias another stage path through a hard link")
    available = set()
    for stage in stages:
        for name in stage["inputs"]:
            if name in producers and name not in available:
                raise ValueError("Stages must be listed in dependency order")
        available.update(stage["outputs"])
    raw_inputs = sorted({name for stage in stages for name in stage["inputs"] if name not in producers})
    raw_dirs = {Path(name).parts[0] for name in raw_inputs if len(Path(name).parts) > 1}
    raw_dirs.update(Path(name).parts[0] for name, canonical in declared_paths["inputs"]
                    if canonical in raw_inputs and len(Path(name).parts) > 1)
    if any(Path(name).parts[0] in raw_dirs or Path(canonical).parts[0] in raw_dirs
           for name, canonical in declared_paths["outputs"]):
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
            # A stage may have replaced an output directory with a symlink.
            # Archive the redirect itself without moving its target's files.
            for parent in reversed(source.parents):
                if parent.is_relative_to(root) and parent.is_symlink():
                    source = parent
                    name = source.relative_to(root).as_posix()
                    break
            if source.is_file() or source.is_symlink():
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
        if alive(lock.get("pid")) or group_alive(lock.get("pgid")):
            return {"status": "BLOCKED_RUNNING", "pid": lock["pid"]}, 3
        lock_path.unlink()
    state = json.loads(checkpoint_path.read_text()) if checkpoint_path.exists() else {"stages": {}}
    for key, record in state["stages"].items():
        pgid = record.get("pgid") or (record.get("pid") if record.get("status") == "RUNNING" else None)
        if group_alive(pgid):
            return {"status": "BLOCKED_LIVE_CHILD", "stage": key,
                    "pid": record.get("pid"), "pgid": pgid}, 3
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
    child, cleanup_complete = None, True

    def save(status):
        state.update({"status": status, "updated_at": stamp(), "run_id": run_id})
        json_write(checkpoint_path, state)
        metadata["status"] = status
        json_write(folder / "runs" / (run_id + ".json"), metadata)

    def on_signal(signum, frame):
        raise InterruptedError("Received signal " + str(signum))

    def changed_dependencies():
        changes = []
        for name, snapshot in sources.items():
            if not Path(name).is_file() or digest(name) != snapshot["sha256"]:
                changes.append({"path": name, "kind": snapshot["kind"], "stages": snapshot["stages"]})
        for stage in stages:
            if stage["id"] in verified:
                try:
                    signature, _ = stage_signature(stage, root, runtime, state["stages"], producers)
                    current = cached(stage, root, state["stages"][stage["id"]], signature)
                except (FileNotFoundError, IsADirectoryError):
                    current = False
                if not current:
                    changes.append({"stage": stage["id"], "kind": "COMPLETED_EVIDENCE_CHANGED",
                                    "stages": [stage["id"]]})
        try:
            current_stages = load_manifest(path)[2]
            if current_stages != stages:
                raise ValueError("Resolved stage paths or commands changed during the run")
        except (ValueError, KeyError, OSError, TypeError, IndexError, AttributeError) as exc:
            changes.append({"kind": "MANIFEST_PATHS_CHANGED", "error": str(exc),
                            "stages": [stage["id"] for stage in stages]})
        return changes

    def fail_dependencies(changes, key):
        affected_ids = {stage["id"] for stage in dependent_stages(stages, key)}
        for change in changes:
            for origin in change["stages"]:
                affected_ids.update(item["id"] for item in dependent_stages(stages, origin))
        affected = [stage for stage in stages if stage["id"] in affected_ids]
        quarantine(root, affected, run_id)
        for stage in affected:
            record = state["stages"].setdefault(stage["id"], {})
            record.update({"status": "FAILED" if stage["id"] == key else "INVALIDATED_DEPENDENCY_CHANGE",
                           "reason": "DEPENDENCY_CHANGED", "finished_at": stamp()})
        metadata["changed_dependencies"] = changes
        metadata["events"].append({"stage": key, "action": "FAILED", "reason": "DEPENDENCY_CHANGED"})
        save("FAILED")
        return {"status": "FAILED", "stage": key, "reason": "DEPENDENCY_CHANGED", "run_id": run_id}, 1

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
        sources, verified = {}, set()
        for stage in stages:
            for kind, names in (("RAW_INPUT", [str(root / name) for name in stage["inputs"] if name in raw]),
                                ("CODE", stage["code"])):
                for name in names:
                    snapshot = sources.setdefault(name, {"sha256": digest(name) if Path(name).is_file() else None,
                                                         "kind": kind, "stages": []})
                    snapshot["stages"].append(stage["id"])
        for kind, name in (("RUNNER", str(Path(__file__).resolve())), ("MANIFEST", str(path))):
            sources[name] = {"sha256": digest(name), "kind": kind, "stages": [stage["id"] for stage in stages]}
        metadata["registered_dependencies"] = sources
        save("RUNNING")
        for index, stage in enumerate(stages):
            key = stage["id"]
            changes = changed_dependencies()
            if changes:
                return fail_dependencies(changes, key)
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
                    record.update({"pid": child.pid, "pgid": child.pid})
                    save("RUNNING")
                    try:
                        returncode = child.wait(timeout=stage.get("timeout_seconds", 120))
                    except subprocess.TimeoutExpired:
                        cleanup_complete = terminate_group(child)
                        returncode = 124
                    if cleanup_complete and group_alive(child.pid):
                        cleanup_complete = terminate_group(child)
                        returncode = returncode or 125
                    if not cleanup_complete:
                        raise RuntimeError("Stage process group could not be terminated")
                child = None
                record.update({"finished_at": stamp(), "pid": None, "pgid": None, "returncode": returncode,
                               "log": str(log_path.relative_to(root))})
                changes = changed_dependencies()
                if changes:
                    return fail_dependencies(changes, key)
                outputs_exist = all((root / name).is_file() for name in stage["outputs"])
                if returncode or not outputs_exist:
                    record["status"] = "FAILED"
                    record["reason"] = "COMMAND_OR_OUTPUT_FAILURE"
                    quarantine(root, affected, run_id)
                    metadata["events"].append({"stage": key, "action": "FAILED", "returncode": returncode})
                    save("FAILED")
                    return {"status": "FAILED", "stage": key, "log": str(log_path), "run_id": run_id}, 1
                record.update({"status": "COMPLETE", "outputs": {name: digest(root / name) for name in stage["outputs"]}})
                metadata["events"].append({"stage": key, "action": "EXECUTED", "at": stamp()})
            verified.add(key)
            changes = changed_dependencies()
            if changes:
                return fail_dependencies(changes, key)
            save("RUNNING")
            if key == stop_after:
                save("PAUSED_AT_CHECKPOINT")
                return {"status": "PAUSED_AT_CHECKPOINT", "stage": key, "run_id": run_id}, 0
        changes = changed_dependencies()
        if changes:
            return fail_dependencies(changes, stages[-1]["id"])
        metadata["finished_at"] = stamp()
        save("COMPLETE")
        return {"status": "COMPLETE", "run_id": run_id, "events": metadata["events"]}, 0
    except (InterruptedError, KeyboardInterrupt) as exc:
        if child is not None and cleanup_complete:
            cleanup_complete = terminate_group(child)
        for key, record in state["stages"].items():
            if record.get("status") == "RUNNING":
                record.update({"status": "INTERRUPTED", "finished_at": stamp()})
                if cleanup_complete:
                    record.update({"pid": None, "pgid": None})
                    quarantine(root, [stage for stage in stages if stage["id"] == key], run_id)
        if cleanup_complete:
            child = None
        metadata["interruption"] = str(exc)
        save("INTERRUPTED" if cleanup_complete else "FAILED_PROCESS_CLEANUP")
        return {"status": metadata["status"], "run_id": run_id}, 130
    except Exception as exc:
        if child is not None and cleanup_complete:
            cleanup_complete = terminate_group(child)
            if cleanup_complete:
                for stage in stages:
                    record = state["stages"].get(stage["id"], {})
                    if record.get("pgid") == child.pid:
                        record.update({"pid": None, "pgid": None, "status": "FAILED"})
                        quarantine(root, [stage], run_id)
                child = None
        metadata["error"] = type(exc).__name__ + ": " + str(exc)
        save("FAILED" if cleanup_complete else "FAILED_PROCESS_CLEANUP")
        return {"status": metadata["status"], "error": metadata["error"], "run_id": run_id}, 1
    finally:
        if child is not None and cleanup_complete:
            cleanup_complete = terminate_group(child)
            if cleanup_complete:
                for stage in stages:
                    record = state["stages"].get(stage["id"], {})
                    if record.get("pgid") == child.pid:
                        record.update({"pid": None, "pgid": None})
                        if record.get("status") == "RUNNING":
                            record["status"] = "FAILED"
                        quarantine(root, [stage], run_id)
                save(metadata.get("status", "FAILED"))
        for sig, handler in old_handlers.items():
            signal.signal(sig, handler)
        if cleanup_complete:
            lock_path.unlink(missing_ok=True)
        else:
            json_write(lock_path, {"pid": os.getpid(), "pgid": child.pid,
                                   "status": "FAILED_PROCESS_CLEANUP", "run_id": run_id})


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
