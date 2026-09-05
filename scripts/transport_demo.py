"""Synthetic transport LP demonstration; never an official competition answer."""

import argparse
import csv
import itertools
import json
from pathlib import Path

import numpy as np
from scipy.optimize import linprog

from run_workflow import digest, json_write


def write_csv(path, rows, fields=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields or list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def read_json(root, name):
    return json.loads((root / name).read_text(encoding="utf-8"))


def instance(root):
    value = read_json(root, "input/transport.json")
    if value.get("evidence_class") != "SYNTHETIC_PRACTICE":
        raise ValueError("This demo requires explicitly synthetic input")
    supply, demand, cost = (np.array(value[key], dtype=float) for key in ("supply", "demand", "cost"))
    if supply.shape != (2,) or demand.shape != (3,) or cost.shape != (2, 3):
        raise ValueError("The independent enumeration demo supports 2 warehouses and 3 destinations")
    if any(not np.all(np.isfinite(x)) or np.any(x < 0) for x in (supply, demand, cost)):
        raise ValueError("Supply, demand and costs must be finite and nonnegative")
    if any(not np.array_equal(x, np.round(x)) for x in (supply, demand)) or demand.sum() > 100:
        raise ValueError("Enumeration requires integer units and at most 100 total demand")
    if supply.sum() < demand.sum():
        raise ValueError("Insufficient total supply")
    return value, supply, demand, cost


def solve_lp(supply, demand, cost):
    rows, cols = cost.shape
    a_supply = np.kron(np.eye(rows), np.ones((1, cols)))
    a_demand = np.tile(np.eye(cols), (1, rows))
    return linprog(cost.ravel(), A_ub=a_supply, b_ub=supply, A_eq=a_demand, b_eq=demand,
                   bounds=(0, None), method="highs")


def greedy(supply, demand):
    remaining_supply, remaining_demand = supply.copy(), demand.copy()
    allocation = np.zeros((len(supply), len(demand)))
    for i in range(len(supply)):
        for j in range(len(demand)):
            allocation[i, j] = min(remaining_supply[i], remaining_demand[j])
            remaining_supply[i] -= allocation[i, j]
            remaining_demand[j] -= allocation[i, j]
    return allocation


def enumerate_integer(supply, demand, cost):
    best, count = float("inf"), 0
    for first in itertools.product(*(range(int(value) + 1) for value in demand)):
        if sum(first) > supply[0] or demand.sum() - sum(first) > supply[1]:
            continue
        second = [int(demand[j]) - first[j] for j in range(3)]
        objective = sum(cost[0, j] * first[j] + cost[1, j] * second[j] for j in range(3))
        best, count = min(best, objective), count + 1
    return best, count


def init(root, missing=False):
    if "practice" not in root.parts:
        raise ValueError("Synthetic demonstration must be initialized below a practice directory")
    if root.exists() and any(root.iterdir()):
        raise FileExistsError("Existing workspace contents are preserved; use a new empty workspace")
    root.mkdir(parents=True, exist_ok=True)
    if not missing:
        json_write(root / "input/transport.json", {
            "evidence_class": "SYNTHETIC_PRACTICE", "quantity_unit": "crate", "cost_unit": "synthetic cost unit per crate",
            "warehouses": ["W1", "W2"], "destinations": ["D1", "D2", "D3"],
            "supply": [4, 5], "demand": [2, 3, 4], "cost": [[2, 7, 4], [3, 1, 6]],
            "assumptions": ["Divisible shipments", "Linear unit costs", "No route capacity except warehouse supply",
                            "Synthetic inputs; no real deployment or competition interpretation"]})
    json_write(root / "input/acceptance.json", {
        "evidence_class": "SYNTHETIC_PRACTICE", "absolute_tolerance": 1e-8,
        "quantity_unit": "crate", "objective_unit": "synthetic cost unit",
        "checks": ["nonnegativity", "demand_equality", "supply_capacity", "objective_recalculation",
                   "independent_integer_enumeration", "baseline_feasible", "baseline_dominance", "order_of_magnitude",
                   "zero_demand", "insufficient_supply_infeasible", "permutation_invariance", "cost_scale_covariance"],
        "enumeration_scope": "Two-source transportation polytope with integer supply and demand; integer optimum also attains LP optimum",
        "randomness": "Not applicable: deterministic LP and exhaustive enumeration", "human_validation": "NOT_PERFORMED"})
    script = "{skill}/scripts/transport_demo.py"
    definitions = [
        ("audit", ["input/transport.json", "input/acceptance.json"], ["clean/audit.json"]),
        ("solve", ["input/transport.json", "clean/audit.json"], ["results/allocation.csv", "results/summary.csv", "results/solver.json"]),
        ("validate", ["input/transport.json", "input/acceptance.json", "results/allocation.csv", "results/summary.csv"],
         ["evidence/validation.csv", "evidence/enumeration.json"]),
        ("plot", ["results/allocation.csv", "results/summary.csv", "evidence/validation.csv"], ["figures/transport.png", "figures/transport.svg"]),
        ("report", ["input/transport.json", "results/summary.csv", "evidence/validation.csv", "figures/transport.svg"],
         ["result.md", "evidence/claims.csv", "evidence/ai-use.json"]) ]
    json_write(root / "workflow.json", {"version": 1, "mode": "SYNTHETIC_PRACTICE",
        "packages": ["numpy", "scipy", "matplotlib"],
        "stages": [{"id": key, "inputs": inputs, "outputs": outputs, "code": [script],
                    "command": ["{python}", script, key, "--workspace", "{workspace}"], "timeout_seconds": 120}
                   for key, inputs, outputs in definitions]})
    print(json.dumps({"status": "INITIALIZED", "workspace": str(root), "missing_data_fixture": missing}))


def audit(root):
    value, supply, demand, cost = instance(root)
    checks = read_json(root, "input/acceptance.json")
    if checks["absolute_tolerance"] <= 0:
        raise ValueError("Tolerance must be registered before execution")
    json_write(root / "clean/audit.json", {
        "evidence_class": value["evidence_class"], "raw_sha256": digest(root / "input/transport.json"),
        "schema": "2x3 finite nonnegative transportation data", "total_supply": float(supply.sum()),
        "total_demand": float(demand.sum()), "capacity_margin": float(supply.sum() - demand.sum()),
        "objective_lower_bound": float(cost.min() * demand.sum()),
        "objective_upper_bound": float(cost.max() * demand.sum()),
        "bounds_unit": "synthetic cost unit", "derived_bounds_not_result": True,
        "acceptance_sha256": digest(root / "input/acceptance.json"), "validation_actor": "AUTOMATED_CODE"})


def solve(root):
    value, supply, demand, cost = instance(root)
    solution = solve_lp(supply, demand, cost)
    if not solution.success:
        raise RuntimeError(solution.message)
    allocation, baseline = solution.x.reshape(cost.shape), greedy(supply, demand)
    rows = [{"warehouse": value["warehouses"][i], "destination": value["destinations"][j],
             "quantity_crate": float(allocation[i, j]), "unit_cost": float(cost[i, j]),
             "cost": float(allocation[i, j] * cost[i, j]), "baseline_quantity_crate": float(baseline[i, j])}
            for i in range(2) for j in range(3)]
    write_csv(root / "results/allocation.csv", rows)
    objective, baseline_cost = float(solution.fun), float((baseline * cost).sum())
    write_csv(root / "results/summary.csv", [{"evidence_class": "SYNTHETIC_PRACTICE", "objective": objective,
        "baseline_objective": baseline_cost, "absolute_saving": baseline_cost - objective,
        "relative_saving": (baseline_cost - objective) / baseline_cost if baseline_cost else 0,
        "objective_unit": "synthetic cost unit", "total_shipped_crate": float(allocation.sum())}])
    json_write(root / "results/solver.json", {"solver": "scipy.optimize.linprog", "method": "highs", "status": solution.status,
        "message": solution.message, "iterations": int(solution.nit), "objective": objective,
        "optimality_scope": "Global optimum of the declared continuous linear program to numerical tolerance",
        "input_sha256": digest(root / "input/transport.json"), "code_sha256": digest(__file__)})


def load_csv(root, name):
    with (root / name).open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def validate(root):
    value, supply, demand, cost = instance(root)
    tolerance = read_json(root, "input/acceptance.json")["absolute_tolerance"]
    rows = load_csv(root, "results/allocation.csv")
    expected_routes = [(warehouse, destination) for warehouse in value["warehouses"]
                       for destination in value["destinations"]]
    if [(row["warehouse"], row["destination"]) for row in rows] != expected_routes:
        raise ValueError("Allocation CSV must contain exactly the declared routes in canonical order")
    allocation = np.array([float(row["quantity_crate"]) for row in rows]).reshape(2, 3)
    baseline = np.array([float(row["baseline_quantity_crate"]) for row in rows]).reshape(2, 3)
    summary = load_csv(root, "results/summary.csv")[0]
    objective = float(summary["objective"])
    exact, count = enumerate_integer(supply, demand, cost)
    validations = []

    def add(key, residual, limit, evidence):
        validations.append({"check_id": key, "status": "PASS" if residual <= limit else "FAIL",
                            "observed_residual": float(residual), "tolerance": limit,
                            "evidence": evidence, "validation_actor": "AUTOMATED_CODE", "human_check": "NOT_PERFORMED"})

    add("nonnegativity", max(0, -allocation.min()), tolerance, "Minimum shipment >= 0 crates")
    add("demand_equality", np.max(np.abs(allocation.sum(axis=0) - demand)), tolerance, "Destination totals equal required crates")
    add("supply_capacity", max(0, np.max(allocation.sum(axis=1) - supply)), tolerance, "Warehouse totals <= available crates")
    calculated_baseline = float((baseline * cost).sum())
    row_unit_costs = np.array([float(row["unit_cost"]) for row in rows]).reshape(cost.shape)
    row_costs = np.array([float(row["cost"]) for row in rows]).reshape(cost.shape)
    recalculation = max(abs(float((allocation * cost).sum()) - objective),
                        np.max(np.abs(row_unit_costs - cost)),
                        np.max(np.abs(row_costs - allocation * cost)),
                        abs(calculated_baseline - float(summary["baseline_objective"])),
                        abs(calculated_baseline - objective - float(summary["absolute_saving"])),
                        abs(((calculated_baseline - objective) / calculated_baseline if calculated_baseline else 0)
                            - float(summary["relative_saving"])),
                        abs(allocation.sum() - float(summary["total_shipped_crate"])))
    if (not np.all(np.isfinite(row_unit_costs)) or not np.all(np.isfinite(row_costs))
            or not all(np.isfinite(float(summary[key])) for key in
                       ("objective", "baseline_objective", "absolute_saving", "relative_saving", "total_shipped_crate"))):
        recalculation = float("inf")
    add("objective_recalculation", recalculation, tolerance, "Require canonical routes and finite values; recalculate line unit costs, line costs, objective, baseline, absolute/relative savings and shipment total")
    add("independent_integer_enumeration", abs(exact - objective), tolerance, f"Exhaustively evaluated {count} feasible integer allocations")
    add("baseline_feasible", max(np.max(np.abs(baseline.sum(axis=0) - demand)), np.max(baseline.sum(axis=1) - supply), -baseline.min()), tolerance, "Same demand and supply constraints as optimized solution")
    add("baseline_dominance", max(0, objective - float((baseline * cost).sum())), tolerance, "Optimized cost no larger than feasible greedy baseline")
    add("order_of_magnitude", max(0, float(cost.min() * demand.sum()) - objective, objective - float(cost.max() * demand.sum())), tolerance, "Bounds derived and registered in audit before solving")
    zero = solve_lp(supply, np.zeros_like(demand), cost)
    add("zero_demand", max(abs(zero.fun), np.max(np.abs(zero.x))) if zero.success else 1,
        tolerance, "Zero demand must yield zero allocation and cost")
    infeasible = solve_lp(np.zeros_like(supply), np.ones_like(demand), cost)
    add("insufficient_supply_infeasible", 0 if infeasible.status == 2 else 1, 0, "HiGHS status 2 for zero supply and positive demand")
    permuted = solve_lp(supply[::-1], demand[[2, 0, 1]], cost[::-1][:, [2, 0, 1]])
    add("permutation_invariance", abs(permuted.fun - objective) if permuted.success else 1e9, tolerance, "Relabeling sources and destinations preserves minimum cost")
    scaled = solve_lp(supply, demand, cost * 10)
    add("cost_scale_covariance", abs(scaled.fun - objective * 10) if scaled.success else 1e9, tolerance, "Tenfold unit costs imply tenfold optimal cost")
    expected = set(read_json(root, "input/acceptance.json")["checks"])
    if expected != {row["check_id"] for row in validations}:
        raise ValueError("Validation coverage differs from preregistered acceptance checks")
    write_csv(root / "evidence/validation.csv", validations)
    json_write(root / "evidence/enumeration.json", {"objective": exact, "feasible_allocations": count,
        "scope": "2x3 integer-supply integer-demand transport LP; total-unimodular constraints imply an integer optimum",
        "approach": "Enumerate W1 allocations; W2 determined by demand conservation; no optimizer called in enumeration",
        "random_stability": {"status": "NOT_APPLICABLE", "reason": "Deterministic LP and exhaustive enumeration"},
        "real_world_validity": {"status": "NOT_ASSESSED", "reason": "Synthetic inputs do not establish deployment validity"}})
    if any(row["status"] != "PASS" for row in validations):
        raise RuntimeError("Mathematical validation failed")


def plot(root):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    rows = load_csv(root, "results/allocation.csv")
    summary = load_csv(root, "results/summary.csv")[0]
    allocation = np.array([float(row["quantity_crate"]) for row in rows]).reshape(2, 3)
    warehouses = list(dict.fromkeys(row["warehouse"] for row in rows))
    destinations = list(dict.fromkeys(row["destination"] for row in rows))
    if any(row["status"] != "PASS" for row in load_csv(root, "evidence/validation.csv")):
        raise ValueError("Figure publication requires passing mathematical validation")
    plt.rcParams.update({"font.size": 11, "axes.spines.top": False, "axes.spines.right": False,
                         "svg.hashsalt": "mathmodel-astra-transport-demo"})
    fig, axes = plt.subplots(1, 2, figsize=(10.6, 4.0), layout="constrained", gridspec_kw={"width_ratios": [1.5, 1]})
    bottom = np.zeros(3)
    for index, color in enumerate(["#287A78", "#B95B43"]):
        bars = axes[0].bar(destinations, allocation[index], bottom=bottom, label=warehouses[index], color=color, width=.58)
        for bar, height in zip(bars, allocation[index]):
            if height:
                axes[0].text(bar.get_x() + bar.get_width()/2, bar.get_y() + height/2, f"{height:g}", ha="center", va="center", color="white", weight="bold")
        bottom += allocation[index]
    axes[0].set(title="Optimized allocation", ylabel="Shipment (crates)", xlabel="Destination", ylim=(0, max(1, bottom.max() * 1.25)))
    axes[0].legend(frameon=False, ncol=2, loc="upper left")
    costs = [float(summary["baseline_objective"]), float(summary["objective"])]
    bars = axes[1].bar(["Greedy baseline", "Linear program"], costs, color=["#8C8F91", "#287A78"], width=.6)
    axes[1].bar_label(bars, labels=[f"{value:g}" for value in costs], padding=4)
    axes[1].set(title="Cost under identical constraints", ylabel="Synthetic cost units", ylim=(0, max(1, max(costs) * 1.25)))
    fig.suptitle("Synthetic practice: two-warehouse transportation", fontsize=13)
    (root / "figures").mkdir(exist_ok=True)
    fig.savefig(root / "figures/transport.png", dpi=180, metadata={"Description": "Synthetic practice, not competition results"})
    fig.savefig(root / "figures/transport.svg", metadata={"Date": None, "Description": "Synthetic practice, not competition results"})
    plt.close(fig)


def report(root):
    _, supply, demand, _ = instance(root)
    row = load_csv(root, "results/summary.csv")[0]
    checks = load_csv(root, "evidence/validation.csv")
    if any(check["status"] != "PASS" for check in checks):
        raise ValueError("Result prose requires passing validation")
    objective, baseline = float(row["objective"]), float(row["baseline_objective"])
    text = ("# 合成运输问题演练\n\n"
            "证据类别：SYNTHETIC_PRACTICE。此例用于验证科研辅助流程，不是 2025 年论文复现，也不是正式赛题答案。\n\n"
            "## 模型与预先判断\n\n"
            "以两座仓库到三个需求点的运输量为变量，最小化运输量与单位费用的乘积之和。"
            "各需求点的收货量等于需求，仓库发货量不超过库存，运输量非负。"
            "运输量单位为箱，单位费用采用合成费用单位／箱，总费用采用合成费用单位。"
            "线性费用、可分割货物和无额外线路容量是本例的模型假设。"
            f"总供给为 {supply.sum():g} 箱，总需求为 {demand.sum():g} 箱；求解前已将数量级界限和 12 项验收条件写入体检记录及参数文件。\n\n"
            "## 计算结果\n\n"
            f"SciPy HiGHS 求得模型最小费用为 {objective:g}，按仓库和需求点顺序分配的可行基线费用为 {baseline:g}。"
            f"在相同供需约束下，费用减少 {baseline-objective:g}，相对降幅为 {float(row['relative_saving'])*100:.2f}%。"
            "逐线路运输量见 `results/allocation.csv`，对应分配与费用比较见下图。\n\n"
            "![合成运输分配与基线费用比较](figures/transport.png)\n\n"
            "## 校验与适用范围\n\n"
            f"{len(checks)} 项自动代码校验通过，包括供需约束、费用重算、小规模穷举对拍、零需求、供给不足、重编号和费用缩放。"
            "整数供需的运输约束矩阵具有全幺模性质，因此此例的整数穷举最优值可与连续线性规划最优值比较。"
            "固定输入上的正确性校验不构成实际运输效果或跨场景稳健性证据。算法未使用随机采样，重复种子检验不适用；现实场景有效性尚未评估。"
            "人工审查尚未进行，自动校验不计作人工核验。\n")
    (root / "result.md").write_text(text, encoding="utf-8")
    write_csv(root / "evidence/claims.csv", [
        {"claim_id": "DEMO-C1", "claim": f"Declared LP optimal cost is {objective:g}", "evidence_class": "SYNTHETIC_PRACTICE",
         "code": str(Path(__file__).resolve()), "code_symbols": "solve,validate", "code_sha256": digest(__file__), "result": "results/summary.csv:row1",
         "validation": "evidence/validation.csv:independent_integer_enumeration", "figure": "figures/transport.svg",
         "manuscript": "result.md:计算结果", "human_review": "NOT_PERFORMED"},
        {"claim_id": "DEMO-C2", "claim": f"Savings versus declared greedy baseline: {baseline-objective:g}", "evidence_class": "SYNTHETIC_PRACTICE",
         "code": str(Path(__file__).resolve()), "code_symbols": "greedy,solve", "code_sha256": digest(__file__), "result": "results/summary.csv:row1",
         "validation": "evidence/validation.csv:baseline_feasible,baseline_dominance", "figure": "figures/transport.svg",
         "manuscript": "result.md:计算结果", "human_review": "NOT_PERFORMED"}])
    json_write(root / "evidence/ai-use.json", {"evidence_class": "SYNTHETIC_PRACTICE", "tool": "Codex",
        "purpose": "Generate deterministic helper code, run synthetic validation and draft the accompanying explanation",
        "model": "NOT_RECORDED_BY_SCRIPT", "reasoning_effort": "NOT_RECORDED_BY_SCRIPT",
        "prompt_summary": "Build a synthetic transport example with independent correctness checks and resumable workflow",
        "adoption": "Generated solver, validation, plot and report scripts used in this synthetic workflow",
        "modification_record": "Script SHA-256 and stage commands are captured by .workflow run metadata",
        "checks": "Automated code checks in evidence/validation.csv", "human_validation": "NOT_PERFORMED"})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["init", "audit", "solve", "validate", "plot", "report"])
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--missing-input", action="store_true", help="Initialize only manifest and acceptance for missing-data validation")
    args = parser.parse_args()
    root = args.workspace.resolve()
    if "practice" not in root.parts:
        raise ValueError("Every synthetic demo stage must run below a practice directory")
    if args.command == "init":
        init(root, args.missing_input)
    else:
        globals()[args.command](root)
        print(json.dumps({"stage": args.command, "status": "COMPLETE", "evidence_class": "SYNTHETIC_PRACTICE"}))


if __name__ == "__main__":
    main()
