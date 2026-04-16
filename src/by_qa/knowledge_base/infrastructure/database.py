"""Database infrastructure helpers for knowledge base ingestion."""

from typing import Awaitable, Callable
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

from psycopg import AsyncConnection
from psycopg.rows import dict_row

from by_qa.config import Settings

_IGNORED_JDBC_QUERY_PARAMS = {
    "characterencoding",
    "servertimezone",
    "timezone",
}


def normalize_opengauss_dsn(dsn: str) -> str:
    """Normalize common JDBC-style DSN parameters for psycopg/libpq."""

    split = urlsplit(dsn)
    if not split.query:
        return dsn

    schema: str | None = None
    query_params: list[tuple[str, str]] = []
    existing_options: str | None = None

    for key, value in parse_qsl(split.query, keep_blank_values=True):
        normalized_key = key.lower()
        if normalized_key == "currentschema":
            schema = value
            continue
        if normalized_key in _IGNORED_JDBC_QUERY_PARAMS:
            continue
        if normalized_key == "options":
            existing_options = value
            continue
        query_params.append((key, value))

    if schema:
        schema_option = f"-c search_path={_build_search_path(schema)}"
        options = (
            f"{existing_options} {schema_option}" if existing_options else schema_option
        )
        query_params.append(("options", options))
    elif existing_options is not None:
        query_params.append(("options", existing_options))

    return urlunsplit(
        (
            split.scheme,
            split.netloc,
            split.path,
            urlencode(query_params, quote_via=quote),
            split.fragment,
        )
    )


def _build_search_path(schema: str) -> str:
    schemas = [part.strip() for part in schema.split(",") if part.strip()]
    if not any(part.lower() == "public" for part in schemas):
        schemas.append("public")
    return ",".join(schemas)


def build_connection_factory(
    settings: Settings,
) -> Callable[[], Awaitable[AsyncConnection]]:
    """Build an async psycopg connection factory for knowledge base persistence."""

    async def connect() -> AsyncConnection:
        return await AsyncConnection.connect(
            normalize_opengauss_dsn(settings.kb_opengauss_dsn),
            autocommit=False,
            prepare_threshold=0,
            row_factory=dict_row,
        )

    return connect
