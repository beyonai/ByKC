"""Compact session history for concurrent QA graph executions."""

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class ReservedTurn:
    """A reserved session turn and the completed history visible to it."""

    sequence: int
    previous_queries: list[str]


class SessionHistoryStore(Protocol):
    """Persistence contract for compact, ordered session turns."""

    async def reserve_turn(
        self,
        *,
        engine: str,
        session_id: str,
        message_id: str,
        query: str,
        history_limit: int,
    ) -> ReservedTurn: ...

    async def finish_turn(
        self,
        *,
        engine: str,
        session_id: str,
        message_id: str,
        succeeded: bool,
        retention_limit: int,
    ) -> None: ...


class OpenGaussSessionHistoryStore:
    """Append-only turn journal backed by the checkpointer's openGauss connection."""

    def __init__(self, connection) -> None:
        self._connection = connection

    async def reserve_turn(
        self,
        *,
        engine: str,
        session_id: str,
        message_id: str,
        query: str,
        history_limit: int,
    ) -> ReservedTurn:
        await self._connection.execute(
            """
            INSERT INTO qa_session_turns
                (engine, session_id, message_id, query, status)
            VALUES (%s, %s, %s, %s, 'running')
            ON DUPLICATE KEY UPDATE
                query = EXCLUDED.query,
                status = 'running',
                completed_at = NULL
            """,
            (engine, session_id, message_id, query),
        )
        result = await self._connection.execute(
            """
            SELECT turn_seq
            FROM qa_session_turns
            WHERE engine = %s AND session_id = %s AND message_id = %s
            """,
            (engine, session_id, message_id),
        )
        row = await result.fetchone()
        if row is None:
            raise RuntimeError("reserved QA session turn could not be read back")

        sequence = row["turn_seq"]
        result = await self._connection.execute(
            """
            SELECT query
            FROM qa_session_turns
            WHERE engine = %s
              AND session_id = %s
              AND status = 'completed'
              AND turn_seq < %s
            ORDER BY turn_seq DESC
            LIMIT %s
            """,
            (engine, session_id, sequence, history_limit),
        )
        rows = await result.fetchall()
        return ReservedTurn(
            sequence=sequence,
            previous_queries=[row["query"] for row in reversed(rows)],
        )

    async def finish_turn(
        self,
        *,
        engine: str,
        session_id: str,
        message_id: str,
        succeeded: bool,
        retention_limit: int,
    ) -> None:
        status = "completed" if succeeded else "failed"
        await self._connection.execute(
            """
            UPDATE qa_session_turns
            SET status = %s, completed_at = CURRENT_TIMESTAMP
            WHERE engine = %s AND session_id = %s AND message_id = %s
            """,
            (status, engine, session_id, message_id),
        )
        await self._connection.execute(
            """
            DELETE FROM qa_session_turns
            WHERE engine = %s
              AND session_id = %s
              AND status <> 'running'
              AND turn_seq NOT IN (
                  SELECT turn_seq
                  FROM qa_session_turns
                  WHERE engine = %s
                    AND session_id = %s
                    AND status = 'completed'
                  ORDER BY turn_seq DESC
                  LIMIT %s
              )
            """,
            (engine, session_id, engine, session_id, retention_limit),
        )


__all__ = [
    "OpenGaussSessionHistoryStore",
    "ReservedTurn",
    "SessionHistoryStore",
]
