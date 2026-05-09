"""
Ingest FRAMES wiki pages into the knowledge base.

Uploads files from datasets/FRAMES/frames_wiki_pages/wiki_pages/ to the KB,
triggers build, and polls for completion. Supports progress persistence,
retry, concurrency, and status sync.

Usage (programmatic):
  from eval.datasets.frames.ingest import main
  main(base_url="http://localhost:8000", kn_name="FRAMES Wiki", concurrency=8)
  main(retry_failed=True)
  main(retry_file="Jimin_824335.txt")
"""

from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests
from tqdm import tqdm

_HERE = Path(__file__).parent
REPO_ROOT = _HERE.parent.parent.parent

INGEST_STATE_FILE = REPO_ROOT / "datasets/FRAMES/.ingest_state.json"
WIKI_PAGES_DIR = REPO_ROOT / "datasets" / "FRAMES" / "frames_wiki_pages" / "wiki_pages"

POLL_INTERVAL = 5
POLL_TIMEOUT = 600
UPLOAD_DIR = "/wiki_pages"

_progress_lock = threading.Lock()


def load_state() -> dict:
    if INGEST_STATE_FILE.exists():
        return json.loads(INGEST_STATE_FILE.read_text(encoding="utf-8"))
    return {"kb_code": None, "kb_name": None, "files": {}}


def save_state(state: dict):
    with _progress_lock:
        INGEST_STATE_FILE.write_text(
            json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
        )


def update_file_status(state: dict, filename: str, status: str):
    with _progress_lock:
        state["files"][filename] = {"status": status}
    save_state(state)


def api_post(base_url: str, path: str, **kwargs) -> dict:
    resp = requests.post(f"{base_url}{path}", timeout=60, **kwargs)
    resp.raise_for_status()
    return resp.json()


def ensure_knowledge_base(base_url: str, kn_name: str, state: dict) -> str:
    """Create or reuse a knowledge base, returning its kb_code."""
    if state.get("kb_code"):
        return state["kb_code"]

    result = api_post(
        base_url,
        "/api/v1/knowledgeBases/create",
        json={
            "knName": kn_name,
            "knDescription": "FRAMES benchmark wiki pages",
        },
    )
    if result["resultCode"] == "0":
        kb_code = result["resultObject"]["knCode"]
    elif "already exists" in result.get("resultMsg", ""):
        raise SystemExit(
            f"Knowledge base '{kn_name}' already exists but knCode is not recorded. "
            f"Please manually set kb_code in {INGEST_STATE_FILE} and retry."
        )
    else:
        raise SystemExit(f"Failed to create knowledge base: {result['resultMsg']}")

    state["kb_code"] = kb_code
    state["kb_name"] = kn_name
    save_state(state)
    return kb_code


def ensure_directory(base_url: str, kn_code: str):
    """Ensure the upload directory exists."""
    result = api_post(
        base_url,
        "/api/v1/directories/create",
        json={
            "knCode": kn_code,
            "directoryPath": UPLOAD_DIR,
            "directoryDescription": "FRAMES wiki pages",
        },
    )
    if result["resultCode"] == "0":
        print(f"Directory created: {UPLOAD_DIR}")
    elif "already exists" in result.get("resultMsg", ""):
        pass
    else:
        raise SystemExit(f"Failed to create directory: {result['resultMsg']}")


def upload_file(base_url: str, kn_code: str, file_path: Path) -> bool:
    """Upload a single file. Returns True on success."""
    target_path = f"{UPLOAD_DIR}/{file_path.name}"
    with open(file_path, "rb") as f:
        result = api_post(
            base_url,
            "/api/v1/knowledgeItems/import",
            data={
                "knCode": kn_code,
                "filePath": target_path,
            },
            files={"fileContent": (file_path.name, f)},
        )

    if result["resultCode"] == "0":
        return True
    if "already exists" in result.get("resultMsg", ""):
        return True
    print(f"  Upload failed [{file_path.name}]: {result['resultMsg']}")
    return False


def trigger_build(base_url: str, kn_code: str, filename: str) -> bool:
    """Trigger knowledge build for a file. Returns True if accepted."""
    target_path = f"{UPLOAD_DIR}/{filename}"
    result = api_post(
        base_url,
        "/api/v1/fileToMarkdownIndex",
        json={
            "knCode": kn_code,
            "filePath": target_path,
        },
    )
    if result["resultCode"] == "0":
        return True
    if "already exists" in result.get("resultMsg", ""):
        return True
    print(f"  Build trigger failed [{filename}]: {result['resultMsg']}")
    return False


def trigger_and_poll(
    base_url: str,
    kn_code: str,
    filename: str,
    state: dict,
    build_timeout: int = POLL_TIMEOUT,
) -> str:
    """Trigger build and poll until completion or timeout.

    Trigger and poll run in the same task so concurrency control is
    uniform — each file holds one slot in the thread pool for its
    entire build lifecycle.

    Timeout is logged and treated as 'failed'.
    """
    # Trigger
    if not trigger_build(base_url, kn_code, filename):
        update_file_status(state, filename, "build_trigger_failed")
        return "build_trigger_failed"

    # Poll
    target_path = f"{UPLOAD_DIR}/{filename}"
    deadline = time.time() + build_timeout

    while time.time() < deadline:
        try:
            result = api_post(
                base_url,
                "/api/v1/fileBuildStatus",
                json={
                    "knCode": kn_code,
                    "filePath": target_path,
                },
            )
        except Exception as exc:
            print(f"  Poll error [{filename}]: {exc}, retrying...")
            time.sleep(POLL_INTERVAL)
            continue

        if result["resultCode"] != "0":
            update_file_status(state, filename, "failed")
            return "failed"

        obj = result["resultObject"]
        status = obj.get("status", "")
        current_step = obj.get("currentStep", "")

        if status == "success" or current_step == "complete":
            update_file_status(state, filename, "success")
            return "success"
        if status == "failed":
            update_file_status(state, filename, "failed")
            return "failed"

        time.sleep(POLL_INTERVAL)

    print(f"  Build timeout [{filename}] after {build_timeout}s")
    update_file_status(state, filename, "failed")
    return "failed"


def delete_file(base_url: str, kn_code: str, filename: str):
    """Delete an uploaded file (used for cleanup before retry)."""
    target_path = f"{UPLOAD_DIR}/{filename}"
    api_post(
        base_url,
        "/api/v1/knowledgeItems/delete",
        json={
            "knCode": kn_code,
            "filePath": target_path,
        },
    )


def upload_only(
    base_url: str, kn_code: str, file_path: Path, state: dict, is_retry: bool = False
) -> str:
    """Phase 1: upload file only (no build trigger). Returns status string."""
    filename = file_path.name

    with _progress_lock:
        entry = state["files"].get(filename, {})

    if not is_retry and entry.get("status") in ("success", "building", "uploaded"):
        return entry["status"]

    if is_retry:
        delete_file(base_url, kn_code, filename)
        entry = {}

    if not upload_file(base_url, kn_code, file_path):
        update_file_status(state, filename, "upload_failed")
        return "upload_failed"
    update_file_status(state, filename, "uploaded")
    return "uploaded"


def poll_single(base_url: str, kn_code: str, filename: str, state: dict) -> str:
    """Query build status once and poll if still building. Used by --sync-status."""
    target_path = f"{UPLOAD_DIR}/{filename}"
    result = api_post(
        base_url,
        "/api/v1/fileBuildStatus",
        json={
            "knCode": kn_code,
            "filePath": target_path,
        },
    )
    if result["resultCode"] == "0":
        obj = result["resultObject"]
        status = obj.get("status", "")
        current_step = obj.get("currentStep", "")
        if status == "success" or current_step == "complete":
            update_file_status(state, filename, "success")
            return "success"
        if status == "failed":
            update_file_status(state, filename, "failed")
            return "failed"

    # Still building — poll with timeout
    deadline = time.time() + POLL_TIMEOUT
    while time.time() < deadline:
        result = api_post(
            base_url,
            "/api/v1/fileBuildStatus",
            json={
                "knCode": kn_code,
                "filePath": target_path,
            },
        )
        if result["resultCode"] != "0":
            update_file_status(state, filename, "failed")
            return "failed"

        obj = result["resultObject"]
        status = obj.get("status", "")
        current_step = obj.get("currentStep", "")
        if status == "success" or current_step == "complete":
            update_file_status(state, filename, "success")
            return "success"
        if status == "failed":
            update_file_status(state, filename, "failed")
            return "failed"

        time.sleep(POLL_INTERVAL)

    print(f"  Build timeout [{filename}] during sync")
    update_file_status(state, filename, "failed")
    return "failed"


def main(
    base_url: str,
    kn_name: str = "FRAMES Wiki",
    concurrency: int = 4,
    retry_failed: bool = False,
    retry_file: str | None = None,
    sync_status: bool = False,
    build_timeout: int = POLL_TIMEOUT,
) -> None:
    state = load_state()
    kn_code = ensure_knowledge_base(base_url, kn_name, state)
    ensure_directory(base_url, kn_code)

    # --sync-status: query actual build result for all building/uploaded files
    if sync_status:
        stale_files = [
            fname
            for fname, v in state["files"].items()
            if v.get("status") in ("building", "uploaded")
        ]
        if not stale_files:
            print("No files need status sync")
            return
        print(f"Syncing build status: {len(stale_files)} files")
        synced_success = 0
        synced_failed = 0
        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            futures = {
                executor.submit(poll_single, base_url, kn_code, fn, state): fn
                for fn in stale_files
            }
            with tqdm(total=len(futures), desc="Syncing status") as pbar:
                for future in as_completed(futures):
                    status = future.result()
                    if status == "success":
                        synced_success += 1
                    else:
                        synced_failed += 1
                    pbar.update(1)
        print(f"\nSync complete: {synced_success} succeeded, {synced_failed} failed")
        print("Use retry_failed=True to retry failed files")
        return

    all_files = sorted(WIKI_PAGES_DIR.glob("*.txt"))
    if not all_files:
        raise SystemExit(f"No files found in {WIKI_PAGES_DIR}")

    if retry_file:
        target = WIKI_PAGES_DIR / retry_file
        if not target.exists():
            raise SystemExit(f"File not found: {target}")
        files_to_process = [target]
        is_retry = True
    elif retry_failed:
        failed_statuses = {"failed", "upload_failed", "build_trigger_failed"}
        files_to_process = [
            f
            for f in all_files
            if state["files"].get(f.name, {}).get("status") in failed_statuses
        ]
        is_retry = True
        if not files_to_process:
            print("No failed files to retry")
            return
    else:
        files_to_process = all_files
        is_retry = False

    print(f"Knowledge base: {kn_name} (knCode={kn_code})")
    print(
        f"Files to process: {len(files_to_process)}/{len(all_files)}, concurrency: {concurrency}"
    )

    # --- Phase 1: parallel upload only ---
    upload_failed = 0
    uploaded_files: list[Path] = []

    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = {
            executor.submit(upload_only, base_url, kn_code, fp, state, is_retry): fp
            for fp in files_to_process
        }
        with tqdm(total=len(futures), desc="Upload") as pbar:
            for future in as_completed(futures):
                status = future.result()
                if status == "uploaded":
                    uploaded_files.append(futures[future])
                elif status == "upload_failed":
                    upload_failed += 1
                # "success" or "building" means already done, skip
                pbar.update(1)

    already_done = sum(
        1
        for f in files_to_process
        if state["files"].get(f.name, {}).get("status") == "success"
    )
    print(
        f"Upload complete: {len(uploaded_files)} uploaded, "
        f"{already_done} already done, {upload_failed} failed"
    )

    if not uploaded_files:
        print("No new files to build")
        return

    # --- Phase 2: parallel trigger + poll (each file holds one slot) ---
    build_success = 0
    build_failed = 0

    print(
        f"Building {len(uploaded_files)} files "
        f"(concurrency={concurrency}, timeout={build_timeout}s)..."
    )

    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = {
            executor.submit(
                trigger_and_poll, base_url, kn_code, fp.name, state, build_timeout
            ): fp.name
            for fp in uploaded_files
        }
        with tqdm(total=len(futures), desc="Build progress") as pbar:
            for future in as_completed(futures):
                filename = futures[future]
                try:
                    status = future.result()
                except Exception as exc:
                    print(f"  Build exception [{filename}]: {exc}")
                    update_file_status(state, filename, "failed")
                    status = "failed"
                if status == "success":
                    build_success += 1
                else:
                    build_failed += 1
                pbar.update(1)

    total_success = already_done + build_success
    total_failed = upload_failed + build_failed
    print(
        f"\nDone: {total_success} succeeded, {total_failed} failed, {len(all_files)} total"
    )
