# 第六阶段：灰度、接管和运营复盘

管理员GET/PUT `/api/v1/ops/rollout`管理数据库实时灰度配置，无需重启。默认enabled=true、percentage=100、stage=readonly（只读），shop/channel/staff_group来自服务端配置，不接受请求覆盖。

```json
{"enabled":true,"stage":"readonly","percentage":10,"shops":["demo-shop"],"channels":["api"],"staff_groups":["default"],"salt":"stage6-v1"}
```

stage依次readonly（问答）、tickets（加工单）、controlled（加退货草稿及通知待发送箱）。通知仍无真实发送器，controlled不代表支付或退款授权。模型/MCP依然不能自行执行高风险动作。默认100%只读用于本地体验，生产应从0%或小范围开始。

分桶使用SHA256(salt、店铺、渠道、客服组、客户标识) mod 100。相同配置/标识分桶稳定；改变salt会重新分桶。直接RAG接口没有客户标识，使用固定rag-direct桶，适合客服测试，不是按客户的灰度入口。主流量使用chat或agent/query。

一键停用：PUT rollout时enabled=false。chat、agent/query和直接RAG不再生成回答，受控写接口也拒绝执行。已开始的请求不会被强制中断；停用针对下一次请求。基础只读API和MCP工具作为人工客服能力仍可使用。

人工接管：POST `/api/v1/ops/conversations/{id}/takeover`，会话置human；后续chat/agent请求保留用户消息并返回人工提示，不调用Agent。仅恢复全局开关不会自动释放已接管会话，避免抢回人工任务。会话消息仍通过已有会话数据保存。没有实现人工桌面/聊天发送界面，接管是后端状态和路由能力。

GET `/api/v1/ops/metrics?days=7`提供窗口内会话数、已复核数、确认自动解决率、当前转人工率、确认失败率、RAG技术错误率和已知生成Token。自动解决必须由管理员PUT `/api/v1/ops/conversations/{id}/outcome`标记；handoff/human会话不能标auto_resolved。字段outcome为auto_resolved/human_resolved/failed/unresolved，reason_code只允许短分类码，禁止个人信息。

指标口径：会话率分母为窗口内创建的全部会话；未复核会话不计自动解决分子，因此auto_resolution_rate是确认值，不是模型返回成功率。转人工按当前会话状态统计，不是历史“曾转人工”事件率。RAG错误率分母为窗口内RagTrace数量，只有retrieval_failure/generation_failure/timeout算技术错误，缺证据和拒答不算服务故障。

Token为成功记录的生成、审核和增强Token之和，不覆盖所有失败调用及Embedding。费用返回null，不虚构模型金额。要报告真实单次成本，仍需供应商账单及实际Embedding/失败请求用量对账。

每周复盘：GET `/api/v1/ops/failures?days=7`返回已标failed/unresolved的会话ID和分类码。客服查看会话及RAG追踪，脱敏后加入开发集，补知识或发布规则新版本，再跑固定独立集回归。不会自动将敏感会话导出为训练集，也不自动修改政策。当前提供复盘接口与流程，不提供定时任务。

迁移：`python -m scripts.migrate_stage6`仅创建rollout_controls和conversation_outcomes；可重复执行。回滚时先停用Agent和受控写，再回退代码；旧版本不读取数据库灰度开关，所以必须保留环境RAG_ENABLED=false、CONTROLLED_WRITES_ENABLED=false并在网关停用chat/agent入口，不能只靠新表开关回滚。SQL Server、真实灰度流量、告警服务及压力测试未完成。

面试量化数据应包括测试总数和场景通过率；实际自动解决/转人工/错误率用本接口对应分母，标明窗口及样本量；灰度比例是配置目标，不是请求实际占比；模型成本待账单核算。建议上线目标：未授权写入0、停用后新请求模型调用0、人工接管后Agent抢答0，其他业务改善需上线前后可比样本，不能当作已实现业绩。
