# 事实、结果和结论合同

CONTEXT 是口径入口，数值权威是参数表、运行元数据和结果文件。先发现项目已有字段，按其结构读写；没有时使用以下最小结构。

- 需求：`id,quote,source_file,source_page,deliverable,paper_location,result_file,status,review_note`。原句逐字保存，不能用解释覆盖原话。
- 参数：`parameter_id,symbol,value,unit,source_kind,source_path,source_locator,plausible_min,plausible_max,sensitivity_plan,status`。
- 结论：`claim_id,statement,value,unit,tolerance,result_file,result_row,result_column,code_file,run_id,paper_location,figure_file,verification_file,human_review,status`。
- AI使用：`entry_id,timestamp,phase,tool,model,effort,purpose,prompt_file,output_files,adopted_changes,human_modifications,verification_evidence,human_reviewer,human_review_date,status`。
- 人工核验：`check_id,scope,evidence_file,reviewer,reviewed_at,outcome,note`。仅实际队员核验后填写身份、日期和结论。

运行元数据至少包含起止时间、输入/代码/参数哈希、解释器和重要库版本、随机种子或确定性说明、命令、退出码、求解状态、日志和输出清单。公开资料记录请求和实际URL、发布日、访问日、SHA-256、内容类型与页码。

状态区分 `PLANNED`、`RUNNING`、`COMPUTED`、`AI_VERIFIED`、`HUMAN_VERIFIED`、`BLOCKED`、`NOT_APPLICABLE`。旧项目已有命名则映射沿用，不把不同状态压成一个 PASS。

每个参数说明来自题面、测量、拟合、公开文献还是合成演练。预估在运行前记录，计算后不要改写预估制造一致。引用作品的结论归作者，本项目未复现时不当成本项目验证。

结论冻结后出现冲突，记录旧结论及证据版本、新证据和受影响论文位置；先判断数据、算法、假设或表达哪个变了，再提出修订。数字只在权威结果中生成，写作读取数字，不手工把同一值维护在多份文件。

生成结果图表或更新结论前，验证记录应覆盖预先登记的检查项，并对应当前输入、参数、代码和结果文件。记录非空、检查 ID 完整且不重复、数值残差在容差内和版本一致是不同条件；CSV 中仅有 PASS 字样不能代替这些检查。失败重验应留下失败状态，不能自动退回引用较早的通过记录。沿用项目已有证据索引；随包运输演练的具体实现见 [运行接口](runtime.md)。
