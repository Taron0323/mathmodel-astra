"""Detect source drift in the reviewed human-readable guide mapping."""

import argparse
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GUIDE = "docs/mathmodel-astra-guide.md"
RECORD = "docs/guide-sources.json"
SECTIONS = {
    "1. 定位与适用范围": ["SKILL.md"],
    "2. 进入项目与任务范围": ["SKILL.md", "references/workflow.md"],
    "3. 全流程与阶段验收": ["references/workflow.md"],
    "4. 按数学结构选择方法": ["references/problem-types.md", "references/capabilities.md"],
    "5. 数据、结论与证据合同": ["references/evidence-contract.md"],
    "6. 数学和计算验证": ["references/verification.md", "references/linear-solutions.md",
                     "references/prediction-validation.md", "references/parameter-identifiability.md", "references/ode-validation.md"],
    "7. 图表、正文与论文学习": ["references/figures.md", "references/writing.md", "references/literature.md",
                      "references/exemplars/synthesis.md"],
    "8. 模型、并行与恢复": ["references/astra-modes.md", "references/model-policy.json", "references/workflow.md"],
    "9. 脚本与可运行示例": ["references/runtime.md", "README.md"],
    "10. 比赛规则与交付": ["references/competition.md", "references/sources.json"],
    "11. 可直接调用的提示词": ["SKILL.md"],
    "12. 来源索引": ["scripts/check_docs.py"],
}


def snapshot():
    body = (ROOT / GUIDE).read_text(encoding="utf-8")
    if {line[3:] for line in body.splitlines() if line.startswith("## ")} != set(SECTIONS):
        raise ValueError("Every guide section must have an explicit source mapping")
    files = {GUIDE, *(name for sources in SECTIONS.values() for name in sources)}
    return {"version": 1, "guide": GUIDE, "sections": SECTIONS,
            "review_actor": "AI_ASSISTANT", "scope": "Source freshness, not semantic or human approval",
            "sha256": {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in sorted(files)}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("check", "record"))
    args = parser.parse_args()
    expected = snapshot()
    target = ROOT / RECORD
    if args.command == "record":
        temporary = target.with_suffix(".tmp")
        temporary.write_text(json.dumps(expected, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(target)
        print("Recorded reviewed guide/source mapping")
        return 0
    actual = json.loads(target.read_text(encoding="utf-8")) if target.exists() else {}
    if actual != expected:
        changed = [name for name, value in expected["sha256"].items() if actual.get("sha256", {}).get(name) != value]
        print(json.dumps({"status": "STALE", "changed": changed,
                          "action": "Review affected guide sections, then run check_docs.py record"}))
        return 1
    print(json.dumps({"status": "CURRENT", "sections": len(SECTIONS), "files": len(expected["sha256"])}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
