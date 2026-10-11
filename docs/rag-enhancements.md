# Query Rewrite、Rerank 与受限 Self-RAG

在百炼模式下，`mode=auto` 的 single_hop 和 multi_evidence 路径启用增强；FAQ跳过额外调用。显式vector/keyword/hybrid模式保留为消融对照。由现有配置自动启用，无需额外密钥。

三个独立开关：RAG_REWRITE_ENABLED、RAG_RERANK_ENABLED、RAG_SELF_RAG_ENABLED；设为false关闭相应步骤。默认均true。RAG_RERANK_CANDIDATES默认8，最多20。

查询改写使用通义生成独立检索问题，原查询结果始终保留，与改写结果做RRF融合。改写失败回到原结果。改写不会改变商品、店铺、角色过滤；自动改写仍可能漂移，需要人工评测。

重排序使用通义列表式相关性判断，不是专用cross-encoder模型。每个候选ID必须出现一次且在合法范围，0/1分候选去除，2/3分保留；无效输出回退原排序。重排输入每段最多1800字符，长例外可能落在截断之外，最终上下文仍使用完整原分段，真实集应验证这种误差。

受限Self-RAG先判断证据是否充分、是否冲突；不足时允许一个补检索问题，补充一次并重新评估，仍不足则转人工。冲突不重试。生成返回insufficient时允许一次“仅回答有依据部分”的纠错生成；格式错误、服务故障或审核异常仍直接转人工，避免重复计费。该流程是受限证据反馈，不是自主多跳规划器或完整Self-RAG训练方法。

增强模型调用每请求最多6次，由总时间预算限制；生成和独立审核另计，最多两轮4次调用。Embedding另计。多条件复杂请求可能耗尽增强预算，此时证据审核无法完成会转人工。所有功能均保留超时、ACL和人工兜底。

Trace包含query_rewrite/rerank状态、各增强步骤耗时/成功失败、检索及生成重试次数、enhancement_token_usage。token_usage仍是生成与独立审核用量，增强用量单列；Embedding消耗与货币成本尚未合并，不能声称总成本已完整统计。没有记录改写正文以避免持久化敏感信息。

验收使用mock验证，不据此宣传实际质量提升。进行真实消融时固定同一人工标注集、Embedding及生成模型，比较显式hybrid和auto，再分别关闭单个开关；记录Precision、Recall、MRR、人工质量、耗时和真实用量。LangGraph、MCP/Skill动态加载仍未实现。
