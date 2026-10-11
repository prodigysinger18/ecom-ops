# 百炼真实语义检索与生成

复制 `.env.dashscope.example` 中配置到项目 `.env`，只在本地填写 `DASHSCOPE_API_KEY`。模型默认text-embedding-v3与qwen-plus，按账号实际模型权限修改。北京区域默认API为 `https://dashscope.aliyuncs.com/compatible-mode/v1`；其他区域应使用账号对应兼容接口及该区域密钥，不混用。

```powershell
python -m scripts.check_dashscope
python -m scripts.reindex_rag
python -m uvicorn app.main:app --reload
```

第一条命令会执行真实Embedding、生成和独立审核，产生API费用；成功才算真实服务联调通过。第二条将已有资料全部重建为当前Embedding模型，事务失败保留旧索引。尚未导入资料时按rag.md先迁移并导入经审核资料；seed_rag仅用于演示，不可当正式商家政策。切换Embedding服务/模型后所有文档必须重建索引，旧词元向量不会与新向量混用。

每个知识回答最多两次检索、一次生成、一次独立审核，设置输入上下文和输出Token上限；没有自动重试，以免供应商超时后重复计费。Embedding导入按最多10条分批，查询按单问题调用。单次网络调用受HTTP超时限制，整体预算超出后返回人工兜底。生成服务不调用业务工具，订单退款和补发仍受原规则与人工流程控制。

生成以JSON结论列表返回，每条结论附引用ID及连续原文。程序检查引用存在、原文匹配、输出未截断和明显越权承诺；另一次模型调用审核结论是否被原文支持且保留例外。独立模型审核仍可能出错，不能替代真实人工标注评测。模型拒答、格式错误、审核失败或服务故障都明确转人工。

响应generation_mode=dashscope，model记录模型，token_usage为生成和审核两次调用实际返回的总用量。当前费用为null：价格因区域、模型和档位而异，且Embedding索引及请求用量未纳入单次金额，不虚报成本。供应商原始异常、密钥和完整问题不进入RAG追踪。

配置中的相似度0.3仅为初始开发阈值，需要使用真实Embedding与独立数据校准。先在开发集调参，再冻结配置运行真实标注测试集；已有本地合成报告不代表新模型效果。生产前须完成区域模型可用性验证、并发、故障注入、数据授权与个人客户认证。
