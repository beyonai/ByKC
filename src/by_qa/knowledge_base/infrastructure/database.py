"""Database infrastructure helpers for knowledge base ingestion."""

from typing import Callable

from psycopg import Connection
from psycopg.rows import dict_row

from by_qa.config import Settings


def build_connection_factory(settings: Settings) -> Callable[[], Connection]:
    """Build a sync psycopg connection factory for knowledge base persistence."""

    def connect() -> Connection:
        return Connection.connect(
            settings.kb_opengauss_dsn,
            autocommit=False,
            prepare_threshold=0,
            row_factory=dict_row,
        )

    return connect
