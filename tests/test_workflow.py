from tests.test_stage4 import db
from app.agents.workflow import run_workflow
from app.core.auth import Role
from app.schemas import ChatRequest


def test_owned_order(db):
    result=run_workflow(db,ChatRequest(user_external_id="alice",message="查询订单",order_no="O1"),Role.AGENT)
    assert not result["handoff"] and result["skill"]=="orders"
    assert result["events"][-1]["tool"]=="query_order"


def test_other_customer_order_fails_closed(db):
    result=run_workflow(db,ChatRequest(user_external_id="bob",message="查询订单",order_no="O1"),Role.AGENT)
    assert result["handoff"] and "delivered" not in result["answer"]


def test_product_requires_id(db):
    result=run_workflow(db,ChatRequest(user_external_id="alice",message="查询库存"),Role.AGENT)
    assert result["handoff"]


def test_after_sale_policy(db):
    result=run_workflow(db,ChatRequest(user_external_id="alice",message="申请退货",order_no="O1",product_id=1,after_sale_action="return"),Role.AGENT)
    assert result["skill"]=="after_sale" and result["sources"]
