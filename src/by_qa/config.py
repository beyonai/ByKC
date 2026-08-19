"""Configuration for the open-source knowledge-base service."""

import ipaddress
import json
import socket
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import quote, urlencode
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dotenv import load_dotenv
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
ENV_FILE = PROJECT_ROOT / ".env"


def load_project_env_file(env_file: Path = ENV_FILE) -> None:
    """Expose project .env values to libraries that read os.environ directly."""
    load_dotenv(env_file, override=False)


def _detect_host_machine_ip() -> str:
    """Detect the LAN IPv4 address that the host would use for outbound traffic."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as outbound_socket:
            # UDP connect selects the default route without requiring a reachable peer.
            outbound_socket.connect(("192.0.2.1", 80))
            ip_address = outbound_socket.getsockname()[0]
            if _is_usable_ipv4_address(ip_address):
                return ip_address
    except OSError:
        pass

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
        if _is_usable_ipv4_address(ip_address):
            return ip_address

    return "127.0.0.1"


def _is_usable_ipv4_address(ip_address: str) -> bool:
    """Return whether an IPv4 address is suitable for LAN access."""
    try:
        address = ipaddress.ip_address(ip_address)
    except ValueError:
        return False

    if address.version != 4:
        return False

    return not (address.is_loopback or address.is_unspecified or address.is_link_local)


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
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    db_host: str = Field(default="", alias="DB_HOST")
    db_port: int = Field(default=5432, alias="DB_PORT")
    db_database: str = Field(default="postgres", alias="DB_DATABASE")
    db_schema: str = Field(default="", alias="DB_SCHEMA")
    db_user: str = Field(default="", alias="DB_USER")
    db_pass: str = Field(default="", alias="DB_PASS")
    db_timezone: str = Field(default="Asia/Shanghai", alias="DB_TIMEZONE")

    kb_minio_endpoint: str = Field(default="127.0.0.1:19000", alias="MINIO_ENDPOINT")
    kb_minio_access_key: str = Field(default="", alias="MINIO_ACCESS_KEY")
    kb_minio_secret_key: str = Field(default="", alias="MINIO_SECRET_KEY")
    kb_minio_bucket: str = Field(default="knowledge-base", alias="KB_MINIO_BUCKET")
    kb_minio_markdown_bucket: str = Field(
        default="knowledge-base-markdown", alias="KB_MINIO_MARKDOWN_BUCKET"
    )
    kb_minio_secure: bool = Field(default=False, alias="MINIO_SECURE")

    embedding_model_name: str = Field(default="", alias="EMBEDDING_MODEL_NAME")
    embedding_base_url: str = Field(default="", alias="EMBEDDING_BASE_URL")
    embedding_api_key: str = Field(default="", alias="EMBEDDING_API_KEY")
    embedding_dimension: int = Field(default=0, alias="EMBEDDING_DIMENSION")
    embedding_distance_metric: str = Field(
        default="cosine", alias="EMBEDDING_DISTANCE_METRIC"
    )
    embedding_batch_max_texts: int = Field(
        default=10, alias="EMBEDDING_BATCH_MAX_TEXTS"
    )
    dsl_max_depth: int = Field(default=3, gt=0, alias="DSL_MAX_DEPTH")
    dsl_max_leaf_count: int = Field(default=12, gt=0, alias="DSL_MAX_LEAF_COUNT")

    llm_base_url: str = Field(default="https://api.openai.com/v1", alias="LLM_BASE_URL")
    llm_api_key: str = Field(default="", alias="LLM_API_KEY")
    llm_standard_model: str = Field(default="gpt-4o", alias="LLM_STANDARD_MODEL")
    llm_standard_temp: float = Field(default=0.7, alias="LLM_STANDARD_TEMP")
    llm_standard_extra_body: Annotated[dict[str, Any], NoDecode] = Field(
        default_factory=dict,
        alias="LLM_STANDARD_EXTRA_BODY",
    )
    llm_lightweight_model: str = Field(
        default="gpt-4o-mini", alias="LLM_LIGHTWEIGHT_MODEL"
    )
    llm_lightweight_temp: float = Field(default=0.0, alias="LLM_LIGHTWEIGHT_TEMP")
    llm_lightweight_extra_body: Annotated[dict[str, Any], NoDecode] = Field(
        default_factory=dict,
        alias="LLM_LIGHTWEIGHT_EXTRA_BODY",
    )
    decomposer_max_sub_queries: int = Field(
        default=5, alias="DECOMPOSER_MAX_SUB_QUERIES"
    )
    llm_standard_max_model_len: int | None = Field(
        default=None, alias="LLM_STANDARD_MAX_MODEL_LEN"
    )
    llm_lightweight_max_model_len: int | None = Field(
        default=None, alias="LLM_LIGHTWEIGHT_MAX_MODEL_LEN"
    )
    embedding_max_model_len: int | None = Field(
        default=None, alias="EMBEDDING_MAX_MODEL_LEN"
    )
    instant_search_max_context_ratio: float = Field(
        default=0.8, alias="INSTANT_SEARCH_MAX_CONTEXT_RATIO"
    )
    instant_search_reserved_tokens: int = Field(
        default=2000, alias="INSTANT_SEARCH_RESERVED_TOKENS"
    )
    instant_search_min_sentence_tokens: int = Field(
        default=50, alias="INSTANT_SEARCH_MIN_SENTENCE_TOKENS"
    )
    kb_fetch_cache_ttl_seconds: int = Field(
        default=24 * 60 * 60, alias="KB_FETCH_CACHE_TTL_SECONDS"
    )
    kb_fetch_cache_cleanup_interval_seconds: int = Field(
        default=10 * 60, alias="KB_FETCH_CACHE_CLEANUP_INTERVAL_SECONDS"
    )
    kb_update_timeline_llm_timeout_seconds: float = Field(
        default=60,
        gt=0,
        alias="KB_UPDATE_TIMELINE_LLM_TIMEOUT_SECONDS",
    )
    knowledge_entity_worker_enabled: bool = Field(
        default=True,
        alias="KNOWLEDGE_ENTITY_WORKER_ENABLED",
    )
    knowledge_entity_worker_id: str = Field(
        default="",
        alias="KNOWLEDGE_ENTITY_WORKER_ID",
    )
    knowledge_entity_worker_concurrency: int = Field(
        default=4,
        ge=1,
        alias="KNOWLEDGE_ENTITY_WORKER_CONCURRENCY",
    )
    knowledge_entity_worker_poll_seconds: float = Field(
        default=3.0,
        gt=0,
        alias="KNOWLEDGE_ENTITY_WORKER_POLL_SECONDS",
    )
    knowledge_entity_task_timeout_seconds: float = Field(
        default=1200.0,
        gt=0,
        alias="KNOWLEDGE_ENTITY_TASK_TIMEOUT_SECONDS",
    )
    knowledge_entity_lease_seconds: int = Field(
        default=180,
        ge=2,
        alias="KNOWLEDGE_ENTITY_LEASE_SECONDS",
    )
    knowledge_entity_heartbeat_seconds: float = Field(
        default=30.0,
        gt=0,
        alias="KNOWLEDGE_ENTITY_HEARTBEAT_SECONDS",
    )
    knowledge_entity_reaper_seconds: float = Field(
        default=30.0,
        gt=0,
        alias="KNOWLEDGE_ENTITY_REAPER_SECONDS",
    )
    knowledge_entity_worker_status_log_seconds: float = Field(
        default=60.0,
        gt=0,
        alias="KNOWLEDGE_ENTITY_WORKER_STATUS_LOG_SECONDS",
    )
    knowledge_entity_shutdown_grace_seconds: float = Field(
        default=60.0,
        ge=0,
        alias="KNOWLEDGE_ENTITY_SHUTDOWN_GRACE_SECONDS",
    )
    entity_embedding_index_enabled: bool = Field(
        default=True,
        alias="ENTITY_EMBEDDING_INDEX_ENABLED",
    )
    entity_synonym_exact_alias_enabled: bool = Field(
        default=True,
        alias="ENTITY_SYNONYM_EXACT_ALIAS_ENABLED",
    )
    entity_synonym_adjudication_enabled: bool = Field(
        default=True,
        alias="ENTITY_SYNONYM_ADJUDICATION_ENABLED",
    )
    entity_synonym_top_k: int = Field(
        default=3,
        ge=1,
        le=10,
        alias="ENTITY_SYNONYM_TOP_K",
    )

    @field_validator("log_level", mode="before")
    @classmethod
    def _normalize_log_level(cls, value: Any) -> str:
        """Normalize and validate the application logging threshold."""
        if not isinstance(value, str):
            raise ValueError("LOG_LEVEL must be a string")
        normalized = value.strip().upper()
        allowed = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        if normalized not in allowed:
            raise ValueError(
                "LOG_LEVEL must be one of DEBUG, INFO, WARNING, ERROR, CRITICAL"
            )
        return normalized

    @field_validator("host_machine", mode="before")
    @classmethod
    def _normalize_host_machine(cls, value: str | None) -> str:
        """Treat blank HOST_MACHINE as unset and fall back to auto-detection."""
        if value is None:
            return _detect_host_machine_ip()
        if isinstance(value, str) and not value.strip():
            return _detect_host_machine_ip()
        return value

    @field_validator("db_timezone")
    @classmethod
    def validate_db_timezone(cls, value: str) -> str:
        """Require an IANA timezone accepted before building DB options."""
        timezone_name = value.strip()
        try:
            ZoneInfo(timezone_name)
        except (ValueError, ZoneInfoNotFoundError) as exc:
            raise ValueError(f"invalid DB_TIMEZONE: {value}") from exc
        return timezone_name

    @field_validator("embedding_batch_max_texts")
    @classmethod
    def _validate_embedding_batch_max_texts(cls, value: int) -> int:
        """Allow positive batch sizes or -1 to disable batching."""
        if value == -1 or value > 0:
            return value
        raise ValueError("EMBEDDING_BATCH_MAX_TEXTS must be greater than 0 or -1")

    @field_validator("knowledge_entity_heartbeat_seconds")
    @classmethod
    def _validate_knowledge_entity_heartbeat_seconds(cls, value: float, info) -> float:
        lease_seconds = info.data.get("knowledge_entity_lease_seconds", 180)
        if value >= lease_seconds:
            raise ValueError(
                "KNOWLEDGE_ENTITY_HEARTBEAT_SECONDS must be less than "
                "KNOWLEDGE_ENTITY_LEASE_SECONDS"
            )
        return value

    @field_validator(
        "llm_standard_extra_body", "llm_lightweight_extra_body", mode="before"
    )
    @classmethod
    def _normalize_llm_extra_body(cls, value: Any) -> dict[str, Any]:
        """Accept JSON strings or dicts for provider-specific request payload fields."""
        if value is None or value == "":
            return {}
        if isinstance(value, dict):
            return value
        if isinstance(value, str):
            parsed = json.loads(value)
            if not isinstance(parsed, dict):
                raise ValueError("LLM extra_body must decode to a JSON object")
            return parsed
        raise ValueError("LLM extra_body must be a JSON object or JSON string")

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

    @property
    def resolved_kb_opengauss_dsn(self) -> str:
        """Get the configured KB openGauss DSN."""
        return self.build_opengauss_dsn()

    @property
    def resolved_checkpointer_opengauss_dsn(self) -> str:
        """Get the configured checkpointer openGauss DSN."""
        return self.build_opengauss_dsn()

    @property
    def checkpointer_backend(self) -> str:
        """Get the default checkpointer backend."""
        return "opengauss"

    def build_opengauss_dsn(self) -> str:
        """Build an openGauss DSN from shared DB_* settings."""
        if not self.db_host or not self.db_user or not self.db_pass:
            return ""

        user = quote(self.db_user, safe="")
        password = quote(self.db_pass, safe="")
        database = quote(self.db_database.strip() or "postgres", safe="")
        dsn = f"postgresql://{user}:{password}@{self.db_host}:{self.db_port}/{database}"
        options = [f"-c timezone={self.db_timezone}"]
        schema = self.db_schema.strip()
        if schema:
            search_path = schema
            if "public" not in [part.strip().lower() for part in schema.split(",")]:
                search_path = f"{schema},public"
            options.insert(0, f"-c search_path={search_path}")
        query = urlencode(
            {"options": " ".join(options)},
            quote_via=quote,
        )
        dsn = f"{dsn}?{query}"
        return dsn

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
