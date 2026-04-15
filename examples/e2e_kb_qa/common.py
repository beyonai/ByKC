"""Shared helpers for the packaged end-to-end example."""

from __future__ import annotations

import base64
import json
import mimetypes
import os
import time
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv

DEFAULT_FILE_VERSION = "v1"
DEFAULT_SOURCE_CODE = "demo"
PROJECT_ROOT = Path(__file__).resolve().parents[2]
EXAMPLE_ROOT = Path(__file__).resolve().parent
ENV_FILE = EXAMPLE_ROOT / ".env"
EXAMPLE_STATE_FILE = "example_kb_state.json"


class ExampleError(RuntimeError):
    """Raised when the packaged example cannot proceed."""


load_dotenv(ENV_FILE)


def runtime_dir(path: str | None = None) -> Path:
    """Resolve the working directory used by the example scripts."""
    candidate = path or str(EXAMPLE_ROOT / ".runtime")
    resolved = Path(candidate).expanduser()
    if not resolved.is_absolute():
        resolved = (PROJECT_ROOT / resolved).resolve()
    else:
        resolved = resolved.resolve()
    resolved.mkdir(parents=True, exist_ok=True)
    return resolved


def require_environment(variable_names: list[str]) -> dict[str, str]:
    """Ensure the example has the environment variables it needs."""
    values: dict[str, str] = {}
    missing: list[str] = []
    for name in variable_names:
        value = os.getenv(name, "").strip()
        if not value:
            missing.append(name)
            continue
        values[name] = value
    if missing:
        missing_text = ", ".join(missing)
        raise ExampleError(f"missing required environment variables: {missing_text}")
    return values


def normalized_base_url(base_url: str | None = None) -> str:
    """Return the service base URL without a trailing slash."""
    candidate = base_url
    if not candidate:
        host = os.getenv("HOST", "").strip()
        port = os.getenv("PORT", "").strip()
        if host and port:
            candidate = f"http://{host}:{port}"
    if not candidate:
        raise ExampleError(
            "missing base URL: use --base-url or configure HOST and PORT"
        )
    return candidate.rstrip("/")


def wait_for_health(base_url: str, *, timeout_seconds: float = 120.0) -> dict[str, Any]:
    """Poll the service health endpoint until the example API is ready."""
    deadline = time.time() + timeout_seconds
    last_error: str | None = None
    with httpx.Client(timeout=5.0) as client:
        while time.time() < deadline:
            try:
                response = client.get(f"{base_url}/health")
                response.raise_for_status()
                return response.json()
            except Exception as exc:  # pragma: no cover - exercised in manual runs
                last_error = str(exc)
                time.sleep(2)
    raise ExampleError(
        f"service did not become healthy within {timeout_seconds:.0f}s: {last_error}"
    )


def post_api(
    client: httpx.Client,
    *,
    base_url: str,
    path: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Post one JSON request to the documented resultCode/resultObject API."""
    response = client.post(f"{base_url}{path}", json=payload)
    payload_json = response.json()

    if response.status_code == 200 and payload_json.get("resultCode") == "0":
        result_object = payload_json.get("resultObject")
        return result_object if isinstance(result_object, dict) else {}

    raise ExampleError(
        "API call failed: "
        f"path={path}, status={response.status_code}, "
        f"result_code={payload_json.get('resultCode')}, "
        f"result_msg={payload_json.get('resultMsg')}"
    )


def post_multipart_api(
    client: httpx.Client,
    *,
    base_url: str,
    path: str,
    data: dict[str, str],
    file_field_name: str,
    file_path: Path,
) -> dict[str, Any]:
    """Post one multipart request to the documented resultCode/resultObject API."""
    content_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
    with file_path.open("rb") as file_handle:
        response = client.post(
            f"{base_url}{path}",
            data=data,
            files={
                file_field_name: (
                    file_path.name,
                    file_handle,
                    content_type,
                )
            },
        )
    payload_json = response.json()

    if response.status_code == 200 and payload_json.get("resultCode") == "0":
        result_object = payload_json.get("resultObject")
        return result_object if isinstance(result_object, dict) else {}

    raise ExampleError(
        "API call failed: "
        f"path={path}, status={response.status_code}, "
        f"result_code={payload_json.get('resultCode')}, "
        f"result_msg={payload_json.get('resultMsg')}"
    )


def pretty_print(title: str, payload: Any) -> None:
    """Print a formatted section in the terminal."""
    print(f"\n=== {title} ===")
    if isinstance(payload, str):
        print(payload)
        return
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def read_file_base64(path: Path) -> str:
    """Read one file and return a base64-encoded payload."""
    return base64.b64encode(path.read_bytes()).decode("ascii")


def build_example_kb_name() -> str:
    """Return a unique example knowledge-base name for one run."""
    return f"demo知识库-{int(time.time())}"


def save_example_kb_state(
    root: Path,
    *,
    kb_code: str,
    kb_name: str,
) -> Path:
    """Persist the created example knowledge-base identity for later scripts."""
    state_path = root / EXAMPLE_STATE_FILE
    state_path.write_text(
        json.dumps({"kb_code": kb_code, "kb_name": kb_name}, ensure_ascii=False),
        encoding="utf-8",
    )
    return state_path


def load_example_kb_state(root: Path) -> tuple[str, str]:
    """Load the created example knowledge-base identity from the runtime dir."""
    state_path = root / EXAMPLE_STATE_FILE
    if not state_path.exists():
        raise ExampleError(
            "example knowledge-base state not found. Run run_kb_flow.py first."
        )
    payload = json.loads(state_path.read_text(encoding="utf-8"))
    kb_code = str(payload.get("kb_code", "")).strip()
    kb_name = str(payload.get("kb_name", "")).strip()
    if not kb_code or not kb_name:
        raise ExampleError(f"example knowledge-base state is invalid: {state_path}")
    return kb_code, kb_name


def infer_build_file_type(path: Path) -> str:
    """Infer the fileToMarkdownIndex file type from the file extension."""
    suffix = path.suffix.lower()
    supported = {
        ".pdf": "pdf",
        ".docx": "docx",
        ".pptx": "pptx",
        ".xlsx": "xlsx",
    }
    file_type = supported.get(suffix)
    if file_type is None:
        raise ExampleError(
            f"unsupported input file type: {path.name}. Supported types: pdf, docx, pptx, xlsx"
        )
    return file_type


def resolve_input_directory(path: str) -> Path:
    """Resolve the user-provided import directory."""
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        candidate = (EXAMPLE_ROOT / candidate).resolve()
    else:
        candidate = candidate.resolve()
    if not candidate.is_dir():
        raise ExampleError(f"input directory not found: {candidate}")
    return candidate


def list_supported_input_files(directory: Path) -> list[Path]:
    """Return supported files from one directory sorted by file name."""
    supported_extensions = {".pdf", ".docx", ".pptx", ".xlsx"}
    files = [
        candidate
        for candidate in sorted(directory.iterdir())
        if candidate.is_file() and candidate.suffix.lower() in supported_extensions
    ]
    if not files:
        raise ExampleError(
            f"no supported files found under {directory}. Add pdf/docx/pptx/xlsx files first."
        )
    return files
