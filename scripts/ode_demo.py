"""Synthetic two-compartment transfer: numerical solve, independent validation and report."""

import argparse
import csv
import json
from pathlib import Path

import numpy as np
from scipy.integrate import solve_ivp

from run_workflow import digest, json_write, stamp


FILES = ("input/protocol.json", "results/trajectory.csv", "results/solver.json")
CHECKS = {"complete_grid", "finite_values", "initial_condition", "nonnegative", "mass_conservation",
          "analytic_accuracy", "independent_solver", "step_refinement", "zero_rate", "zero_mass"}


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_csv(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def read_csv(path):
    with path.open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def fingerprint(root):
    return {name: digest(root / name) for name in FILES} | {
        "implementation": digest(__file__), "runtime": digest(Path(__file__).with_name("run_workflow.py"))}


def specification(root):
    spec = read_json(root / FILES[0])
    expected = {"nominal": (10.0, 0.7), "zero_rate": (10.0, 0.0), "zero_mass": (0.0, 0.7)}
    if (spec.get("cases") != {key: {"mass": mass, "rate": rate} for key, (mass, rate) in expected.items()}
            or spec.get("max_steps") != [0.4, 0.2, 0.1] or spec.get("duration") != 4.0
            or spec.get("samples") != 81 or spec.get("acceptance") != {
                "absolute_error": 1e-4, "conservation": 1e-10, "independent_error": 1e-9}):
        raise ValueError("This fixed demonstration requires its preregistered protocol")
    return spec


def initialize(root):
    root.mkdir(parents=True, exist_ok=False)
    spec = {"evidence_class": "SYNTHETIC_PRACTICE", "equations": "dA/dt=-k*A; dB/dt=k*A",
            "time_unit": "s", "amount_unit": "g", "rate_unit": "1/s", "duration": 4.0, "samples": 81,
            "cases": {"nominal": {"mass": 10.0, "rate": 0.7}, "zero_rate": {"mass": 10.0, "rate": 0.0},
                      "zero_mass": {"mass": 0.0, "rate": 0.7}}, "max_steps": [0.4, 0.2, 0.1],
            "acceptance": {"absolute_error": 1e-4, "conservation": 1e-10, "independent_error": 1e-9},
            "randomness": "DETERMINISTIC", "human_validation": "NOT_PERFORMED"}
    json_write(root / FILES[0], spec)
    outputs = {"solve": list(FILES[1:]), "validate": ["evidence/validation.json"],
               "render": ["figures/transfer.png", "figures/transfer.svg", "evidence/figure.json",
                          "report.md", "claims.csv"]}
    stages = []
    for key in outputs:
        inputs = [FILES[0]] if key == "solve" else list(FILES)
        if key == "render":
            inputs.append("evidence/validation.json")
        stages.append({"id": key, "inputs": inputs, "outputs": outputs[key],
                       "code": ["{skill}/scripts/ode_demo.py"], "timeout_seconds": 60,
                       "command": ["{python}", "{skill}/scripts/ode_demo.py", key, "--workspace", "{workspace}"]})
    json_write(root / "workflow.json", {"mode": "SYNTHETIC_PRACTICE", "packages": ["numpy", "scipy", "matplotlib"],
                                       "stages": stages})


def solve(root):
    spec = specification(root)
    times = np.linspace(0, spec["duration"], spec["samples"])
    rows, runs = [], []
    for name, case in spec["cases"].items():
        for step in spec["max_steps"]:
            rate, mass = case["rate"], case["mass"]
            solution = solve_ivp(lambda t, y: [-rate * y[0], rate * y[0]], (0, times[-1]), [mass, 0.0],
                                 method="RK23", t_eval=times, max_step=step, first_step=step, rtol=1, atol=1)
            if not solution.success or solution.y.shape != (2, len(times)):
                raise RuntimeError("Numerical integration did not complete")
            rows.extend({"case": name, "max_step_s": step, "time_s": float(t), "A_g": float(a), "B_g": float(b)}
                        for t, a, b in zip(solution.t, *solution.y))
            runs.append({"case": name, "max_step_s": step, "method": "RK23", "nfev": solution.nfev,
                         "status": int(solution.status), "message": solution.message})
    write_csv(root / FILES[1], rows)
    json_write(root / FILES[2], {"runs": runs, "relative_tolerance": 1, "absolute_tolerance": 1,
                               "note": "Loose adaptive tolerances isolate the max-step refinement experiment"})


def validate(root):
    output = root / "evidence/validation.json"
    json_write(output, {"status": "FAIL", "reason": "Validation has not completed"})
    spec, rows = specification(root), read_csv(root / FILES[1])
    times = np.linspace(0, spec["duration"], spec["samples"])
    checks = {key: True for key in CHECKS}
    expected = {(name, step, float(t)) for name in spec["cases"] for step in spec["max_steps"] for t in times}
    identity = [(r["case"], float(r["max_step_s"]), float(r["time_s"])) for r in rows]
    checks["complete_grid"] = len(identity) == len(expected) and set(identity) == expected
    if not checks["complete_grid"]:
        raise ValueError("Expected each registered case, step and sample exactly once")
    errors, mass_residuals = {}, []
    for name, case in spec["cases"].items():
        mass, rate = case["mass"], case["rate"]
        # The reference integrates a matrix system with a different high-order method.
        matrix = np.array([[-rate, 0], [rate, 0]])
        reference = solve_ivp(lambda t, y: matrix @ y, (0, times[-1]), [mass, 0], t_eval=times,
                              method="DOP853", rtol=1e-12, atol=1e-13)
        exact = np.array([mass * np.exp(-rate * times), -mass * np.expm1(-rate * times)])
        checks["independent_solver"] &= bool(reference.success and reference.y.shape == exact.shape
                                               and np.max(np.abs(reference.y - exact)) < spec["acceptance"]["independent_error"])
        for step in spec["max_steps"]:
            group = sorted((r for r in rows if r["case"] == name and float(r["max_step_s"]) == step),
                           key=lambda r: float(r["time_s"]))
            values = np.array([[float(r["A_g"]), float(r["B_g"])] for r in group]).T
            finite = bool(np.isfinite(values).all())
            checks["finite_values"] &= finite
            if not finite:
                raise ValueError("Trajectory contains nonfinite values")
            error = float(np.max(np.abs(values - exact)))
            errors[name + ":" + str(step)] = error
            residual = float(np.max(np.abs(values.sum(axis=0) - mass)))
            mass_residuals.append(residual)
            checks["initial_condition"] &= bool(np.array_equal(values[:, 0], [mass, 0]))
            checks["nonnegative"] &= bool(np.min(values) >= -spec["acceptance"]["conservation"])
            checks["mass_conservation"] &= residual <= spec["acceptance"]["conservation"]
            if step == min(spec["max_steps"]):
                checks["analytic_accuracy"] &= error <= spec["acceptance"]["absolute_error"]
            if name in ("zero_rate", "zero_mass"):
                checks[name] &= error <= spec["acceptance"]["conservation"]
    nominal = [errors["nominal:" + str(step)] for step in spec["max_steps"]]
    checks["step_refinement"] = nominal[2] < nominal[1] < nominal[0]
    record = {"status": "PASS" if all(checks.values()) else "FAIL", "checked_at": stamp(),
              "evidence_class": "SYNTHETIC_PRACTICE", "human_validation": "NOT_PERFORMED",
              "checks": [{"check_id": key, "status": "PASS" if value else "FAIL"} for key, value in sorted(checks.items())],
              "max_errors_g": errors, "max_mass_residual_g": max(mass_residuals), "files": fingerprint(root)}
    json_write(output, record)
    if record["status"] != "PASS":
        raise ValueError("ODE validation failed")
    return record


def current_evidence(root):
    record = read_json(root / "evidence/validation.json")
    checks = record.get("checks", [])
    if (record.get("status") != "PASS" or record.get("files") != fingerprint(root)
            or len(checks) != len(CHECKS) or {r.get("check_id") for r in checks} != CHECKS
            or any(r.get("status") != "PASS" for r in checks)):
        raise ValueError("Rendering requires complete current ODE evidence")
    spec, rows = specification(root), read_csv(root / FILES[1])
    errors, residuals = {}, []
    for name, case in spec["cases"].items():
        for step in spec["max_steps"]:
            group = sorted((r for r in rows if r["case"] == name and float(r["max_step_s"]) == step),
                           key=lambda r: float(r["time_s"]))
            times = np.array([float(r["time_s"]) for r in group])
            values = np.array([[float(r["A_g"]), float(r["B_g"])] for r in group]).T
            exact = np.array([case["mass"] * np.exp(-case["rate"] * times),
                              -case["mass"] * np.expm1(-case["rate"] * times)])
            errors[name + ":" + str(step)] = float(np.max(np.abs(values - exact)))
            residuals.append(float(np.max(np.abs(values.sum(axis=0) - case["mass"]))))
    if record.get("max_errors_g") != errors or record.get("max_mass_residual_g") != max(residuals):
        raise ValueError("Validation summary does not match the saved trajectory")
    return record


def render(root):
    record = current_evidence(root)
    spec = specification(root)
    rows = read_csv(root / FILES[1])
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    root.joinpath("figures").mkdir(exist_ok=True)
    json_write(root / "evidence/figure.json", {"status": "FAIL", "reason": "Rendering has not completed"})
    fig, axes = plt.subplots(1, 2, figsize=(10, 4), layout="constrained")
    nominal = sorted((r for r in rows if r["case"] == "nominal" and float(r["max_step_s"]) == 0.1),
                     key=lambda r: float(r["time_s"]))
    for key, label, color in (("A_g", "A", "#287A78"), ("B_g", "B", "#B95B43")):
        axes[0].plot([float(r["time_s"]) for r in nominal], [float(r[key]) for r in nominal], label=label, color=color)
    axes[0].set(xlabel="Time (s)", ylabel="Amount (g)", title="Two-compartment transfer")
    axes[0].legend(frameon=False)
    steps = spec["max_steps"]
    errors = [record["max_errors_g"]["nominal:" + str(step)] for step in steps]
    axes[1].loglog(steps, errors, "o-", color="#287A78")
    axes[1].set(xlabel="Maximum step (s)", ylabel="Maximum absolute error (g)", title="RK23 step refinement")
    for axis in axes:
        axis.spines[["top", "right"]].set_visible(False)
    for suffix in ("png", "svg"):
        fig.savefig(root / ("figures/transfer." + suffix), dpi=160)
    plt.close(fig)
    if record != current_evidence(root):
        raise ValueError("Evidence changed while rendering")
    images = {name: digest(root / name) for name in ("figures/transfer.png", "figures/transfer.svg")}
    json_write(root / "evidence/figure.json", {"status": "GENERATED", "files": images,
               "validation_sha256": digest(root / "evidence/validation.json"), "implementation": digest(__file__),
               "visual_review": "NOT_PERFORMED"})
    write_csv(root / "claims.csv", [{"claim_id": "ODE-ERROR", "statement": "Fine-step maximum absolute error",
              "value": errors[-1], "unit": "g", "result_file": FILES[1], "code_file": "scripts/ode_demo.py",
              "verification_file": "evidence/validation.json", "figure_file": "figures/transfer.svg",
              "paper_location": "report.md", "human_review": "NOT_PERFORMED", "status": "AI_VERIFIED"}])
    report = ("# 合成双室转移演练\n\n"
              "系统满足 dA/dt=-kA、dB/dt=kA，初始总量为 10 g，k=0.7 /s，模拟时长为 4 s。"
              "解析解为 A(t)=10 exp(-0.7t)、B(t)=10-A(t)。\n\n"
              f"RK23 的最大步长从 0.4 s 降至 0.2 s、0.1 s 时，最大绝对误差依次为 "
              f"{errors[0]:.6g} g、{errors[1]:.6g} g、{errors[2]:.6g} g。"
              f"总量守恒最大残差为 {record['max_mass_residual_g']:.6g} g。\n\n"
              "DOP853 与解析解完成独立对照；零转移率、零初始总量两类退化条件通过检验。"
              "本例验证给定线性系统与数值流程，不涉及参数拟合、噪声区间或真实赛题。\n\n"
              "![转移与步长误差](figures/transfer.png)\n")
    root.joinpath("report.md").write_text(report, encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("init", "solve", "validate", "render"))
    parser.add_argument("--workspace", type=Path, required=True)
    args = parser.parse_args()
    functions = {"init": initialize, "solve": solve, "validate": validate, "render": render}
    functions[args.command](args.workspace.resolve())


if __name__ == "__main__":
    main()
