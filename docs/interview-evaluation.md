# 面试评测交付与复跑指南

## 本次交付

1. `data/evaluation/holdout.json`：120条新编写候选咨询，12类，各10条。含问题、参考检查要点、必要证据文档、预期路由、处理方式、来源、审核人及审核状态。不是抓取的真实客户数据，全部 `reviewed=false`；参考答案是组内检查要点，正式评测前需逐题精修。与已有dev/test/routing-test无完全相同的问题，但语义主题重合，不声称统计独立。它现已被查看和运行，不能继续称为从未接触的盲测集。
2. 四组对照：向量、混合、混合+模型重排序、混合+重排序+证据评估。相同知识库、问题分解、候选上限、Top-k和上下文选择预算。live各组均有生成、引用校验和独立生成审核；证据组额外检查检索证据是否充分，顺序为检索/重排序→证据评估→生成，不关闭安全检查。
3. 六类业务故障注入、逐类XML和汇总报告。
4. `docs/design-decisions.md`：选择理由、代价、可关闭组件、失败根因和生产边界。

## 本次实际结果

| 项目 | 实测结果 | 能证明什么 |
| --- | --- | --- |
| 完整回归 | 172项通过 | 当前覆盖范围内的功能和评测工具工作 |
| 120条候选路由 | 107/120，89.17%，13条失败 | 当前规则有自然表达漏判；标签待人工复核，不是生产准确率 |
| 离线FAQ对照 | 每组60题，向量/混合文档Recall@5均100%，MRR@5均1.0，Precision@5均23.67% | 仅4份演示文档、本地hash向量；没有证明混合检索收益 |
| 故障验证 | 六类、15个用例均通过 | 模拟超时、缺失、越权、重复、更新和接管的已测行为 |
| 真实百炼冒烟 | 1题×4组，全部生成通过 | 真实服务和每个实验阶段可调用；不证明泛化效果 |

单题百炼实测（问题：普通商品付款后最晚多久发出）：

| 方案 | 本次执行耗时 | 已报告生成/审核Token | 已报告增强Token |
| --- | --- | --- | --- |
| 向量 | 3365.513ms | 604 | 0 |
| 混合 | 3628.827ms | 604 | 0 |
| 混合+重排序 | 5448.239ms | 458 | 538 |
| 混合+重排序+证据评估 | 6215.072ms | 458 | 703 |

单题耗时不能用作可信P95。这个问题四组Recall@5都是100%，增强有新增延迟和Token开销，没有已证实的质量收益。索引构建/Embedding用量和部分失败调用未完整计入；成本没有冒充账单。人审答案准确率、Faithfulness、幻觉率为 `null`，不是0或100%。

文档Precision@5按相关文档数/5计算；知识库仅4份文档且多数问题只需1份，分母固定为5导致低值，不应与chunk级Precision混用，也不能据此判断线上检索质量。

## 第一步：复跑本地评测

在项目目录终端逐条执行：

```powershell
python -m scripts.benchmark_interview
python -m scripts.analyze_interview
python -m scripts.verify_business_faults
python -m pytest -q -p no:cacheprovider --junitxml=reports/interview-regression.xml
```

默认离线，不读取真实业务库、不调用百炼。重排序和证据增强明确记为 `not_run`，不会用假重排序造提升。

结果文件：

- `reports/interview-benchmark.json`：逐题召回/回答、耗时、路由混淆矩阵、失败结果、数据和知识库指纹。
- `reports/interview-benchmark.analysis.json`：与向量基线的差值、13个路由失败问题。
- `reports/business-faults.json`：六类故障汇总，XML见 `reports/faults/`。
- `reports/interview-regression.xml`：完整回归。

每次重跑使用新run_id，会覆盖指定输出文件及对应review模板。完成审核后务必保存该次报告和review到单独文件夹，再运行新的实验。

## 第二步：完善数据标注

打开 `data/evaluation/holdout.json`，逐题核对 `expected_route`、`reference_answer` 和 `necessary_evidence`。特别是“商品是什么规格”既可能文档问答也可能业务查询，需要先统一业务标注规范；不能为了让模型正确而改标签。

确认后填写真实审核人和 `reviewed=true`。审核范围还应包括：问题是否自然、参考答案是否准确保留例外、证据是否必要、用户未提供业务参数时是否应澄清。不要执行build脚本覆盖已审核数据。

采集匿名化真实咨询时，将 `origin` 改为实际来源，删除联系方式、订单号等敏感数据，设置稳定ID和真实审核状态。通过单独开发集优化路由，然后再冻结另一批最终测试集。此次120题提供结构和候选，不能替代人工标注与真实用户采样。

仅运行已审核数据：

```powershell
python -m scripts.benchmark_interview --reviewed-only --output reports/interview-reviewed-labels.json
```

没有审核数据时会拒绝运行，防止产生假的“人工评测结果”。

## 第三步：运行真实模型对照

沿用现有`.env`百炼配置。先跑少量题确认通路，注意limit选择前N个FAQ问题，不是随机代表性采样：

```powershell
python -m scripts.benchmark_interview --live --limit 3 --output reports/interview-live-pilot.json
```

全部60条FAQ×4组会产生Embedding、生成、独立审核、重排序和证据评估调用。需要真实完整对照时去掉limit：

```powershell
python -m scripts.benchmark_interview --live --output reports/interview-live-full.json
```

相同知识库仅4篇不能体现真实规模；后续先增加经审核的真实业务资料和相近干扰文档，再用独立问题集评测。报告保留失败阶段，不以只筛选成功调用的结果说明整体性能。

## 第四步：审核答案，计算质量指标

每次报告旁边会生成 `.reviews.json`。对照同次报告中的回答、引用和参考检查要点，填写：

- `reviewer`：实际审核人。
- `reviewed=true`：确实完成审核。
- `answer_correct`：是否正确且完整回答问题。
- `has_hallucination`：是否出现无证据事实。
- `claims_total` / `claims_supported`：回答中可验证事实数/证据支持事实数。

不用动run_id和answer_sha256，工具用它们拒绝旧结果标签与重复标签。事实数必须逐条计数，不能随便填百分比。再执行：

```powershell
python -m scripts.review_interview --report reports/interview-live-full.json --reviews reports/interview-live-full.reviews.json --output reports/interview-live-reviewed.json
```

AnswerAccuracy=正确回答数/已审核回答数；Faithfulness=证据支持事实数/事实总数；幻觉率=含幻觉回答数/已审核回答数。不同模型profile分别计算，不把“审核过30条”写成“全部60条”。

## 第五步：比较收益与成本

```powershell
python -m scripts.analyze_interview --report reports/interview-live-full.json
```

结果给出各组相对向量的Recall/MRR/Precision、耗时和Token差值。答案质量以第四步人审报告为准，不能用模型自检通过率代替Faithfulness。

如需费用估算，增加 `--input-price` 和 `--output-price`，数值为你所用服务实际每百万Token价格。估算只覆盖已报告聊天用量；Embedding、索引、失败但未返回usage的调用和基础设施不计入，最终费用核对百炼账单。

## 面试可用表述

“为电商客服实现120条、12类咨询的可复跑候选评测，首次规则路由107/120，保留13条自然表达失败案例；建立向量/混合/重排序/证据评估四组对照和与具体回答绑定的人审指标工具，完成六类15项故障验证。百炼四组真实调用已通过单题冒烟，但目前未证明增强方案的质量收益，正在用独立人工标注与真实业务数据完善效果验证。”
