# LangGraph只读工作流

请求示例：`{"user_external_id":"user-001","message":"查物流到哪了","order_no":"ECO202610090001"}`。调用需携带现有客服Bearer令牌。返回skill、events、answer、handoff和conversation_id；会话消息和工具事件写入数据库。

本轮全项目99/99测试通过。新增工作流测试覆盖本人订单、错误用户、库存缺少商品ID和售后规则。该结果不代表线上Agent成功率，生产需另测任务完成率、工具参数错误率、真实延迟和成本。

POST /api/v1/agent/query接受与chat相同的请求，并使用相同客服认证。
LangGraph流程为START → plan → execute → END，递归上限6。规划器为确定性规则，按需选择orders、logistics、catalog、after_sale、knowledge能力集合，只有只读工具，没有模型任意工具执行。
订单查询先验证用户关联，物流和售后以订单关联为前置；库存要求商品ID。知识路径复用现有增强RAG。参数缺失、工具失败、订单不归属均转人工。
这是受限工作流，不是自主多跳规划器。Skill在这里指项目内部能力集合，不是Codex SKILL.md插件。尚未提供MCP协议服务；不能宣称已接入MCP。
执行检查45秒预算，但同步工具仍需要独立网络/数据库超时，不能将检查视为硬实时中断。
