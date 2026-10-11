# 本地只读MCP服务

使用官方Python MCP SDK，以stdio提供标准工具发现和调用，入口为 `python -m app.mcp_server`。四个工具：query_product、query_order、query_logistics、check_after_sale_policy。工具声明只读；查询会写脱敏审计，不改变业务状态。

启动前通过MCP客户端的进程环境配置 `MCP_STAFF_TOKEN`，值为现有客服或管理员令牌；不要通过聊天或工具参数传递令牌。缺少/无效令牌启动拒绝。进程仅用于可信本地客服客户端，不能作为公开客户API；user_external_id是客服服务的客户，不是客户端身份认证。正式环境需替换共享令牌和客户身份绑定。

客户端配置示例（环境字段中的令牌在客户端本地配置）：

```json
{
  "mcpServers": {
    "ecom-ops": {
      "command": "python",
      "args": ["-m", "app.mcp_server"],
      "cwd": "C:/projects/ecom-ops",
      "env": {"MCP_STAFF_TOKEN": "在客户端本地填写客服令牌"}
    }
  }
}
```

请将示例cwd替换为实际项目路径。客户端必须支持cwd；否则使用能设置工作目录的启动方式，保证读取正确.env及数据库。stdout专用于协议，审计写入audit_logs：调用工具名、角色、成功/失败、request_id；不记录令牌、手机号、地址或订单正文。

订单及物流校验客户关联，不存在订单与非本人订单统一返回found=false。售后不执行退款/补发。库存来自当前数据库快照，尚未接真实电商平台同步。

本轮MCP服务独立运行，LangGraph仍直接调用同源业务函数；没有将其切换为MCP客户端。因此可以说“提供了标准MCP工具服务”，不能说“LangGraph已通过MCP调用全部工具”。不提供公开HTTP传输或OAuth。
