"""Explicit live test: two generation calls and one embedding call incur provider usage."""
from app.core.config import settings
from app.rag.embeddings import embed
from app.rag.generation import generate
from app.rag.schemas import Citation


def main():
    if settings.rag_provider != "dashscope" or not settings.dashscope_api_key:
        raise SystemExit("请先在.env配置RAG_PROVIDER=dashscope及DASHSCOPE_API_KEY")
    try:
        vector=embed(["耳机如何连接手机蓝牙？"])[0]
        cite=Citation(document_key="smoke-test",chunk_id=1,title="测试说明",section="蓝牙连接",source_url="/docs/test",
                      version="test",quote="长按耳机电源键5秒进入配对模式，然后在手机蓝牙设置中选择耳机名称。")
        text,used,usage,reason=generate("耳机如何连接手机？",[cite],15)
        if not text:raise ValueError("generation refused")
        print(f"live embedding dimension={len(vector)}; generation={reason}; tokens={usage.get('total_tokens','unknown')}")
        print(text)
    except Exception:
        raise SystemExit("百炼联调未通过；请核实区域、模型权限、密钥和网络。未输出供应商原始异常。") from None


if __name__ == "__main__":main()
