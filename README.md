# mathmodel-astra

适配 GPT-6 Astra Max／Ultra 的数学建模科研辅助 Codex Skill。支持全流程推进和单阶段任务，把题面、数学模型、求解、验证、图表、论文与结论证据连接起来。

## 安装

公开仓库可直接通过 Git 安装，无需登录 GitHub：

```sh
git clone https://github.com/Taron0323/mathmodel-astra.git "$HOME/.agents/skills/mathmodel-astra"
```

已配置 GitHub CLI 时也可使用：

```sh
gh repo clone Taron0323/mathmodel-astra ~/.agents/skills/mathmodel-astra
```

目标目录已有同名 Skill 时，先比较并备份已有版本；克隆命令不会覆盖非空目录。重新打开 Codex 会话，在技能列表中确认 `mathmodel-astra` 可用。使用此 Skill 本身无需运行运输演练或安装科学计算库。

升级前记录当前提交并确认工作区没有本地修改。以下命令用于跟随 `main` 的干净 Git 安装；手动复制的目录先比较文件，不直接套用 Git 升级：

```sh
git -C "$HOME/.agents/skills/mathmodel-astra" status --short
git -C "$HOME/.agents/skills/mathmodel-astra" rev-parse HEAD
git -C "$HOME/.agents/skills/mathmodel-astra" fetch origin
git -C "$HOME/.agents/skills/mathmodel-astra" merge --ff-only origin/main
```

需要固定版本或回滚时，在干净安装目录执行 `git switch --detach <已核实的提交>`。保留升级前记录的提交；有本地改动时先另行备份并核对差异。回滚 Skill 不会回滚项目数据，旧计算证据是否可复用仍须检查输入、代码和环境版本。

## 调用

在数学建模项目目录中选择客户端实际支持的模型与推理档位，再发送：

```text
$mathmodel-astra 读取当前项目，按已授权目标推进到下一项可验收产出；复用已有有效结果，需要密码或登录的网站直接跳过。
```

常用单阶段任务：

```text
$mathmodel-astra 审查这一问的目标函数、约束和假设，给出可解释基线、验证方法与退出条件。

$mathmodel-astra 依据已有结果修订摘要，删除空泛评价和冗余防御措辞，保留条件、误差及失败结果。

$mathmodel-astra 从 HANDOFF 和实际文件恢复，核实进程与证据，继续未完成阶段。
```

## 工作方式

- Max 建议用于资料整理、实现、常规验证、绘图和正文修订；Ultra 建议用于关键数学抽象、复杂推导、证据冲突和科学审查。档位需要在客户端设置，提示词不能自行切换。
- 先发现当前项目的 `AGENTS.md`、配置和事实记录，按阶段加载参考，不要求所有项目采用同一布局。
- 原始输入只读，保存 SHA-256；清洗、求解和绘图分离。结论关联代码、结果、图表及论文位置。
- 已验证且未变化的结果复用；输入、代码或环境变化后只重跑受影响阶段。支持检查点、失败记录和中断恢复。
- 单阶段请求只处理相应阶段及必要依赖。项目的 `mathmodel-grill`、`mathmodel-writing` 可按需协作，缺失时使用随包参考。
- 正式核心建模和分析由参赛队主导；按实际比赛与年份核验规则，分别记录 AI 核验、人工核验和提交状态。

## 内容

| 路径 | 用途 |
| --- | --- |
| [SKILL.md](SKILL.md) | 技能入口、触发范围和阶段路由 |
| [agents/openai.yaml](agents/openai.yaml) | Codex 显示信息和调用策略 |
| [references/workflow.md](references/workflow.md) | 全流程、阶段依赖和续接 |
| [references/astra-modes.md](references/astra-modes.md) | Max／Ultra 策略和配置证据 |
| [references/problem-types.md](references/problem-types.md) | 题型、数学结构和基线 |
| [references/verification.md](references/verification.md) | 正确性、敏感性及稳健性检查 |
| [references/writing.md](references/writing.md) | 自然、准确的中文表达 |
| [references/figures.md](references/figures.md) | 基于实际结果的图表设计 |
| [references/exemplars/synthesis.md](references/exemplars/synthesis.md) | 2025 年 CUMCM 论文学习与五张范例卡 |
| [references/runtime.md](references/runtime.md) | 脚本接口、依赖与运行范围 |
| [references/linear-solutions.md](references/linear-solutions.md) | 最终线性方案、取整与费用核对，附可运行接口和反例 |
| [references/prediction-validation.md](references/prediction-validation.md) | 预测对象、分组与时间划分、训练内预处理及重复观测反例 |
| [references/parameter-identifiability.md](references/parameter-identifiability.md) | 参数反演、尺度、局部秩与条件数、剖面分析和可运行诊断 |
| [references/capabilities.md](references/capabilities.md) | 各题型的方法、脚本、独立验证与历史题覆盖层级 |
| [references/ode-validation.md](references/ode-validation.md) | 有解析解的动态系统、守恒与步长收敛演练 |
| [references/sources.json](references/sources.json) | 竞赛条款及模型支持项的来源、日期、定位与核验范围 |
| [references/model-policy.json](references/model-policy.json) | 按任务角色记录模型偏好与可用性回退，不修改宿主设置 |
| [docs/mathmodel-astra-guide.md](docs/mathmodel-astra-guide.md) | 面向人类阅读的完整流程、题型路由、赛区边界与运行示例 |
| [docs/mathmodel-astra-evaluation.md](docs/mathmodel-astra-evaluation.md) | 已验证能力、当前限制与分优先级优化建议 |

其他按需参考从 `SKILL.md` 路由进入。

## 运行合成演练

通用运行器只依赖 Python 标准库。运输演练需要 Python 3.10+、NumPy、SciPy 和 Matplotlib；进程中断与超时使用 POSIX 信号，已有 Linux/macOS CI 验证，Windows 原生行为尚未验证。

在仓库根目录执行：

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-demo.txt
.venv/bin/python scripts/transport_demo.py init --workspace practice/transport-demo
.venv/bin/python scripts/run_workflow.py run --manifest practice/transport-demo/workflow.json
.venv/bin/python scripts/run_workflow.py status --manifest practice/transport-demo/workflow.json
```

`init` 要求新目录。生成内容包括输入与验收预设、结果 CSV、SVG/PNG、中文短文、claims 映射和运行元数据。短文展示逐线路方案、费用比较、枚举数量和约束偏差，数值读取自验证通过的结果文件。上述合成例的最小费用为 25，顺序分配基线为 43；基线费用为零时，相对降幅在 CSV 中留空，正文不生成百分比。

需要验证运行器行为时，在新目录执行：

```sh
.venv/bin/python scripts/verify_runtime.py --workspace practice/runtime-validation
```

命令与代码路径只展开 `{python}`、`{workspace}`、`{skill}` 三个保留标记，其他花括号保持原样。相对 `code` 路径从工作流 JSON 所在目录解析，支持从其他目录启动；详见[运行接口](references/runtime.md)。

运行不需要科学计算依赖的回归测试，覆盖工作流可移植性与最终线性方案核验：

```sh
python3 -m unittest discover -s tests -v
```

GitHub Actions 在 Linux、macOS 与 Python 3.10、3.13 的组合上执行回归测试、完整运输运行行为验收和预测划分演练。实际状态见仓库 Actions；本地通过不代表远端检查已完成。

完整运输演练检查缺数据、真实中断与恢复、旧结果复用、输入或代码变化、归档保留、错误结果拦截和图文发布证据。主示例含 12 项数学检查；当前行为检查数量以 `runtime-summary.json` 为准。污染样例中的 FAIL 是预期拒绝，不等于主流程失败。这些结果只验证对应合成例和运行流程，不证明任意赛题的科学正确性。

图文生成要求验证覆盖完整并与当前输入、结果和代码匹配，空表、部分 PASS 和旧版本记录都会被拒绝。升级旧演练清单时需登记新增的 `evidence/validation-state.json` 及相关依赖，具体见[运行接口](references/runtime.md)。

运输图表还生成 `evidence/figure-state.json`，把 PNG/SVG 与实际数值证据绑定。单独调用 `report` 也会拒绝缺图、错图或失败的重绘记录；数值重新验证后依次运行 `plot`、`report`。旧清单需将图表状态加入 `plot.outputs` 与 `report.inputs`，迁移方式同见运行接口。

## 核对最终优化方案

`scripts/verify_linear_solution.py` 从独立登记的线性模型和最终解 JSON 复算约束、变量上下界、整数/二元域与目标值，适用于 LP/MILP 求解后以及取整、裁剪等后处理之后。只依赖标准库；提供输入哈希和逐项残差，区分可行性、费用一致性与尚未核验的最优性。

完整模型格式、命令和“连续解取整后不可行”的合成例见[最终方案核验](references/linear-solutions.md)。模型语义仍需对题面核对，非线性与逻辑约束需使用相应检查。

## 检查预测划分

预测新个体、已知个体的未来观测和未来新个体需要不同的评估划分。新增的[预测验证参考](references/prediction-validation.md)将目标对象、信息时点、训练内预处理、指标权重与验收记录对应起来。

合成演练采用相同的单近邻模型比较记录随机五折与个体隔离五折，每折重新拟合模型和标准化器，同时报告多数类基线。输入包含 256 个个体的 1024 条重复记录，标签独立随机生成；图中的高准确率对应测试记录与训练记录共享个体的条件。

```sh
.venv/bin/python -m pip install -r requirements-prediction.txt
.venv/bin/python scripts/grouped_prediction_demo.py run --workspace practice/grouped-prediction
.venv/bin/python scripts/grouped_prediction_demo.py render --workspace practice/grouped-prediction
.venv/bin/python -m unittest discover -s tests/prediction -v
```

`run` 要求新目录，保存各折索引、每条预测、指标、验证记录及图文；`render` 复用未变化的有效证据，不重新训练。该例用于检查重复观测的评价边界，不代表真实赛题性能或官方论文复现。科学计算依赖仅在运行对应演练或测试时需要。

## 诊断反演参数

拟合残差小不保证参数唯一。`diagnose_identifiability.py` 对实际导出的 Jacobian 做尺度归一化和 SVD，输出局部数值秩、条件数与未确定的一阶参数方向。输入示例、解释范围及如何改报参数组合见[参数可辨识性](references/parameter-identifiability.md)。只需 NumPy；不自动求导、拟合模型或计算置信区间。

```sh
.venv/bin/python scripts/diagnose_identifiability.py --input jacobian.json --output results/sensitivity.json
.venv/bin/python -m unittest discover -s tests/numerical -v
```

诊断完成与科学结论分开记录：秩亏时命令仍成功完成，并给出具体未确定方向。满列秩时继续结合奇异值尺度、噪声、剖面与模型结构判断参数精度。

## 论文来源

范例来自[中国大学生在线 2025 年 CUMCM 展示目录](https://dxs.moe.gov.cn/zx/hd/sxjm/sxjmlw/2025qgdxssxjmjslwzs/)。A/B/C 五篇共 424 页完成全文文字阅读及 59 页关键原图核对；A 题目录只有一篇。逐篇卡片区分原文事实、解释、迁移建议与可见不足，给出官网链接和页码。

本仓库提供原创提炼与卡片，不包含官方论文原图、全文 OCR、派生 PDF、用户题面或本机运行日志。卡片中的 `papers/`、`manifest.json` 等反引号路径指学习项目的本地归档，不是本仓库的运行依赖。论文报告值未在本仓库复现，不将官方展示自动称为某一奖项等级。

模型、客户端和竞赛规则会变化；使用前按当前环境核验。Max／Ultra 的分工是工作策略，不是质量对比实验结论。

## 动态系统与行为评测

双室转移演练使用 RK23 求解，以解析解、DOP853、守恒和两个退化实例核验结果。绘图阶段只读有效证据：

```sh
.venv/bin/python scripts/ode_demo.py init --workspace practice/ode-demo
.venv/bin/python scripts/run_workflow.py run --manifest practice/ode-demo/workflow.json
.venv/bin/python scripts/run_workflow.py status --manifest practice/ode-demo/workflow.json
```

该例依赖 `requirements-demo.txt`。支持边界与十项验收见 [ODE 验证](references/ode-validation.md)；相关误差传播、统计区间和真实历史题复现仍分别记录，不能由本例通过推断。

实际代理的单阶段边界、目录适配、续接、输入保护与信息处理案例见 [行为评测](evals/behavior/README.md)。它使用隔离文件与真实运行记录，和 CI 中的确定性脚本测试分开。一次运行不构成跨模型效果或重复稳定性的证据。

## 维护文档

规范入口为 `SKILL.md` 与按需参考。完整人类手册通过 [来源映射](docs/guide-sources.json) 记录各节对应规范的哈希，CI 执行 `python scripts/check_docs.py check`。源文件变化后先审阅对应段落，再执行 `python scripts/check_docs.py record` 更新映射。该检查发现来源漂移，不代替语义审查；长手册不需要整份加载到每次 Skill 任务。
