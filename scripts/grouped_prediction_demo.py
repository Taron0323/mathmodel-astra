"""Synthetic counterexample: row-wise validation of unseen-entity prediction."""

import argparse
import csv
import importlib.metadata
import json
from pathlib import Path

import numpy as np
from sklearn.dummy import DummyClassifier
from sklearn.metrics import accuracy_score
from sklearn.model_selection import GroupKFold, KFold
from sklearn.neighbors import KNeighborsClassifier
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from run_workflow import digest, json_write, stamp


FEATURES = ["feature_" + str(index) for index in range(6)]
EVIDENCE_FILES = ("input/observations.csv", "input/protocol.json", "results/folds.json", "results/predictions.csv", "results/metrics.csv")
PROTOCOLS = ("row_split", "group_split")
CHECK_IDS = {
    "raw_inputs_unchanged", "complete_fold_schema", "split_partitions", "shared_entities_recalculated",
    "group_split_entity_disjoint", "row_split_exposes_overlap", "scaler_fitted_on_training_rows",
    "metric_sample_counts", "counterexample_reproduced", "invalid_protocol_not_endorsed",
    *(name + suffix for name in PROTOCOLS for suffix in
      ("_predicts_each_row_once", "_prediction_identity", "_metric_recalculation")),
}


def write_csv(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def read_csv(path):
    with path.open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def current_evidence(root):
    state = read_json(root / "verification.json")
    checks = state.get("checks", [])
    if (state.get("status") != "PASS" or state.get("code_sha256") != digest(__file__)
            or not isinstance(checks, list) or len(checks) != len(CHECK_IDS)
            or any(not isinstance(row, dict) or row.get("status") != "PASS" for row in checks)
            or {row.get("check_id") for row in checks} != CHECK_IDS
            or state.get("files") != {name: digest(root / name) for name in EVIDENCE_FILES}):
        raise ValueError("Rendering requires complete current prediction evidence")
    return state


def evaluate(root):
    if root.exists() and any(root.iterdir()):
        raise FileExistsError("Use a new practice workspace; existing evidence is preserved")
    root.mkdir(parents=True, exist_ok=True)
    protocol = {"evidence_class": "SYNTHETIC_PRACTICE", "target": "Binary label of an unseen entity",
                "entity_count": 256, "observations_per_entity": 4, "data_seed": 20260906, "split_seed": 29,
                "folds": 5, "features": FEATURES, "model": "StandardScaler + KNeighborsClassifier(n_neighbors=1)",
                "baseline": "DummyClassifier(strategy=most_frequent)", "tuning": "NONE",
                "label_generation": "Independent random binary label per entity, unrelated to feature prototypes",
                "repeated_observations": "Four identical feature observations per entity, with the same label",
                "acceptance": {"minimum_row_accuracy": 0.9, "maximum_group_accuracy": 0.75, "minimum_accuracy_gap": 0.2},
                "human_validation": "NOT_PERFORMED"}
    json_write(root / "input/protocol.json", protocol)
    generator = np.random.default_rng(protocol["data_seed"])
    groups = np.repeat(np.arange(protocol["entity_count"]), protocol["observations_per_entity"])
    prototypes = generator.normal(size=(protocol["entity_count"], len(FEATURES)))
    labels = generator.integers(0, 2, protocol["entity_count"])
    write_csv(root / "input/observations.csv", [
        {"row_id": row_id, "entity_id": int(group), "label": int(labels[group]),
         **{feature: float(value) for feature, value in zip(FEATURES, prototypes[group])}}
        for row_id, group in enumerate(groups)])
    original_inputs = {name: digest(root / name) for name in EVIDENCE_FILES[:2]}
    data = read_csv(root / "input/observations.csv")
    features = np.array([[float(row[name]) for name in FEATURES] for row in data])
    target = np.array([int(row["label"]) for row in data])
    groups = np.array([int(row["entity_id"]) for row in data])
    splits = {
        "row_split": KFold(protocol["folds"], shuffle=True, random_state=protocol["split_seed"]).split(features, target),
        "group_split": GroupKFold(protocol["folds"]).split(features, target, groups),
    }
    folds, predictions, metrics = [], [], []
    for name, iterator in splits.items():
        actual, predicted, baseline_predicted = [], [], []
        for fold_id, (train, test) in enumerate(iterator):
            model = make_pipeline(StandardScaler(), KNeighborsClassifier(n_neighbors=1))
            baseline = DummyClassifier(strategy="most_frequent")
            model.fit(features[train], target[train])
            baseline.fit(features[train], target[train])
            output, reference = model.predict(features[test]), baseline.predict(features[test])
            scaler = model.named_steps["standardscaler"]
            folds.append({"protocol": name, "fold": fold_id, "train_rows": train.tolist(), "test_rows": test.tolist(),
                          "shared_entities": sorted(set(groups[train].tolist()) & set(groups[test].tolist())),
                          "scaler_sample_count": int(scaler.n_samples_seen_), "scaler_mean": scaler.mean_.tolist()})
            predictions.extend({"protocol": name, "fold": fold_id, "row_id": int(row_id), "entity_id": int(groups[row_id]),
                                "actual": int(target[row_id]), "predicted": int(prediction), "baseline_predicted": int(base)}
                               for row_id, prediction, base in zip(test, output, reference))
            actual.extend(target[test].tolist())
            predicted.extend(output.tolist())
            baseline_predicted.extend(reference.tolist())
        metrics.append({"protocol": name, "test_observations": len(actual), "independent_entities": len(set(groups.tolist())),
                        "accuracy": float(accuracy_score(actual, predicted)),
                        "baseline_accuracy": float(accuracy_score(actual, baseline_predicted)),
                        "valid_for_unseen_entities": name == "group_split"})
    json_write(root / "results/folds.json", folds)
    write_csv(root / "results/predictions.csv", predictions)
    write_csv(root / "results/metrics.csv", metrics)
    verify(root, original_inputs)


def verify(root, original_inputs):
    json_write(root / "verification.json", {"status": "FAIL", "reason": "Verification has not completed"})
    data = read_csv(root / "input/observations.csv")
    protocol = read_json(root / "input/protocol.json")
    folds = read_json(root / "results/folds.json")
    predictions = read_csv(root / "results/predictions.csv")
    metric_rows = read_csv(root / "results/metrics.csv")
    metrics = {row["protocol"]: row for row in metric_rows}
    all_rows = set(range(len(data)))
    checks = []

    def check(key, passed):
        checks.append({"check_id": key, "status": "PASS" if passed else "FAIL"})

    check("raw_inputs_unchanged", original_inputs == {name: digest(root / name) for name in original_inputs})
    check("complete_fold_schema", len(folds) == len(PROTOCOLS) * protocol["folds"]
          and {(fold["protocol"], fold["fold"]) for fold in folds}
          == {(name, index) for name in PROTOCOLS for index in range(protocol["folds"])}
          and len(metric_rows) == len(PROTOCOLS) and set(metrics) == set(PROTOCOLS)
          and {row["protocol"] for row in predictions} == set(PROTOCOLS))
    if checks[-1]["status"] != "PASS":
        raise ValueError("Expected every registered protocol and fold exactly once")
    check("split_partitions", all(set(fold["train_rows"]).isdisjoint(fold["test_rows"])
          and set(fold["train_rows"]) | set(fold["test_rows"]) == all_rows
          and all(len(fold[key]) == len(set(fold[key])) > 0 for key in ("train_rows", "test_rows")) for fold in folds))
    groups = np.array([int(row["entity_id"]) for row in data])
    features = np.array([[float(row[name]) for name in FEATURES] for row in data])
    check("shared_entities_recalculated", all(fold["shared_entities"] == sorted(
        set(groups[fold["train_rows"]].tolist()) & set(groups[fold["test_rows"]].tolist())) for fold in folds))
    check("group_split_entity_disjoint", all(not fold["shared_entities"] for fold in folds if fold["protocol"] == "group_split"))
    check("row_split_exposes_overlap", all(fold["shared_entities"] for fold in folds if fold["protocol"] == "row_split"))
    check("scaler_fitted_on_training_rows", all(fold["scaler_sample_count"] == len(fold["train_rows"])
          and np.allclose(fold["scaler_mean"], features[fold["train_rows"]].mean(axis=0), rtol=0, atol=1e-12) for fold in folds))
    check("metric_sample_counts", all(int(row["test_observations"]) == len(data)
          and int(row["independent_entities"]) == len(set(groups.tolist())) for row in metric_rows))
    for name in PROTOCOLS:
        observed = [row for row in predictions if row["protocol"] == name]
        check(name + "_predicts_each_row_once", len(observed) == len(data) and {int(row["row_id"]) for row in observed} == all_rows)
        test_rows = {fold["fold"]: set(fold["test_rows"]) for fold in folds if fold["protocol"] == name}
        check(name + "_prediction_identity", all(int(row["row_id"]) in test_rows[int(row["fold"])]
              and row["actual"] == data[int(row["row_id"])]["label"]
              and row["entity_id"] == data[int(row["row_id"])]["entity_id"] for row in observed))
        check(name + "_metric_recalculation", all(abs(sum(row[column] == row["actual"] for row in observed) / len(observed)
              - float(metrics[name][metric])) < 1e-12 for column, metric in
              (("predicted", "accuracy"), ("baseline_predicted", "baseline_accuracy"))))
    row_accuracy, group_accuracy = (float(metrics[name]["accuracy"]) for name in PROTOCOLS)
    acceptance = protocol["acceptance"]
    check("counterexample_reproduced", row_accuracy > acceptance["minimum_row_accuracy"]
          and group_accuracy < acceptance["maximum_group_accuracy"]
          and row_accuracy - group_accuracy > acceptance["minimum_accuracy_gap"])
    check("invalid_protocol_not_endorsed", metrics["row_split"]["valid_for_unseen_entities"] == "False"
          and metrics["group_split"]["valid_for_unseen_entities"] == "True")
    state = {"status": "PASS" if all(row["status"] == "PASS" for row in checks) else "FAIL", "checked_at": stamp(),
             "evidence_class": "SYNTHETIC_PRACTICE", "scope": "Synthetic split counterexample and stored prediction evidence",
             "actor": "AUTOMATED_CODE", "human_validation": "NOT_PERFORMED", "checks": checks,
             "code_sha256": digest(__file__), "files": {name: digest(root / name) for name in EVIDENCE_FILES},
             "versions": {name: importlib.metadata.version(name) for name in ("numpy", "scikit-learn", "matplotlib")}}
    json_write(root / "verification.json", state)
    if state["status"] != "PASS":
        raise RuntimeError("Prediction demonstration validation failed")


def render(root):
    current_evidence(root)
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.ticker import PercentFormatter

    metrics = read_csv(root / "results/metrics.csv")
    folds = read_json(root / "results/folds.json")
    labels = ["Row split\ninvalid for unseen entities", "Entity split\nunseen entities"]
    fig, axes = plt.subplots(1, 2, figsize=(11.4, 4.6), layout="constrained")
    positions = np.arange(2)
    for offset, key, label, color in ((-.18, "accuracy", "1-nearest neighbor", "#287A78"),
                                      (.18, "baseline_accuracy", "Majority baseline", "#8C8F91")):
        values = [float(row[key]) for row in metrics]
        bars = axes[0].bar(positions + offset, values, .34, label=label, color=color)
        axes[0].bar_label(bars, labels=[f"{value:.1%}" for value in values], padding=3)
    axes[0].set(xticks=positions, xticklabels=labels, ylim=(0, 1.08), yticks=np.linspace(0, 1, 6),
                ylabel="Held-out row accuracy")
    axes[0].yaxis.set_major_formatter(PercentFormatter(1))
    axes[0].legend(frameon=False, loc="upper center", bbox_to_anchor=(.5, 1.2), ncol=2, fontsize=9)
    for position, name, color in ((0, "row_split", "#B95B43"), (1, "group_split", "#287A78")):
        overlap = [len(fold["shared_entities"]) for fold in folds if fold["protocol"] == name]
        axes[1].scatter(position + np.linspace(-.16, .16, len(overlap)), overlap, color=color, s=45)
    axes[1].set_title("Each point represents one fold", fontsize=10, pad=10)
    axes[1].set(xticks=positions, xticklabels=labels, xlim=(-.5, 1.5), ylim=(-8, 190), ylabel="Shared train/test entities per fold")
    for axis in axes:
        axis.spines[["top", "right"]].set_visible(False)
    fig.suptitle("Synthetic repeated observations: predicting unseen entities", fontsize=13)
    (root / "figures").mkdir(exist_ok=True)
    fig.savefig(root / "figures/split-comparison.png", dpi=160)
    fig.savefig(root / "figures/split-comparison.svg", metadata={"Date": None})
    plt.close(fig)
    values = {row["protocol"]: row for row in metrics}
    protocol = read_json(root / "input/protocol.json")
    text = ("# 重复观测划分演练\n\n"
            f"合成数据包含 {protocol['entity_count']} 个个体，每个个体有 {protocol['observations_per_entity']} 条相同特征的记录。"
            "个体标签独立随机生成，任务是预测训练阶段未见过的个体。两种划分均在每折训练部分拟合标准化器与单近邻模型，没有调参。\n\n"
            "## 结果与划分\n\n"
            f"按记录随机划分的准确率为 {float(values['row_split']['accuracy']):.2%}，"
            f"按个体隔离划分为 {float(values['group_split']['accuracy']):.2%}。"
            "前者的训练和测试记录来自部分相同个体，不符合新个体预测的评价目标。"
            "后者每折的个体交集为空，预测由该折重新训练的模型产生。\n\n"
            "![相同模型在两种划分下的准确率与个体重叠](figures/split-comparison.png)\n\n"
            "本例的重复特征可识别已经见过的个体；由于标签与特征独立，新个体预测缺少可泛化的信号。"
            "记录级高分反映了这种记忆条件。实际任务应先确定预测对象与信息时点，再选择划分方式；"
            "这两组数字用于演示划分的影响，不用于评价实际赛题模型。\n\n"
            "每条预测、各折训练和测试索引、基线结果及训练内标准化记录保存在 `results/`。人工核验尚未进行。\n")
    (root / "report.md").write_text(text, encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("run", "render"))
    parser.add_argument("--workspace", required=True, type=Path)
    args = parser.parse_args()
    root = args.workspace.resolve()
    if "practice" not in root.parts:
        parser.error("Synthetic data and results must remain below a practice directory")
    if args.command == "run":
        evaluate(root)
    render(root)
    state = current_evidence(root)
    print(json.dumps({"status": state["status"], "checks": len(state["checks"]), "workspace": str(root)}))


if __name__ == "__main__":
    main()
