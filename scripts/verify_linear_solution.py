"""Check a submitted LP/MILP candidate against its recorded linear model."""

import argparse
from datetime import datetime, timezone
from decimal import Decimal, DecimalException, Inexact, localcontext
import hashlib
import json
from pathlib import Path
import sys
import uuid


def fields(value, required, optional=()):
    if not isinstance(value, dict) or not set(required) <= value.keys() or value.keys() - set(required) - set(optional):
        raise ValueError("Expected fields: " + ", ".join(required) + "; optional: " + ", ".join(optional))


def number(value):
    if isinstance(value, bool) or not isinstance(value, (int, Decimal)):
        raise ValueError("Expected a JSON number")
    result = Decimal(value)
    if not result.is_finite():
        raise ValueError("All numbers must be finite")
    return result


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key: " + key)
        result[key] = value
    return result


def read(path):
    raw = path.read_bytes()
    value = json.loads(raw.decode("utf-8"), parse_float=Decimal, parse_constant=Decimal,
                       object_pairs_hook=unique_object)
    return value, hashlib.sha256(raw).hexdigest()


def audit(model, solution):
    fields(model, ("version", "variables", "constraints", "objective", "tolerances"))
    if type(model["version"]) is not int or model["version"] != 1:
        raise ValueError("Unsupported model version")
    fields(solution, ("values", "objective"))
    variables = model["variables"]
    if not isinstance(variables, dict) or not variables or any(not key for key in variables):
        raise ValueError("Declare at least one named variable")
    if not isinstance(solution["values"], dict) or solution["values"].keys() != variables.keys():
        raise ValueError("Solution must contain exactly the declared variables")
    values = {key: number(value) for key, value in solution["values"].items()}
    tol = model["tolerances"]
    fields(tol, ("absolute", "relative", "integrality"))
    absolute, relative, integral = (number(tol[key]) for key in ("absolute", "relative", "integrality"))
    if absolute < 0 or not 0 <= relative < 1 or not 0 <= integral < Decimal("0.5"):
        raise ValueError("Require absolute >= 0, 0 <= relative < 1 and 0 <= integrality < 0.5")
    checks = []

    def check(kind, key, lhs, sense, rhs, tolerance=None):
        allowed = absolute + relative * max(abs(lhs), abs(rhs)) if tolerance is None else tolerance
        violation = {"<=": lambda: max(Decimal(0), lhs - rhs),
                     ">=": lambda: max(Decimal(0), rhs - lhs),
                     "==": lambda: abs(lhs - rhs)}[sense]()
        checks.append({"kind": kind, "id": key, "lhs": lhs, "sense": sense, "rhs": rhs,
                       "violation": violation, "tolerance": allowed,
                       "status": "PASS" if violation <= allowed else "FAIL"})

    for key, variable in variables.items():
        fields(variable, ("domain", "lower", "upper"))
        if variable["domain"] not in ("continuous", "integer", "binary"):
            raise ValueError("Unknown variable domain: " + key)
        lower = number(variable["lower"]) if variable["lower"] is not None else None
        upper = number(variable["upper"]) if variable["upper"] is not None else None
        if lower is not None and upper is not None and lower > upper:
            raise ValueError("Inverted bounds: " + key)
        if lower is not None:
            check("bound", key + ".lower", values[key], ">=", lower)
        if upper is not None:
            check("bound", key + ".upper", values[key], "<=", upper)
        if variable["domain"] == "integer":
            check("integrality", key, values[key], "==", values[key].to_integral_value(), integral)
        elif variable["domain"] == "binary":
            nearest = min((Decimal(0), Decimal(1)), key=lambda value: abs(values[key] - value))
            check("integrality", key, values[key], "==", nearest, integral)

    def linear(coefficients):
        if not isinstance(coefficients, dict) or coefficients.keys() - variables.keys():
            raise ValueError("Coefficients must refer only to declared variables")
        return sum((number(coefficient) * values[key] for key, coefficient in coefficients.items()), Decimal(0))

    if not isinstance(model["constraints"], list):
        raise ValueError("Constraints must be a list")
    constraint_ids = set()
    for constraint in model["constraints"]:
        fields(constraint, ("id", "coefficients", "sense", "rhs"))
        key = constraint["id"]
        if not isinstance(key, str) or not key or key in constraint_ids:
            raise ValueError("Constraint IDs must be nonempty and unique")
        constraint_ids.add(key)
        if constraint["sense"] not in ("<=", ">=", "=="):
            raise ValueError("Unsupported constraint sense: " + key)
        check("constraint", key, linear(constraint["coefficients"]), constraint["sense"], number(constraint["rhs"]))
    objective = model["objective"]
    fields(objective, ("sense", "coefficients", "constant"))
    if objective["sense"] not in ("min", "max"):
        raise ValueError("Objective sense must be min or max")
    computed = linear(objective["coefficients"]) + number(objective["constant"])
    check("objective", "reported_objective", computed, "==", number(solution["objective"]))
    feasible = all(row["status"] == "PASS" for row in checks if row["kind"] != "objective")
    consistent = checks[-1]["status"] == "PASS"
    return {"status": "PASS" if feasible and consistent else "FAIL", "feasible": feasible,
            "objective_consistent": consistent, "objective_sense": objective["sense"],
            "recomputed_objective": computed, "checks": checks}


def write_report(path, report):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp-" + uuid.uuid4().hex)
    try:
        temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str, allow_nan=False) + "\n",
                             encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--solution", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    model_path, solution_path, output = (path.resolve() for path in (args.model, args.solution, args.output))
    if any(output == source or (output.exists() and source.exists() and output.samefile(source))
           for source in (model_path, solution_path)):
        parser.error("Output cannot overwrite the model or solution")
    report = {"checked_at": datetime.now(timezone.utc).isoformat(), "actor": "AUTOMATED_CODE",
              "human_validation": "NOT_PERFORMED", "optimality": "NOT_ASSESSED",
              "scope": "Recorded linear model, submitted variable values and objective only",
              "arithmetic": "Decimal, 80 significant digits; inexact arithmetic is rejected",
              "checker_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    try:
        model, report["model_sha256"] = read(model_path)
        solution, report["solution_sha256"] = read(solution_path)
        with localcontext() as context:
            context.prec = 80
            context.traps[Inexact] = True
            report.update(audit(model, solution))
        code = 0 if report["status"] == "PASS" else 1
    except (ValueError, TypeError, KeyError, OSError, DecimalException) as exc:
        report.update(status="CONFIGURATION_ERROR", error=type(exc).__name__ + ": " + str(exc))
        code = 2
    try:
        write_report(output, report)
    except OSError as exc:
        print("Could not write verification report: " + str(exc), file=sys.stderr)
        return 2
    print(json.dumps({"status": report["status"], "output": str(output), "optimality": report["optimality"]}))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
