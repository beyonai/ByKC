"""Tests for the container entrypoint script."""

import os
import shutil
import stat
import subprocess
from pathlib import Path


def test_entrypoint_loads_root_env_file(tmp_path):
    """Entrypoint should export variables from the project-root .env file."""
    project_root = tmp_path
    scripts_dir = project_root / "scripts"
    scripts_dir.mkdir()

    source_script = Path("scripts/entrypoint.sh")
    target_script = scripts_dir / "entrypoint.sh"
    shutil.copy(source_script, target_script)
    target_script.chmod(target_script.stat().st_mode | stat.S_IXUSR)

    (project_root / ".env").write_text(
        "HOST=127.0.0.1\nPORT=9123\nENTRYPOINT_TEST_VALUE=loaded-from-dotenv\n",
        encoding="utf-8",
    )

    fake_bin = project_root / "bin"
    fake_bin.mkdir()
    fake_uvicorn = fake_bin / "uvicorn"
    fake_uvicorn.write_text(
        "#!/bin/bash\n"
        "printf '%s\\n' \"HOST=$HOST\"\n"
        "printf '%s\\n' \"PORT=$PORT\"\n"
        "printf '%s\\n' \"ENTRYPOINT_TEST_VALUE=$ENTRYPOINT_TEST_VALUE\"\n"
        "printf '%s\\n' \"$*\"\n",
        encoding="utf-8",
    )
    fake_uvicorn.chmod(fake_uvicorn.stat().st_mode | stat.S_IXUSR)

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"

    result = subprocess.run(
        [str(target_script)],
        cwd=project_root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "HOST=127.0.0.1" in result.stdout
    assert "PORT=9123" in result.stdout
    assert "ENTRYPOINT_TEST_VALUE=loaded-from-dotenv" in result.stdout
    assert "by_qa.main:app" in result.stdout
