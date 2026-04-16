"""Tests for the KB reset shell wrapper."""

import os
import shutil
import stat
import subprocess
from pathlib import Path


def _prepare_reset_script(tmp_path: Path) -> Path:
    project_root = tmp_path
    scripts_dir = project_root / "scripts"
    scripts_dir.mkdir()

    source_script = Path("scripts/reset_kb_stack.sh")
    target_script = scripts_dir / "reset_kb_stack.sh"
    shutil.copy(source_script, target_script)
    target_script.chmod(target_script.stat().st_mode | stat.S_IXUSR)

    venv_bin = project_root / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    fake_python = venv_bin / "python"
    fake_python.write_text(
        "#!/bin/bash\n"
        "printf '%s\\n' \"DB_HOST=$DB_HOST\"\n"
        "printf '%s\\n' \"DB_PORT=$DB_PORT\"\n"
        "printf '%s\\n' \"DB_SCHEMA=$DB_SCHEMA\"\n"
        "printf '%s\\n' \"DB_USER=$DB_USER\"\n"
        "printf '%s\\n' \"DB_PASS=$DB_PASS\"\n"
        "printf '%s\\n' \"MINIO_ENDPOINT=$MINIO_ENDPOINT\"\n"
        "printf '%s\\n' \"MINIO_ACCESS_KEY=$MINIO_ACCESS_KEY\"\n"
        "printf '%s\\n' \"MINIO_SECRET_KEY=$MINIO_SECRET_KEY\"\n"
        "printf '%s\\n' \"KB_MINIO_BUCKET=$KB_MINIO_BUCKET\"\n"
        "printf '%s\\n' \"KB_MINIO_MARKDOWN_BUCKET=$KB_MINIO_MARKDOWN_BUCKET\"\n",
        encoding="utf-8",
    )
    fake_python.chmod(fake_python.stat().st_mode | stat.S_IXUSR)
    return target_script


def test_reset_kb_stack_loads_missing_values_from_root_env(tmp_path):
    """Reset wrapper should use .env values when env vars are absent."""
    target_script = _prepare_reset_script(tmp_path)
    (tmp_path / ".env").write_text(
        "\n".join(
            [
                "DB_HOST=127.0.0.1",
                "DB_PORT=15432",
                "DB_SCHEMA=byai",
                "DB_USER=gaussdb",
                "DB_PASS=secret",
                "MINIO_ENDPOINT=127.0.0.1:19000",
                "MINIO_ACCESS_KEY=minioadmin",
                "MINIO_SECRET_KEY=minioadmin",
                "KB_MINIO_BUCKET=knowledge-base",
                "KB_MINIO_MARKDOWN_BUCKET=knowledge-base-markdown",
                "MINIO_SECURE=false",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        [str(target_script)],
        cwd=tmp_path,
        env={"PATH": os.environ["PATH"]},
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "DB_HOST=127.0.0.1" in result.stdout
    assert "DB_PORT=15432" in result.stdout
    assert "DB_SCHEMA=byai" in result.stdout
    assert "DB_USER=gaussdb" in result.stdout
    assert "DB_PASS=secret" in result.stdout
    assert "MINIO_ENDPOINT=127.0.0.1:19000" in result.stdout
    assert "MINIO_ACCESS_KEY=minioadmin" in result.stdout
    assert "MINIO_SECRET_KEY=minioadmin" in result.stdout
    assert "KB_MINIO_BUCKET=knowledge-base" in result.stdout
    assert "KB_MINIO_MARKDOWN_BUCKET=knowledge-base-markdown" in result.stdout


def test_reset_kb_stack_prefers_environment_over_root_env(tmp_path):
    """Reset wrapper should prefer exported env vars over .env values."""
    target_script = _prepare_reset_script(tmp_path)
    (tmp_path / ".env").write_text(
        "\n".join(
            [
                "DB_HOST=dotenv-host",
                "DB_PORT=15432",
                "DB_SCHEMA=dotenv-schema",
                "DB_USER=dotenv-user",
                "DB_PASS=dotenv-pass",
                "MINIO_ENDPOINT=dotenv-endpoint",
                "MINIO_ACCESS_KEY=dotenv-access",
                "MINIO_SECRET_KEY=dotenv-secret",
                "KB_MINIO_BUCKET=dotenv-bucket",
                "KB_MINIO_MARKDOWN_BUCKET=dotenv-markdown-bucket",
                "MINIO_SECURE=false",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    env = {"PATH": os.environ["PATH"]}
    env.update(
        {
            "DB_HOST": "env-host",
            "DB_PORT": "5432",
            "DB_SCHEMA": "env-schema",
            "DB_USER": "env-user",
            "DB_PASS": "env-pass",
            "MINIO_ENDPOINT": "env-endpoint",
            "MINIO_ACCESS_KEY": "env-access",
            "MINIO_SECRET_KEY": "env-secret",
            "KB_MINIO_BUCKET": "env-bucket",
            "KB_MINIO_MARKDOWN_BUCKET": "env-markdown-bucket",
            "MINIO_SECURE": "true",
        }
    )

    result = subprocess.run(
        [str(target_script)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "DB_HOST=env-host" in result.stdout
    assert "DB_PORT=5432" in result.stdout
    assert "DB_SCHEMA=env-schema" in result.stdout
    assert "DB_USER=env-user" in result.stdout
    assert "DB_PASS=env-pass" in result.stdout
    assert "MINIO_ENDPOINT=env-endpoint" in result.stdout
    assert "MINIO_ACCESS_KEY=env-access" in result.stdout
    assert "MINIO_SECRET_KEY=env-secret" in result.stdout
    assert "KB_MINIO_BUCKET=env-bucket" in result.stdout
    assert "KB_MINIO_MARKDOWN_BUCKET=env-markdown-bucket" in result.stdout


def test_reset_kb_stack_fails_when_required_values_are_missing(tmp_path):
    """Reset wrapper should fail fast when neither env nor .env provides required config."""
    target_script = _prepare_reset_script(tmp_path)

    result = subprocess.run(
        [str(target_script)],
        cwd=tmp_path,
        env={"PATH": os.environ["PATH"]},
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "Missing required config: DB_HOST" in result.stderr
