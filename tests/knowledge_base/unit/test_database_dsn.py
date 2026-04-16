from psycopg.conninfo import conninfo_to_dict

from by_qa.config import Settings
from by_qa.knowledge_base.infrastructure.database import build_connection_factory


def test_settings_build_opengauss_dsn_from_db_parts():
    settings = Settings(
        DB_HOST="10.10.168.204",
        DB_PORT=5432,
        DB_SCHEMA="byai",
        DB_USER="gaussdb",
        DB_PASS="Admin@123",
    )

    params = conninfo_to_dict(settings.build_opengauss_dsn())

    assert params["host"] == "10.10.168.204"
    assert params["port"] == "5432"
    assert params["dbname"] == "postgres"
    assert params["user"] == "gaussdb"
    assert params["password"] == "Admin@123"
    assert params["options"] == "-c search_path=byai,public"


def test_settings_build_opengauss_dsn_omits_search_path_when_schema_blank():
    settings = Settings(
        DB_HOST="10.10.168.204",
        DB_PORT=5432,
        DB_SCHEMA="",
        DB_USER="gaussdb",
        DB_PASS="Admin@123",
    )

    params = conninfo_to_dict(settings.build_opengauss_dsn())

    assert params["host"] == "10.10.168.204"
    assert "options" not in params


async def test_connection_factory_uses_normalized_dsn(monkeypatch):
    settings = Settings(
        DB_HOST="127.0.0.1",
        DB_PORT=15432,
        DB_SCHEMA="byai",
        DB_USER="gaussdb",
        DB_PASS="secret",
    )
    calls = []

    class FakeCursor:
        async def fetchall(self):
            return []

    class FakeConnection:
        async def execute(self, statement, params=None):
            del params
            calls.append(
                statement.as_string(None)
                if hasattr(statement, "as_string")
                else statement
            )
            return FakeCursor()

        async def commit(self):
            calls.append("commit")

    async def fake_connect(dsn, **_kwargs):
        calls.append(dsn)
        return FakeConnection()

    monkeypatch.setattr(
        "by_qa.knowledge_base.infrastructure.database.AsyncConnection.connect",
        fake_connect,
    )
    await build_connection_factory(settings)()

    params = conninfo_to_dict(calls[0])
    assert params["options"] == "-c search_path=byai,public"


async def test_connection_factory_creates_configured_schema(monkeypatch):
    settings = Settings(
        DB_HOST="10.10.168.204",
        DB_PORT=5432,
        DB_SCHEMA="byai",
        DB_USER="gaussdb",
        DB_PASS="Admin@123",
    )
    calls = []

    class FakeCursor:
        async def fetchall(self):
            return []

    class FakeConnection:
        async def execute(self, statement, params=None):
            del params
            calls.append(
                statement.as_string(None)
                if hasattr(statement, "as_string")
                else statement
            )
            return FakeCursor()

        async def commit(self):
            calls.append("commit")

    async def fake_connect(dsn, **kwargs):
        del dsn, kwargs
        return FakeConnection()

    monkeypatch.setattr(
        "by_qa.knowledge_base.infrastructure.database.AsyncConnection.connect",
        fake_connect,
    )

    await build_connection_factory(settings)()

    assert calls[:2] == ['CREATE SCHEMA IF NOT EXISTS "byai"', "commit"]


async def test_connection_factory_adds_extension_schemas_to_search_path(monkeypatch):
    settings = Settings(
        DB_HOST="10.10.168.204",
        DB_PORT=5432,
        DB_SCHEMA="byai",
        DB_USER="gaussdb",
        DB_PASS="Admin@123",
    )
    calls = []

    class FakeCursor:
        def __init__(self, rows):
            self.rows = rows

        async def fetchall(self):
            return self.rows

    class FakeConnection:
        async def execute(self, statement, params=None):
            calls.append((statement, params))
            if "FROM pg_extension" in str(statement):
                return FakeCursor([{"nspname": "gaussdb"}, {"nspname": "public"}])
            return FakeCursor([])

        async def commit(self):
            calls.append(("commit", None))

    async def fake_connect(dsn, **kwargs):
        del dsn, kwargs
        return FakeConnection()

    monkeypatch.setattr(
        "by_qa.knowledge_base.infrastructure.database.AsyncConnection.connect",
        fake_connect,
    )

    await build_connection_factory(settings)()

    assert calls[-1] == (
        "SELECT set_config('search_path', %(search_path)s, false)",
        {"search_path": "byai,gaussdb,public"},
    )


async def test_connection_factory_skips_schema_creation_when_schema_blank(monkeypatch):
    settings = Settings(
        DB_HOST="10.10.168.204",
        DB_PORT=5432,
        DB_SCHEMA="",
        DB_USER="gaussdb",
        DB_PASS="Admin@123",
    )
    calls = []

    class FakeCursor:
        async def fetchall(self):
            return []

    class FakeConnection:
        async def execute(self, statement, params=None):
            del params
            calls.append(statement)
            return FakeCursor()

        async def commit(self):
            calls.append("commit")

    async def fake_connect(dsn, **kwargs):
        del dsn, kwargs
        return FakeConnection()

    monkeypatch.setattr(
        "by_qa.knowledge_base.infrastructure.database.AsyncConnection.connect",
        fake_connect,
    )

    await build_connection_factory(settings)()

    assert not any(
        getattr(call, "as_string", lambda _context: "")(None).startswith(
            "CREATE SCHEMA"
        )
        for call in calls
    )
    assert "commit" not in calls


async def test_build_connection_factory_uses_async_connection(monkeypatch):
    calls = []

    class FakeCursor:
        async def fetchall(self):
            return []

    class FakeConnection:
        async def execute(self, statement, params=None):
            del statement, params
            return FakeCursor()

        async def commit(self):
            return None

    async def fake_connect(*args, **kwargs):
        calls.append((args, kwargs))
        return FakeConnection()

    monkeypatch.setattr(
        "by_qa.knowledge_base.infrastructure.database.AsyncConnection.connect",
        fake_connect,
    )
    settings = Settings(DB_HOST="localhost", DB_USER="u", DB_PASS="p")

    connection = await build_connection_factory(settings)()

    assert connection is not None
    assert calls[0][1]["autocommit"] is False
    assert calls[0][1]["prepare_threshold"] == 0
