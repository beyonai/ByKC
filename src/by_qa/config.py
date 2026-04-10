"""Configuration for the open-source knowledge-base service."""

import socket
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
ENV_FILE = PROJECT_ROOT / ".env"


def _detect_host_machine_ip() -> str:
    """Detect a non-loopback local IPv4 address without depending on internet access."""
    try:
        addresses = socket.getaddrinfo(
            socket.gethostname(),
            None,
            family=socket.AF_INET,
            type=socket.SOCK_DGRAM,
        )
    except OSError:
        return "127.0.0.1"

    for _, _, _, _, sockaddr in addresses:
        ip_address = sockaddr[0]
        if not ip_address.startswith("127."):
            return ip_address

    return "127.0.0.1"


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    host: str = Field(default="0.0.0.0", alias="HOST")
    port: int = Field(default=8000, alias="PORT")
    service_name: str = Field(default="by-qa-manager", alias="SERVICE_NAME")
    host_machine: str = Field(
        default_factory=lambda: _detect_host_machine_ip(),
        alias="HOST_MACHINE",
    )
    redis_host: str = Field(default="localhost", alias="REDIS_HOST")
    redis_port: int = Field(default=6379, alias="REDIS_PORT")
    redis_username: str = Field(default="", alias="REDIS_USERNAME")
    redis_password: str = Field(default="", alias="REDIS_PASSWORD")
    redis_database: int = Field(default=0, alias="REDIS_DATABASE")

    agent_data_path: Path = Field(default=Path("agent_data"), alias="AGENT_DATA_PATH")

    kb_opengauss_dsn: str = Field(default="", alias="KB_OPENGAUSS_DSN")
    kb_minio_endpoint: str = Field(default="127.0.0.1:19000", alias="KB_MINIO_ENDPOINT")
    kb_minio_access_key: str = Field(default="", alias="KB_MINIO_ACCESS_KEY")
    kb_minio_secret_key: str = Field(default="", alias="KB_MINIO_SECRET_KEY")
    kb_minio_bucket: str = Field(default="knowledge-base", alias="KB_MINIO_BUCKET")
    kb_minio_markdown_bucket: str = Field(
        default="knowledge-base-markdown", alias="KB_MINIO_MARKDOWN_BUCKET"
    )
    kb_minio_secure: bool = Field(default=False, alias="KB_MINIO_SECURE")

    embedding_model_name: str = Field(default="", alias="EMBEDDING_MODEL_NAME")
    embedding_base_url: str = Field(default="", alias="EMBEDDING_BASE_URL")
    embedding_api_key: str = Field(default="", alias="EMBEDDING_API_KEY")
    embedding_dimension: int = Field(default=0, alias="EMBEDDING_DIMENSION")
    embedding_distance_metric: str = Field(
        default="cosine", alias="EMBEDDING_DISTANCE_METRIC"
    )

    llm_base_url: str = Field(default="https://api.openai.com/v1", alias="LLM_BASE_URL")
    llm_api_key: str = Field(default="", alias="LLM_API_KEY")
    classifier_model: str = Field(default="gpt-4o-mini", alias="CLASSIFIER_MODEL")
    classifier_temp: float = Field(default=0.0, alias="CLASSIFIER_TEMP")
    retrieval_model: str = Field(default="gpt-4o", alias="RETRIEVAL_MODEL")
    retrieval_temp: float = Field(default=0.0, alias="RETRIEVAL_TEMP")
    generator_model: str = Field(default="gpt-4o", alias="GENERATOR_MODEL")
    generator_temp: float = Field(default=0.7, alias="GENERATOR_TEMP")
    quality_model: str = Field(default="gpt-4o", alias="QUALITY_MODEL")
    quality_temp: float = Field(default=0.0, alias="QUALITY_TEMP")
    decomposer_model: str = Field(default="gpt-4o-mini", alias="DECOMPOSER_MODEL")
    decomposer_temp: float = Field(default=0.0, alias="DECOMPOSER_TEMP")
    decomposer_max_sub_queries: int = Field(
        default=5, alias="DECOMPOSER_MAX_SUB_QUERIES"
    )
    aggregator_model: str = Field(default="gpt-4o", alias="AGGREGATOR_MODEL")
    aggregator_temp: float = Field(default=0.7, alias="AGGREGATOR_TEMP")
    context_max_tokens: int = Field(default=128000, alias="CONTEXT_MAX_TOKENS")
    instant_search_max_context_ratio: float = Field(
        default=0.8, alias="INSTANT_SEARCH_MAX_CONTEXT_RATIO"
    )
    instant_search_reserved_tokens: int = Field(
        default=2000, alias="INSTANT_SEARCH_RESERVED_TOKENS"
    )
    instant_search_min_sentence_tokens: int = Field(
        default=50, alias="INSTANT_SEARCH_MIN_SENTENCE_TOKENS"
    )
    checkpointer_backend: str = Field(default="sqlite", alias="CHECKPOINTER_BACKEND")
    checkpointer_sqlite_path: str = Field(
        default="./data/checkpoints.db", alias="CHECKPOINTER_SQLITE_PATH"
    )
    checkpointer_opengauss_dsn: str = Field(
        default="", alias="CHECKPOINTER_OPENGAUSS_DSN"
    )
    kb_fetch_cache_ttl_seconds: int = Field(
        default=24 * 60 * 60, alias="KB_FETCH_CACHE_TTL_SECONDS"
    )
    kb_fetch_cache_cleanup_interval_seconds: int = Field(
        default=10 * 60, alias="KB_FETCH_CACHE_CLEANUP_INTERVAL_SECONDS"
    )

    @property
    def logs_path(self) -> Path:
        """Get logs storage path."""
        return self.agent_data_path / "logs"

    @property
    def sessions_path(self) -> Path:
        """Get sessions storage path."""
        return self.agent_data_path / "sessions"

    @property
    def retrieval_results_path(self) -> Path:
        """Get retrieval results storage path."""
        return self.agent_data_path / "retrieval_results"

    @property
    def kb_cache_path(self) -> Path:
        """Get fetched-file cache path."""
        return self.agent_data_path / "kb_cache"

    def ensure_directories(self) -> None:
        """Ensure all required directories exist."""
        self.agent_data_path.mkdir(parents=True, exist_ok=True)
        self.logs_path.mkdir(parents=True, exist_ok=True)
        self.sessions_path.mkdir(parents=True, exist_ok=True)
        self.retrieval_results_path.mkdir(parents=True, exist_ok=True)
        self.kb_cache_path.mkdir(parents=True, exist_ok=True)


@lru_cache()
def get_settings() -> Settings:
    """Get cached settings instance."""
    settings = Settings()
    settings.ensure_directories()
    return settings
