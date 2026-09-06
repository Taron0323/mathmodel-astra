# 动态系统：双室转移演练

本例为固定合成线性系统，初值为 A(0)=10 g、B(0)=0 g，转移率 k=0.7 /s，时间范围为 0 至 4 s：

$$
\frac{dA}{dt}=-kA,\qquad \frac{dB}{dt}=kA.
$$

解析解为 $A(t)=10\exp(-kt)$、$B(t)=10-A(t)$，因此总量守恒且两状态非负。另登记零转移率和零初始总量两个退化实例。全部参数与容差为演练值，不是测量结果或正式赛题结论。

求解使用 SciPy `solve_ivp` 的 RK23。设较宽的自适应误差容差，分别限制最大步长为 0.4 s、0.2 s、0.1 s，在同一 81 个时点保存结果。此处观察最大步长缩小后的误差变化，不将输出采样间隔等同于积分步长，也不由三个点声称所有系统的收敛阶。

验证独立重算解析解，并使用矩阵形式的 DOP853 高精度积分对照。十项检查覆盖网格完整、有限值、初值、非负、守恒、解析误差、独立积分、步长收敛、零转移率和零总量。细步长最大误差阈值为 1e-4 g，守恒为 1e-10 g，DOP853 与解析解的差异为 1e-9 g；这些值在运行前登记。

在仓库根目录使用已安装 `requirements-demo.txt` 的环境：

```sh
python scripts/ode_demo.py init --workspace practice/ode-demo
python scripts/run_workflow.py run --manifest practice/ode-demo/workflow.json
python scripts/run_workflow.py status --manifest practice/ode-demo/workflow.json
```

`init` 拒绝覆盖已有目录。流程为 `solve`、`validate`、`render`，输出轨迹 CSV、求解元数据、验证记录、PNG/SVG、中文报告和结论映射。绘图读取有效结果，不重新求解；单独 `render` 同样要求完整且版本匹配的验证。原始协议、数值结果、演练代码与运行器代码均参加证据哈希核对。

输入协议固定用于此反例族；处理新机理时另建项目模型、参数来源和验收，不能直接替换方程后沿用这十项通过结论。拟合参数、随机噪声、非线性系统、刚性系统及预测误差传播不在本例覆盖范围。

回归入口为 `python -m unittest discover -s tests/numerical -p test_ode_demo.py -v`，包括人为破坏守恒、重复或非有限样本、旧结果及不完整验证的拒绝检查。
