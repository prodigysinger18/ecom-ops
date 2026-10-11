from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Literal
from pydantic import SecretStr, Field


class Settings(BaseSettings):
    database_url: str = "sqlite:///./ecom_ops.db"
    app_env: str = "development"
    log_level: str = "INFO"
    admin_api_key: str = "dev-admin-key"
    agent_api_key: str = "dev-agent-key"
    customer_jwt_secret: SecretStr | None = None
    customer_jwt_issuer: str = "ecom-ops"
    customer_jwt_audience: str = "ecom-customer"
    customer_token_ttl_seconds: int = Field(default=1800, ge=60, le=3600)
    demo_enabled: bool = True
    redis_url: str | None = None
    celery_broker_url: str | None = None
    celery_result_backend: str | None = None
    rag_enabled: bool = True
    rag_shop_id: str = "demo-shop"
    rag_embedding_url: str | None = None
    rag_embedding_key: str | None = None
    rag_embedding_model: str = "local-hash-v1"
    rag_timeout_seconds: float = 5.0
    rag_max_candidates: int = 1000
    rag_top_k: int = 5
    rag_min_similarity: float = 0.12
    rag_provider: str = "local"
    dashscope_api_key: SecretStr | None = None
    dashscope_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    dashscope_embedding_model: str = "text-embedding-v3"
    dashscope_generation_model: str = "qwen-plus"
    rag_generation_enabled: bool = False
    rag_provider_timeout_seconds: float = Field(default=15, gt=0, le=60)
    rag_total_timeout_seconds: float = Field(default=45, gt=0, le=120)
    rag_generation_max_tokens: int = Field(default=1200, ge=128, le=4096)
    rag_rewrite_enabled: bool = True
    rag_rerank_enabled: bool = False
    rag_self_rag_enabled: bool = False
    rag_rerank_candidates: int = Field(default=8, ge=2, le=20)
    agent_tool_transport: Literal["local", "mcp"] = "mcp"
    mcp_call_timeout_seconds: float = Field(default=15, gt=0, le=60)
    controlled_writes_enabled: bool = True
    rollout_channel: str = "api"
    rollout_staff_group: str = "default"
    model_config = SettingsConfigDict(env_file=".env", case_sensitive=False, extra="ignore")


settings = Settings()
