"""Reset only this project's knowledge-base data while keeping persistent services alive."""

from __future__ import annotations

import asyncio
import re

import boto3

from by_qa.config import Settings
from by_qa.knowledge_base.infrastructure.database import build_connection_factory
from by_qa.knowledge_base.infrastructure.runtime import build_bootstrap_service

TARGET_TABLES = (
    "knowledge_base",
    "knowledge_fs_entry",
    "knowledge_fetch_cache_index",
    "knowledge_item",
    "knowledge_item_version",
    "knowledge_item_chunk",
    "knowledge_item_chunk_retrieval_mv",
)


async def _detect_embedding_configuration(connection) -> tuple[str | None, int | None]:
    """Infer embedding table name suffix and vector dimension from the current schema."""
    async with connection.cursor() as cursor:
        await cursor.execute("""
            SELECT
                t.tablename,
                format_type(a.atttypid, a.atttypmod) AS embedding_type
            FROM pg_tables t
            JOIN pg_class c
              ON c.relname = t.tablename
            JOIN pg_namespace n
              ON n.oid = c.relnamespace
             AND n.nspname = t.schemaname
            JOIN pg_attribute a
              ON a.attrelid = c.oid
             AND a.attname = 'embedding'
            WHERE t.schemaname NOT IN ('pg_catalog', 'information_schema')
              AND t.tablename LIKE 'chunk_embedding_%%'
            ORDER BY t.tablename
            LIMIT 1
            """)
        row = await cursor.fetchone()
    if not row:
        return None, None
    match = re.search(r"vector\((\d+)\)", row["embedding_type"])
    dimension = int(match.group(1)) if match else None
    model_name = row["tablename"].removeprefix("chunk_embedding_")
    return model_name or None, dimension


async def reset_database(settings: Settings) -> str:
    """Drop and recreate this project's knowledge-base tables in the active schema."""
    connection = await build_connection_factory(settings)()
    try:
        model_name = settings.embedding_model_name or None
        dimension = settings.embedding_dimension or None
        if not model_name or not dimension:
            (
                detected_model_name,
                detected_dimension,
            ) = await _detect_embedding_configuration(connection)
            model_name = model_name or detected_model_name
            dimension = dimension or detected_dimension
        async with connection.cursor() as cursor:
            await cursor.execute("SELECT current_schema() AS schema_name")
            schema_name = (await cursor.fetchone())["schema_name"]
            await cursor.execute(
                """
                SELECT schemaname, tablename
                FROM pg_tables
                WHERE schemaname NOT IN ('pg_catalog', 'information_schema')
                  AND (
                    tablename = ANY(%(table_names)s)
                    OR tablename LIKE 'chunk_embedding_%%'
                  )
                ORDER BY schemaname, tablename
                """,
                {
                    "table_names": list(TARGET_TABLES),
                },
            )
            table_rows = await cursor.fetchall()
            for row in table_rows:
                await cursor.execute(
                    f'DROP TABLE IF EXISTS "{row["schemaname"]}"."{row["tablename"]}" CASCADE'
                )
        await connection.commit()
        if model_name and dimension:
            bootstrap_settings = settings.model_copy(
                update={
                    "embedding_model_name": model_name,
                    "embedding_dimension": dimension,
                }
            )
            await (await build_bootstrap_service(bootstrap_settings)).apply(connection)
        return schema_name
    finally:
        await connection.close()


def reset_minio(settings: Settings) -> None:
    """Remove all objects from this project's MinIO buckets but keep the buckets themselves."""
    scheme = "https" if settings.kb_minio_secure else "http"
    endpoint = settings.kb_minio_endpoint.removeprefix("http://").removeprefix(
        "https://"
    )
    endpoint_url = f"{scheme}://{endpoint}"
    client = boto3.client(
        "s3",
        endpoint_url=endpoint_url,
        aws_access_key_id=settings.kb_minio_access_key,
        aws_secret_access_key=settings.kb_minio_secret_key,
    )
    bucket_names = {
        settings.kb_minio_bucket,
        settings.kb_minio_markdown_bucket,
    }
    for bucket_name in bucket_names:
        try:
            client.head_bucket(Bucket=bucket_name)
        except Exception:
            client.create_bucket(Bucket=bucket_name)
            continue
        paginator = client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket_name):
            for obj in page.get("Contents", []):
                client.delete_object(Bucket=bucket_name, Key=obj["Key"])


def main() -> None:
    """Reset knowledge-base data stores for local development and testing."""
    settings = Settings()
    schema_name = asyncio.run(reset_database(settings))
    reset_minio(settings)
    print(
        f"Knowledge-base data reset completed for schema '{schema_name}' and buckets "
        f"'{settings.kb_minio_bucket}', '{settings.kb_minio_markdown_bucket}'."
    )


if __name__ == "__main__":
    main()
