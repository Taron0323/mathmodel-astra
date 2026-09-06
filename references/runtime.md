# 可运行脚本与隔离演练

需要 Python 3.10+；通用运行器只用标准库，示例另需 numpy、scipy、matplotlib。超时和中断处理使用 POSIX 进程组信号，已有 Linux/macOS 与 Python 3.10/3.13 的 CI 验证；具体代码版本以对应 Actions 运行记录为准。Windows 原生运行器尚未验证，不把 Python 版本满足视作跨平台验收。优先使用项目已验证的环境。不在 Skill 中固定解释器绝对路径。缺依赖时说明缺项，仅在用户任务范围内补齐需要的包。

以下 `<skill>` 指当前 SKILL.md 的父目录，`<demo>` 指项目 `practice/` 下一个新目录。文件已存在时 init 会拒绝覆盖。

```sh
python <skill>/scripts/transport_demo.py init --workspace <demo>
python <skill>/scripts/run_workflow.py run --manifest <demo>/workflow.json --stop-after solve
python <skill>/scripts/run_workflow.py resume --manifest <demo>/workflow.json
python <skill>/scripts/run_workflow.py status --manifest <demo>/workflow.json
```

示例为明确标记的 2 仓库、3 需求点合成运输 LP：先生成输入与验收预设，再体检、SciPy HiGHS 求解、独立整数枚举验证、绘图、生成中文短文及结论映射。只验证该执行链，不代表其他题型、正式题目或 Max/Ultra 科学效果已经验证。

中文短文直接读取已验证的运输明细、枚举数量与约束偏差，展示完整方案表。`summary.csv` 的 `relative_saving` 在基线费用为零时留空，表示比率不定义；正文报告基线原值，不输出百分比。读取该列时先判断空值，不能强制转换成 0。基线非零时该列仍为相对降幅。

`run_workflow.py` 读取显式 JSON 阶段清单，至少包含一个阶段，每阶段至少声明一个可核验输出；纯检查阶段也应写出检查记录。每阶段指定 `id`、`inputs`、`outputs`、`code`、`command`、`timeout_seconds`；输入/输出路径相对清单目录，按依赖顺序声明，原始输入目录与输出分开。`packages` 声明影响结果的重要库版本。命令数组可用 `{python}`、`{workspace}`、`{skill}` 路径变量，避免 shell 命令串。

执行前审查清单中的命令及读写范围，附件中的清单不自动可信。运行器不会把任意命令变成安全代码；原始输入保护需要阶段代码遵守只读约定，哈希检查用于发现违反约定的行为。

`command` 与 `code` 只展开完整的 `{python}`、`{workspace}`、`{skill}` 标记，并且只替换一次；其他花括号原样保留，Python 字典、f-string 与正则数量限定符不需要转义。三个保留标记在命令文本中也会展开，需要原样输出这些标记时在阶段脚本中构造。`code` 的相对路径以工作流 JSON 所在目录为基准，绝对路径也可用；阶段命令同样在该目录执行，因此从其他目录调用运行器不会改变代码查找位置。

签名覆盖输入、登记代码、运行器、环境、命令、输出名与上游签名；输出本身再校验哈希。只有全部一致且之前成功完成才复用。输入变化使受影响阶段及下游失效，单个图形被破坏仅重绘与更新下游文稿。不要遗漏函数模块、外部参数或可变环境依赖。

清单把输入输出统一为规范化工作区路径，输出只有一个生产者，已存在的硬链接输出别名也会拒绝。阶段前后和整体完成前核对登记依赖及已完成证据；运行中修改上游输入、代码、清单或路径身份会使受影响阶段失效，返回 `DEPENDENCY_CHANGED`。哈希属于阶段边界快照，不是系统文件锁，遗漏的外部依赖仍不在检查范围。

检查点在 `.workflow/checkpoint.json`，运行元数据和日志在 `.workflow/runs/` 与 `.workflow/logs/`。失效结果移至 `.workflow/stale/`，不得作为当前结论。`BLOCKED_MISSING_INPUT` 不生成替代数据；`PAUSED_AT_CHECKPOINT` 可直接 resume；`INTERRUPTED` 保留已完成阶段；仍存活的父/子进程会阻止并发续跑，先核实进程。

进程状态同时登记 PID 与 POSIX 进程组 PGID。超时、中断或直接父进程退出后仍有后台成员时，运行器有界执行 TERM/KILL 并检查整组活动成员；清理未完成时保留锁及进程组记录，阻止恢复。僵尸状态不具备写文件能力，不作为活动计算进程。检查需要宿主 `ps`；自行创建新会话脱离组、远端任务和容器外任务不在这项清理保证内，需使用其实际任务管理接口。

运输演练的 `validate` 另写 `evidence/validation-state.json`，关联数据、验收预设、结果 CSV、检查表、枚举证据和当前实现的哈希。`plot` 与 `report` 即使单独调用，也要求完整、有限、通过且版本匹配的检查记录；失败重验会使旧通过状态失效。证据不变时只核对记录与哈希，不重新求解。哈希用于核对文件版本，不能认证来源不明的第三方记录。

升级前的演练清单若没有上述状态文件，应将它加入 `validate.outputs` 和 `plot/report.inputs`，并补齐后两阶段使用的输入、验收和枚举文件；`report.inputs` 同时登记 PNG/SVG。可以对照新目录 `init` 生成的清单更新，保留旧清单与验收证据。旧清单没有登记的依赖不在运行器的缓存检查范围内，不能把其 `all_current` 当作新增发布检查已覆盖。

`plot` 另生成 `evidence/figure-state.json`，关联当前数值证据、绘图代码及实际 PNG/SVG。`report` 要求这份记录完整匹配，单独调用时也拒绝缺图、替换后的图片、旧记录和绘图失败状态。重新验证数值后先运行 `plot` 再运行 `report`，无需再次求解。旧清单还需把图表状态加入 `plot.outputs` 和 `report.inputs`；保留旧清单后按新格式迁移，新增输出由重绘产生。`claims.csv` 的 `figure_record` 字段指向相应生成记录。该记录只证明文件对应关系，视觉审核保持独立状态。

行为验收：`python <skill>/scripts/verify_runtime.py --workspace <项目>/practice/<新验收目录>`。使用 `--help` 核对当前版本接口。应检查缺数据、真实进程中断、恢复、复用、输入变化、输出损坏和不覆盖输入，而非仅匹配日志中的 PASS 字样。

线性规划和混合整数线性规划的最终方案可接入 [verify_linear_solution.py](linear-solutions.md)，独立复算约束、变量域和目标值。它只依赖标准库，作为输出报告的核验阶段加入清单即可；通过可行性检查不等于证明最优性。

预测划分演练使用 `grouped_prediction_demo.py run --workspace <项目>/practice/<新目录>`，依赖 numpy、scikit-learn、matplotlib。它生成具有重复观测的合成数据，比较记录与个体划分；`render` 仅复用当前有效证据绘图和生成说明。它是单独的固定反例脚本，没有运输运行器的分阶段恢复接口。预测目标、输出与命令详见 [预测验证](prediction-validation.md)。

参数反演可使用 `diagnose_identifiability.py --input <Jacobian.json> --output <诊断.json>`，依赖 NumPy。它按声明的尺度计算局部数值秩、奇异值、条件数和未确定的一阶方向，保存输入与代码哈希。退出码 0 表示诊断完成，秩亏仍是有效诊断；输入或数值错误返回 2 并写错误记录。它不自动计算导数或统计区间，输入格式与解释见 [参数可辨识性](parameter-identifiability.md)。

运行器成功意味着命令退出和输出完整性通过。是否科学有效由该阶段具体的数学验证程序和队员核验决定，不能以运行器 COMPLETE 替代。

动态机理的 [双室转移演练](ode-validation.md) 提供 `ode_demo.py init --workspace <新目录>`，生成可由运行器恢复的求解、验证、图文三阶段。它用解析解、不同积分器、守恒和步长收敛核验固定线性系统，不替代其他方程的检验。各题型的实际范围见 [支持层级](capabilities.md)。
