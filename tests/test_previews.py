import pytest
from tests.test_readiness import client, db, customer_tokens, AGENT


@pytest.mark.parametrize("endpoint", ["/api/v1/chat", "/api/v1/agent/query"])
@pytest.mark.parametrize("message,args,kind", [
    ("查询商品库存", {"product_id": 1}, "product"),
    ("查询订单状态", {"order_no": "O1"}, "order"),
])
def test_business_previews(client, endpoint, message, args, kind):
    r = client.post(endpoint, headers=AGENT, json={"user_external_id": "alice", "message": message, **args})
    assert r.status_code == 200
    card = r.json()["previews"][0]
    assert card["kind"] == kind
    assert card["url"].startswith("/api/v1/previews/") and "?" not in card["url"]
    assert client.get(card["url"]).status_code == 401
    assert client.get(card["url"], headers=AGENT).json() == card
    assert "shipping_address" not in str(card) and "phone" not in str(card)


def test_customer_preview_ownership_and_revocation(client, customer_tokens):
    auth = customer_tokens["alice"]
    result = client.post("/api/v1/customer/chat", headers=auth, json={"message": "查询订单状态", "order_no": "O1"}).json()
    url = result["previews"][0]["url"]
    assert client.get(url, headers=auth).status_code == 200
    assert client.get(url, headers=customer_tokens["bob"]).status_code == 404
    denied = client.post("/api/v1/customer/chat", headers=customer_tokens["bob"], json={"message": "查询订单状态", "order_no": "O1"}).json()
    assert denied["previews"] == []
    client.post("/api/v1/customer/logout", headers=auth)
    assert client.get(url, headers=auth).status_code == 401


def test_customer_can_preview_products(client, customer_tokens):
    assert client.get("/api/v1/previews/products/1", headers=customer_tokens["alice"]).json()["kind"] == "product"


@pytest.mark.parametrize("payload", [
    {"message": "明天会下雨吗", "product_id": 1},
    {"message": "查询商品库存", "product_id": 999},
    {"message": "查询订单状态", "order_no": "missing"},
    {"message": "帮我取消订单", "order_no": "O1"},
])
def test_no_cards_for_fallback_or_failed_business(client, payload):
    result = client.post("/api/v1/chat", headers=AGENT, json={"user_external_id": "alice", **payload})
    assert result.status_code == 200 and result.json()["previews"] == []
