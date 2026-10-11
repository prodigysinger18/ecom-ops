"""Actual API -> LangGraph -> stdio MCP test. No model calls."""
from fastapi.testclient import TestClient
from app.main import app
from app.core.config import settings


def main():
    settings.agent_tool_transport="mcp"
    with TestClient(app) as client:
        response=client.post("/api/v1/agent/query",headers={"Authorization":"Bearer "+settings.agent_api_key},
                             json={"user_external_id":"user-001","message":"物流到哪了","order_no":"ECO202610090001"})
        response.raise_for_status()
        result=response.json()
        events=[e for e in result["events"] if e.get("tool")]
        if result["handoff"] or [e["tool"] for e in events]!=["query_order","query_logistics"]:
            raise SystemExit("workflow failed; verify demo order and customer association")
        if not all(e.get("transport")=="mcp" and e.get("remote_request_id") for e in events):
            raise SystemExit("MCP trace missing")
        print(f"api_langgraph_mcp=ok tools={len(events)} conversation_id={result['conversation_id']}")


if __name__=="__main__":main()
