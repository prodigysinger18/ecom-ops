"""Bounded LangGraph workflow; all tools are read-only and server-selected."""
from typing import TypedDict
from time import perf_counter
from langgraph.graph import StateGraph, START, END
from sqlalchemy import select
from app.models import User, ToolCall
from app.tools.readonly import query_order, query_logistics, query_product, product_reply
from app.tools.after_sale import check_after_sale_policy
from app.policies.schemas import PolicyCheck
from app.rag.routing import route
from app.rag.service import answer
from app.core.config import settings
from app.agents.mcp_client import call_tool
from app.agents.router import classify
import json


SKILLS = {
    "orders": ("query_order",),
    "logistics": ("query_order", "query_logistics"),
    "catalog": ("query_product",),
    "after_sale": ("query_order", "check_after_sale_policy"),
    "knowledge": ("retrieve_knowledge",),
}


class State(TypedDict, total=False):
    plan: list[str]
    answer: str
    sources: list[str]
    handoff: bool
    events: list[dict]
    skill: str


def run_workflow(db, payload, role, conversation_id=None):
    deadline = perf_counter()+45
    def plan(state):
        strategy = route(payload.message, order_no=payload.order_no, product_id=payload.product_id,
                         action=payload.after_sale_action)
        if payload.after_sale_action:
            skill = "after_sale"
        elif strategy.name == "tool" and classify(payload.message).name == "product_query":
            skill = "catalog"
        elif strategy.name == "tool" and classify(payload.message).name == "logistics_query":
            skill = "logistics"
        elif payload.order_no or (strategy.name == "tool" and classify(payload.message).name == "order_query"):
            skill = "orders"
        else:
            skill = "knowledge"
        return {"skill":skill,"plan":list(SKILLS[skill]), "events":[{"node":"plan","skill":skill,"strategy":strategy.name}]}

    def execute(state):
        events = list(state["events"])
        user = db.scalar(select(User).where(User.external_id == payload.user_external_id))
        if not user:
            return {"answer":"未找到可核验用户，请转人工。","handoff":True,"events":events}
        sources, texts = [], []
        order = None
        handoff = False
        for name in state["plan"]:
            if perf_counter() > deadline:
                return {"answer":"执行超时，请转人工。","handoff":True,"events":events}
            try:
                remote_request_id = None
                if name != "retrieve_knowledge" and settings.agent_tool_transport == "mcp":
                    arguments = {"user_external_id":user.external_id,"order_no":payload.order_no}
                    if name == "query_product":
                        arguments = {"product_id":payload.product_id}
                    elif name == "check_after_sale_policy":
                        arguments.update(product_id=payload.product_id,action=payload.after_sale_action)
                    data = call_tool(name,arguments,role,deadline-perf_counter())
                    remote_request_id = data["request_id"]
                    if name == "check_after_sale_policy":
                        if "eligible" not in data or not isinstance(data.get("handoff"),bool):
                            raise ValueError("invalid policy response")
                        texts.append(data["reason"]);handoff=data["handoff"]
                        if data.get("basis"):sources.append(f"{data['basis']}（版本 {data['policy_version']}）")
                    else:
                        if data.get("found") is not True:raise ValueError("no associated data")
                        if name == "query_order":texts.append(f"订单状态：{data['status']}。")
                        elif name == "query_logistics":texts.append(f"物流状态：{data['status']}，{data.get('last_event') or '暂无最新轨迹'}。")
                        elif name == "query_product":texts.append(product_reply(data, payload.message))
                elif name == "query_order":
                    order = query_order(db,payload.order_no,user.id) if payload.order_no else None
                    if not order: raise ValueError("order association unavailable")
                    texts.append(f"订单状态：{order.status}。")
                elif name == "query_logistics":
                    item = query_logistics(db,payload.order_no,user.id)
                    if not item: raise ValueError("no logistics")
                    texts.append(f"物流状态：{item.status}，{item.last_event or '暂无最新轨迹'}。")
                elif name == "query_product":
                    item = query_product(db,product_id=payload.product_id) if payload.product_id else None
                    if not item: raise ValueError("product missing")
                    texts.append(product_reply(item, payload.message))
                elif name == "check_after_sale_policy":
                    result = check_after_sale_policy(db,PolicyCheck(user_external_id=user.external_id,
                        order_no=payload.order_no,product_id=payload.product_id,action=payload.after_sale_action))
                    texts.append(result.reason); handoff=result.handoff
                    if result.basis: sources.append(f"{result.basis}（版本 {result.policy_version}）")
                elif name == "retrieve_knowledge":
                    result=answer(db,payload.message,role,payload.product_id)
                    texts.append(result.answer); handoff=result.handoff
                    sources=[f"{c.title}/{c.section}（版本 {c.version}）：{c.source_url}" for c in result.citations]
                else:
                    raise ValueError("unregistered tool")
                event={"node":"execute","tool":name,"success":True}
                event.update(transport=settings.agent_tool_transport if name != "retrieve_knowledge" else "local",
                             remote_request_id=remote_request_id)
            except Exception:
                texts=["工具失败、参数不足或订单关联无法确认，请转人工核实。"]
                handoff=True
                event={"node":"execute","tool":name,"success":False}
                event["transport"]=settings.agent_tool_transport if name != "retrieve_knowledge" else "local"
            events.append(event)
            if conversation_id:
                db.add(ToolCall(conversation_id=conversation_id,tool_name=name,arguments="{}",
                    result=json.dumps(event),success=event["success"]))
            if not event["success"]: break
        return {"answer":"\n".join(texts),"sources":sources,"handoff":handoff,"events":events}

    graph=StateGraph(State)
    graph.add_node("plan",plan); graph.add_node("execute",execute)
    graph.add_edge(START,"plan"); graph.add_edge("plan","execute"); graph.add_edge("execute",END)
    return graph.compile().invoke({},config={"recursion_limit":6})
