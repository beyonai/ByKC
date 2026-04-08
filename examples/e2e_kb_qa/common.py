"""Shared helpers for the packaged end-to-end example."""

from __future__ import annotations

import base64
import json
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
    allowed_error_codes: set[str] | None = None,
) -> dict[str, Any]:
    """Post one request to the JSON envelope API."""
    response = client.post(f"{base_url}{path}", json=payload)
    payload_json = response.json()

    if response.status_code == 200:
        return payload_json["data"]

    error = payload_json.get("error") or {}
    error_code = error.get("error_code")
    if allowed_error_codes and error_code in allowed_error_codes:
        return {"_allowed_error": error}

    raise ExampleError(
        "API call failed: "
        f"path={path}, status={response.status_code}, "
        f"error_code={error_code}, error_message={error.get('error_message')}"
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


def example_kb_identity() -> tuple[str, str]:
    """Return the default knowledge-base code and name."""
    return "demo", "demo知识库"


def infer_build_file_type(path: Path) -> str:
    """Infer the knowledge-build file type from the file extension."""
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
