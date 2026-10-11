# 电商售后助手：RAG增强阶段

## 业务边界与目标

支持商品使用FAQ、配送及退货政策解释；需要多个条款时最多进行两次检索，保留条款、例外和表格的章节上下文。订单/物流/实时库存走现有工具，具体订单售后资格走第四阶段规则引擎。资料不足、冲突、过期、超时或请求超出范围时明确说明并提供人工入口。

本次实现为模块化后端，Swagger是开发与验收入口。沿用已认证客服/管理员API令牌，不开放匿名客户查询。店铺范围来自服务端 `RAG_SHOP_ID`，检索角色来自认证令牌，用户不能通过请求改变角色或查询店铺。文档支持商品范围和角色权限；多店铺生产部署需将当前固定店铺绑定升级为个人账号的租户权限，而不是允许客户传任意店铺ID。

上线验收目标（不是已实测业绩）：独立人工标注集Recall@5≥90%、人工审核引用准确率≥95%、无答案正确兜底率≥95%、过期/越权引用0；初始端到端P95预算2秒。在线Embedding成本需按供应商返回用量、定价和实例费用核算，未配置供应商前不虚构单次金额。

## 快速运行

```powershell
python -m pip install -r requirements.txt
python -m scripts.migrate_rag
python -m scripts.seed_rag
python -m scripts.seed
python -m uvicorn app.main:app --reload
```

访问 `http://127.0.0.1:8000/docs`。`seed_rag` 导入4份演示资料，与正式商家政策无关；仅在开发数据库运行。已有 `.env` 指向外部Redis时应确认其可用；没有 `.env` 时本地默认不需要Redis、Celery或模型密钥。

`POST /api/v1/rag/query` 携带 `Authorization: Bearer dev-agent-key`：

```json
{"question":"商品发货时效；退货运费谁承担","mode":"vector"}
```

响应包含原文回答、文档/章节/来源/版本/原文引用、是否转人工、失败原因、追踪ID及耗时。`/api/v1/chat` 在没有订单号的文档问题上接入RAG，保留完整会话和工具记录；带订单号的查询沿用业务工具。`RAG_ENABLED=false` 可回到原路由，直接RAG接口会返回停用提示。启用后不会自动将旧知识库导入新索引。

## 文档与索引同步

管理员接口：

- `PUT /api/v1/knowledge/documents/{key}`：新增或更新文档及其索引。
- `DELETE /api/v1/knowledge/documents/{key}`：删除文档和所有分段。
- `GET /api/v1/rag/traces/{id}`：查看脱敏追踪，普通客服无权查看。

请求字段：title、source_url、shop_id、version、effective_at（含时区）、content；可选product_id、expires_at、allowed_roles、claims。

文件导入只支持已审核UTF-8 Markdown或text；PDF、扫描件、Word等需先完成对应提取及人工校对。本版本不做OCR。CLI读取正文与元数据JSON：

```powershell
python -m scripts.import_knowledge --key merchant-returns --file returns.md --metadata returns.meta.json
```

元数据示例（示例政策不代表正式政策）：

```json
{
  "title":"商家退货政策",
  "source_url":"https://example.com/policies/returns",
  "shop_id":"demo-shop",
  "version":"merchant-v1",
  "effective_at":"2026-10-10T00:00:00+08:00",
  "allowed_roles":["agent","admin"],
  "claims":{"return_days":"7"}
}
```

清洗执行Unicode规范化、换行与空白清理、同文档重复段落去重。按Markdown章节切分，不将条款例外、表格拆成无上下文片段；单章超过6000字符拒绝导入，要求按业务条款重分章节。索引包括标题、章节和正文。更新先完成Embedding再事务替换分段；索引失败保留旧版本。重复导入同key不重复累积分段；同key更新立即替换，暂不保存文档历史，请在外部版本管理保留原文。不要提前更新同key到未来版本，因为这会替换当前版本；未来发布请通过不同文档key及有效期配置管理。

`claims` 为审核人员提供的关键结构化事实；检索资料对同一claim给出不同值时转人工。这不等同于自动理解任意自然语言冲突，未标注claims的矛盾仍须人工评测。检索按店铺、角色、商品、有效期过滤，没有商品上下文时不使用特定商品资料。

## 检索、回答与成本边界

默认向量基线：512维归一化词元哈希向量，中文使用双字词元。这是可复现的词汇向量，不是预训练语义Embedding。同库还提供BM25关键词基线与RRF融合的hybrid模式，用于对比。当前小规模演示无需外置向量数据库；超过1000个候选分段时兜底，正式扩容需迁移向量检索存储。

可配置完整Embedding HTTP端点、密钥及模型。接口采用 `{model,input}` 请求及 `{data:[{index,embedding}]}` 响应，设有HTTP超时；只使用匹配模型的索引。模型切换后需重新导入所有文档，防止混用向量。在线Embedding服务未实际联调，调用失败返回人工兜底。

默认回答模式为 **extractive**：组织检索到的原文及引用，不调用生成模型、不推导未经支持的订单结论。证据支持检查为原文引用一致性。它减少编造，但不能替代人工审核证据是否适用。不存在模型“自检即正确”的假设。

多条件问题按分号、“以及”、“并且”拆分，最多两步；每个子问题都必须查到证据。相似度阈值可配置，最多5条检索结果，每步最多2段用于回答，上下文总量上限8000字符。HTTP服务每次调用有超时；整体时间预算在响应前检查，不等同于全链路可中断的硬实时截止。

未接入生成模型、语义重排序或模型查询改写；基线尚未证明需要它们，后续只对发现的具体错误加能力并比较新增成本。外部模型调用成本字段为null表示未计费/未知，不是系统运行成本为零。

## 安全、追踪和灰度

文档疑似恶意指令在导入时拒绝，检索后再检查；资料永远作为被引用的数据，无法指定工具执行。此规则检测并非完整的注入防御证明，真实对抗集需持续维护。

追踪保存路由、检索模式、Embedding模型、排名/分数、最终分段及版本、步骤数、兜底原因、耗时、成本未知标记。避免保存原问题、正文、个人信息及供应商异常；原会话另由已有消息表保存。工具查询记录在已有工具表。查询改写标记none，未进行额外生成或重试。

灰度前必须检查真实用户认证与订单归属、真实Redis及异步任务执行/重试（上一阶段worker仍是占位逻辑）、Embedding故障、并发性能及租户权限。当前共享客服令牌不代表真实客户身份认证。

迁移新增rag_documents、rag_chunks、rag_traces，脚本可重复执行，不修改旧表。关闭RAG或回滚代码时保留新表、追踪及审计，不自动删数据。SQL Server及真实中间件未在本次环境验收。

## 评测与维护

```powershell
python -m pytest -q --junitxml=reports/rag-tests.xml
python -m scripts.evaluate_rag --split dev
python -m scripts.evaluate_rag --split test
```

开发集与独立测试集分别在data/rag/dev.json、test.json。每条包含问题、参考答案、必要证据、期望分支及类别；独立集覆盖简单、例外、多证据、无答案、实时工具、指令注入、过期和冲突。参考答案供人工复核，脚本不将自动文档召回当作人工答案正确率。

reports/rag-test-baseline.json记录各模式逐题结果、文档Recall@5、分支正确率、串行本地P95。数据为合成样例，不能用于宣传真实业务准确率或并发SLA。原文引用一致性也不能当成语义引用准确率。

后续将已脱敏、人工标注的历史问题加入开发集；保留固定独立测试集，记录每个版本的参数、样本规模、召回、误放行、兜底、答案正确率与成本。只有发现术语遗漏、排名错误或省略上下文时再评测BM25/RRF、Rerank或改写。

复杂度路由和指标验收见 [reports/routing-v2-acceptance.md](../reports/routing-v2-acceptance.md)。当前路由是确定性规则，不应包装成模型意图识别；Faithfulness、Answer Accuracy 和 Hallucination Rate 没有人工标签时保持 N/A。
