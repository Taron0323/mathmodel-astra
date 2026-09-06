"""Diagnose local parameter sensitivity from a supplied, explicitly scaled Jacobian."""

import argparse
import hashlib
import json
from pathlib import Path
import sys

import numpy as np

from run_workflow import digest, json_write, stamp


def numeric_array(value, shape, name, positive=False):
    flattened = value if len(shape) == 1 else [item for row in value for item in row]
    if any(isinstance(item, bool) or not isinstance(item, (int, float)) for item in flattened):
        raise ValueError(name + " must contain JSON numbers")
    result = np.asarray(value, dtype=float)
    if result.shape != shape or not np.isfinite(result).all() or (positive and not (result > 0).all()):
        raise ValueError(name + " has invalid shape, nonfinite values or nonpositive scales")
    return result


def diagnose(specification):
    required = {"parameter_names", "jacobian", "parameter_scales", "residual_scales", "residual_scale_kind"}
    if not isinstance(specification, dict) or not required <= specification.keys():
        raise ValueError("Required fields: " + ", ".join(sorted(required)))
    if specification.keys() - required - {"relative_rank_tolerance"}:
        raise ValueError("Unknown fields in sensitivity specification")
    names = specification["parameter_names"]
    if (not isinstance(names, list) or not names or any(not isinstance(name, str) or not name.strip() for name in names)
            or len(names) != len(set(names))):
        raise ValueError("Parameter names must be nonempty and unique")
    matrix = specification["jacobian"]
    if not isinstance(matrix, list) or not matrix or any(not isinstance(row, list) or len(row) != len(names) for row in matrix):
        raise ValueError("Jacobian rows must have one entry per named parameter")
    rows, columns = len(matrix), len(names)
    jacobian = numeric_array(matrix, (rows, columns), "jacobian")
    for name in ("parameter_scales", "residual_scales"):
        if not isinstance(specification[name], list):
            raise ValueError(name + " must be a list")
    parameter_scales = numeric_array(specification["parameter_scales"], (columns,), "parameter_scales", positive=True)
    residual_scales = numeric_array(specification["residual_scales"], (rows,), "residual_scales", positive=True)
    scale_kind = specification["residual_scale_kind"]
    if scale_kind not in ("reference", "independent_noise_sd"):
        raise ValueError("Residual scales must be reference scales or independent noise standard deviations")
    precision_floor = max(rows, columns) * np.finfo(float).eps
    tolerance = specification.get("relative_rank_tolerance", precision_floor)
    if (isinstance(tolerance, bool) or not isinstance(tolerance, (int, float)) or not np.isfinite(tolerance)
            or not precision_floor <= tolerance < 1):
        raise ValueError("Relative rank tolerance must be at least the float64 precision floor and below 1")
    with np.errstate(over="raise", under="raise", invalid="raise", divide="raise"):
        scaled = jacobian / residual_scales[:, None] * parameter_scales[None, :]
    # Only request the full right basis when there are fewer observations than parameters.
    _, singular_values, right = np.linalg.svd(scaled, full_matrices=rows < columns)
    if not np.isfinite(singular_values).all():
        raise ValueError("Singular values exceed numerical range; rescale the problem")
    threshold = float(tolerance * singular_values[0])
    rank = int(np.count_nonzero(singular_values > threshold))
    full_rank = rank == columns
    condition = float(singular_values[0] / singular_values[-1]) if full_rank else None
    largest = np.max(np.abs(scaled), axis=0)
    unit_columns = np.divide(scaled, largest, out=np.zeros_like(scaled), where=largest > 0)
    lengths = np.linalg.norm(unit_columns, axis=0)
    unit_columns = np.divide(unit_columns, lengths, out=np.zeros_like(unit_columns), where=lengths > 0)
    cosines = unit_columns.T @ unit_columns
    pairs = [{"parameters": [names[first], names[second]],
              "cosine": float(np.clip(cosines[first, second], -1, 1)) if largest[first] and largest[second] else None}
             for first in range(columns) for second in range(first + 1, columns)]
    def direction_record(direction):
        direction = direction.copy()
        if direction[np.argmax(np.abs(direction))] < 0:
            direction *= -1
        return {"scaled_coordinates": dict(zip(names, direction.tolist())),
                "parameter_change": dict(zip(names, (parameter_scales * direction).tolist()))}

    return {"status": "DIAGNOSED", "rank_status": "FULL_COLUMN_RANK" if full_rank else "RANK_DEFICIENT",
            "observations": rows, "parameters": columns, "parameter_names": names, "numerical_rank": rank,
            "unresolved_linearized_directions": columns - rank, "scaled_singular_values": singular_values.tolist(),
            "relative_rank_tolerance": float(tolerance), "absolute_rank_threshold": threshold,
            "scaled_condition_number": condition, "zero_sensitivity_parameters": [name for name, size in zip(names, largest) if size == 0],
            "sensitivity_column_cosines": pairs,
            "max_absolute_sensitivity_cosine": max((abs(pair["cosine"]) for pair in pairs if pair["cosine"] is not None), default=None),
            "unresolved_directions": [direction_record(direction) for direction in right[rank:]],
            "weakest_direction": direction_record(right[-1]), "parameter_scales": parameter_scales.tolist(),
            "residual_scales": residual_scales.tolist(), "residual_scale_kind": scale_kind,
            "scope": "Local linearization of the supplied Jacobian in declared parameter and residual scales",
            "global_identifiability": "NOT_ASSESSED", "statistical_intervals": "NOT_COMPUTED"}


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key: " + key)
        result[key] = value
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    source, output = args.input.resolve(), args.output.resolve()
    if output == source or (output.exists() and source.exists() and output.samefile(source)):
        parser.error("Output cannot overwrite the Jacobian input")
    report = {"checked_at": stamp(), "actor": "AUTOMATED_CODE", "human_validation": "NOT_PERFORMED",
              "checker_sha256": digest(__file__), "numpy_version": np.__version__}
    try:
        raw = source.read_bytes()
        report["input_sha256"] = hashlib.sha256(raw).hexdigest()
        report.update(diagnose(json.loads(raw, object_pairs_hook=unique_object)))
        code = 0
    except (ValueError, TypeError, KeyError, OSError, OverflowError, FloatingPointError, np.linalg.LinAlgError) as exc:
        report.update(status="DIAGNOSTIC_ERROR", error=type(exc).__name__ + ": " + str(exc))
        code = 2
    try:
        json_write(output, report)
    except (OSError, ValueError) as exc:
        print("Could not write sensitivity diagnosis: " + str(exc), file=sys.stderr)
        return 2
    print(json.dumps({"status": report["status"], "rank_status": report.get("rank_status"), "output": str(output)}))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
