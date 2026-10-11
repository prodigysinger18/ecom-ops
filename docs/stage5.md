# 第五阶段：受控写操作

新增三个已认证客服接口：POST /api/v1/tickets、POST /api/v1/return-drafts、POST /api/v1/notifications?kind=policy或logistics。

请求示例：

```json
{"user_external_id":"stage4-demo-user","order_no":"STAGE4-DEMO-ORDER","product_id":1,"confirmed":true}
```

须使用实际商品ID，可选conversation_id。请求头Authorization使用现有客服令牌，Idempotency-Key使用1至128位字母数字/点/下划线/短横线。confirmed=true仅表示已认证客服显式提交低风险动作，不代表客户授权或高风险审批。

工单校验订单归属、订单商品和会话归属，记录政策决定及完整历史消息；政策不满足仍允许创建人工申诉工单。退货草稿要求当前政策eligible=true，仅写入draft，不向平台提交退货，不退款，不改变订单状态。通知保存服务器生成的真实物流快照/政策依据至outbox，始终pending及sent=false。无真实发送渠道，不伪报送达；本阶段未实现发送执行器。

controlled_actions保存幂等键、请求指纹、操作者、用户及订单关联、动作、金额0、规则版本、确认身份、request_id、外部响应not_called及返回结果。业务资源、动作记录和审计在同一事务内提交。唯一幂等键在写资源前占位；相同键与相同请求返回首次结果，不重新判断历史规则；不同内容或角色复用键返回409。不同键仍可创建多个资源，不能把它宣传成跨键业务去重。

confirmed_by来自现有staff:agent/admin认证身份，共享令牌无法追溯具体员工。上线前必须接个人身份认证、客户授权和高风险审批；暂不开放退款、补发、改地址、改金额接口，也不将写工具注册给MCP或模型自主调用。由已认证客服明确提交，而不是模型自行执行。

迁移：`python -m scripts.migrate_stage5`，新增controlled_actions、return_drafts、notification_outbox，不修改旧表。可重复执行。本地SQLite验收，SQL Server尚未验证。部署前备份；回滚设置CONTROLLED_WRITES_ENABLED=false并回退应用版本，保留记录，不自动删表。

第三阶段原人工转接接口仍存在，其旧幂等行为不等同于本轮唯一键事务保障；新受控创建路径采用本轮接口。后续应统一旧路径并验证真实通知渠道、故障重试与出站消息幂等。

面试指标应关注：相同幂等键重试的重复资源数、不同请求复用键拦截率、审计/政策追溯完整率、规则拒绝时草稿误放行率、未授权写入数，以及真实通知成功率/死信率。本轮只能报告自动化测试结果，通知成功率未测，因为没有调用发送渠道。
