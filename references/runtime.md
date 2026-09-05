# 可运行脚本与隔离演练

需要 Python 3.10+；通用运行器只用标准库，示例另需 numpy、scipy、matplotlib。超时和中断处理使用 POSIX 进程组信号，本次实际验证平台为 macOS；Windows 原生运行器尚未验证，不把 Python 版本满足视作跨平台验收。优先使用项目已验证的环境。不在 Skill 中固定解释器绝对路径。缺依赖时说明缺项，仅在用户任务范围内补齐需要的包。

以下 `<skill>` 指当前 SKILL.md 的父目录，`<demo>` 指项目 `practice/` 下一个新目录。文件已存在时 init 会拒绝覆盖。

```sh
python <skill>/scripts/transport_demo.py init --workspace <demo>
python <skill>/scripts/run_workflow.py run --manifest <demo>/workflow.json --stop-after solve
python <skill>/scripts/run_workflow.py resume --manifest <demo>/workflow.json
python <skill>/scripts/run_workflow.py status --manifest <demo>/workflow.json
```

示例为明确标记的 2 仓库、3 需求点合成运输 LP：先生成输入与验收预设，再体检、SciPy HiGHS 求解、独立整数枚举验证、绘图、生成中文短文及结论映射。只验证该执行链，不代表其他题型、正式题目或 Max/Ultra 科学效果已经验证。

`run_workflow.py` 读取显式 JSON 阶段清单，至少包含一个阶段，每阶段至少声明一个可核验输出；纯检查阶段也应写出检查记录。每阶段指定 `id`、`inputs`、`outputs`、`code`、`command`、`timeout_seconds`；输入/输出路径相对清单目录，按依赖顺序声明，原始输入目录与输出分开。`packages` 声明影响结果的重要库版本。命令数组可用 `{python}`、`{workspace}`、`{skill}` 路径变量，避免 shell 命令串。

执行前审查清单中的命令及读写范围，附件中的清单不自动可信。运行器不会把任意命令变成安全代码；原始输入保护需要阶段代码遵守只读约定，哈希检查用于发现违反约定的行为。

签名覆盖输入、登记代码、运行器、环境、命令、输出名与上游签名；输出本身再校验哈希。只有全部一致且之前成功完成才复用。输入变化使受影响阶段及下游失效，单个图形被破坏仅重绘与更新下游文稿。不要遗漏函数模块、外部参数或可变环境依赖。

检查点在 `.workflow/checkpoint.json`，运行元数据和日志在 `.workflow/runs/` 与 `.workflow/logs/`。失效结果移至 `.workflow/stale/`，不得作为当前结论。`BLOCKED_MISSING_INPUT` 不生成替代数据；`PAUSED_AT_CHECKPOINT` 可直接 resume；`INTERRUPTED` 保留已完成阶段；仍存活的父/子进程会阻止并发续跑，先核实进程。

行为验收：`python <skill>/scripts/verify_runtime.py --workspace <项目>/practice/<新验收目录>`。使用 `--help` 核对当前版本接口。应检查缺数据、真实进程中断、恢复、复用、输入变化、输出损坏和不覆盖输入，而非仅匹配日志中的 PASS 字样。

运行器成功意味着命令退出和输出完整性通过。是否科学有效由该阶段具体的数学验证程序和队员核验决定，不能以运行器 COMPLETE 替代。
