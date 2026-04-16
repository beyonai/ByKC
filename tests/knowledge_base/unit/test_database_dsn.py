from unittest.mock import patch

from psycopg.conninfo import conninfo_to_dict

from by_qa.config import Settings
from by_qa.knowledge_base.infrastructure.database import normalize_opengauss_dsn


def test_normalize_opengauss_dsn_converts_jdbc_schema_parameter():
    dsn = (
        "postgresql://gaussdb:Admin%23123@10.10.168.203:5432/postgres"
        "?currentSchema=byai&characterEncoding=utf8"
        "&serverTimezone=Asia/Shanghai&timeZone=GMT+8"
    )

    normalized = normalize_opengauss_dsn(dsn)

    params = conninfo_to_dict(normalized)
    assert params["host"] == "10.10.168.203"
    assert params["dbname"] == "postgres"
    assert params["user"] == "gaussdb"
    assert params["password"] == "Admin#123"
    assert params["options"] == "-c search_path=byai,public"
    assert "currentSchema" not in normalized
    assert "characterEncoding" not in normalized
    assert "serverTimezone" not in normalized
    assert "timeZone" not in normalized


def test_normalize_opengauss_dsn_preserves_existing_libpq_parameters():
    dsn = (
        "postgresql://gaussdb:secret@127.0.0.1:15432/postgres"
        "?sslmode=disable&application_name=by-qa&currentSchema=byai"
    )

    normalized = normalize_opengauss_dsn(dsn)

    params = conninfo_to_dict(normalized)
    assert params["sslmode"] == "disable"
    assert params["application_name"] == "by-qa"
    assert params["options"] == "-c search_path=byai,public"


def test_normalize_opengauss_dsn_does_not_duplicate_public_schema():
    dsn = (
        "postgresql://gaussdb:secret@127.0.0.1:15432/postgres?currentSchema=byai,public"
    )

    normalized = normalize_opengauss_dsn(dsn)

    params = conninfo_to_dict(normalized)
    assert params["options"] == "-c search_path=byai,public"


def test_connection_factory_uses_normalized_dsn():
    settings = Settings(
        KB_OPENGAUSS_DSN=(
            "postgresql://gaussdb:secret@127.0.0.1:15432/postgres"
            "?currentSchema=byai&characterEncoding=utf8"
        )
    )

    from by_qa.knowledge_base.infrastructure.database import build_connection_factory

    with patch(
        "by_qa.knowledge_base.infrastructure.database.Connection.connect"
    ) as connect:
        build_connection_factory(settings)()

    dsn = connect.call_args.args[0]
    params = conninfo_to_dict(dsn)
    assert params["options"] == "-c search_path=byai,public"
