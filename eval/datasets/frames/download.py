"""
Download Wikipedia pages (with tables) needed for the FRAMES dataset, output as Markdown.

Uses the Wikipedia action=parse API to fetch full HTML (with tables, infobox),
then cleans it with BeautifulSoup and converts to clean Markdown via markdownify.

Functionality:
  - Extracts all required wiki URLs from frames_queries.jsonl
  - Concurrent download with resume support (skips existing files)
  - Strips references, images, external links, navigation templates, etc.
  - Preserves body text and table data

Usage (programmatic):
  from eval.datasets.frames.download import main
  main(concurrency=8, retry_failed=True)
"""

import hashlib
import json
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from markdownify import markdownify as md
from requests.adapters import HTTPAdapter
from tqdm import tqdm
from urllib3.util.retry import Retry

_HERE = Path(__file__).parent
REPO_ROOT = _HERE.parent.parent.parent

OUTPUT_DIR = REPO_ROOT / "datasets/FRAMES/frames_wiki_pages/wiki_pages"
QUERIES_PATH = REPO_ROOT / "datasets/FRAMES/frames_wiki_pages/frames_queries.jsonl"
FAILED_FILE = REPO_ROOT / "datasets/FRAMES/.download_failed.json"

REQUEST_DELAY = 1.0
MAX_RETRIES = 3
SKIP_PREFIXES = ("Special:", "Category:", "Module:", "Template:", "Help:", "Wikipedia:")

_print_lock = threading.Lock()


def make_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=MAX_RETRIES,
        backoff_factor=1.5,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET", "HEAD"],
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers["User-Agent"] = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
    )
    return session


def url_to_filename(url: str) -> str:
    base = url.split("#")[0]
    match = re.search(
        r"wikipedia\.org/(?:wiki/|w/index\.php\?(?:[^#]*&)?title=)([^#]+)", base
    )
    if match:
        raw = match.group(1).lstrip("/").split("?")[0].split("&")[0]
        title = requests.utils.unquote(raw)
        title = requests.utils.unquote(title)
        title = title.replace("/", "_")[:80]
    else:
        title = hashlib.md5(url.encode()).hexdigest()[:12]
    suffix = hashlib.md5(url.encode()).hexdigest()[:6]
    return f"{title}_{suffix}.txt"


def extract_page_title(url: str, session: requests.Session) -> str | None:
    if re.match(r"https?://w\.wiki/", url):
        try:
            resp = session.head(url, timeout=10, allow_redirects=True)
            url = resp.url
        except Exception:
            return None

    base = url.split("#")[0]
    match = re.search(
        r"wikipedia\.org/(?:wiki/|w/index\.php\?(?:[^#]*&)?title=)([^#]+)", base
    )
    if not match:
        return None

    raw_title = match.group(1).lstrip("/").split("?")[0].split("&")[0]
    title = requests.utils.unquote(raw_title)
    title = requests.utils.unquote(title)
    title = title.replace("_", " ").strip()

    for prefix in SKIP_PREFIXES:
        if title.startswith(prefix):
            return None
    return title if title else None


def clean_html(html: str) -> str:
    """Clean Wikipedia HTML: remove references, images, external links, navigation templates, etc."""
    soup = BeautifulSoup(html, "html.parser")

    # Remove unwanted elements
    selectors = [
        "sup.reference",
        "span.reference",
        ".reflist",
        ".references",
        ".mw-references-wrap",
        "img",
        "figure",
        ".thumb",
        ".mw-file-element",
        ".mw-file-description",
        ".mw-editsection",
        ".navbox",
        ".navbox-styles",
        ".catlinks",
        ".mw-authority-control",
        ".sistersitebox",
        ".side-box",
    ]
    for sel in selectors:
        for el in soup.select(sel):
            el.decompose()

    # Remove trailing junk sections
    remove_ids = {
        "External_links",
        "See_also",
        "Notes",
        "Further_reading",
        "References",
        "Citations",
        "Sources",
        "Bibliography",
    }
    for heading_el in soup.find_all(id=lambda x: x in remove_ids if x else False):
        container = heading_el.find_parent(class_="mw-heading") or heading_el
        for sibling in list(container.find_next_siblings()):
            sibling.decompose()
        container.decompose()

    # Strip all links, keep only text
    for a in soup.find_all("a"):
        a.replace_with(a.get_text())

    return str(soup)


def html_to_markdown(html: str) -> str:
    """Convert cleaned HTML to Markdown and post-process."""
    cleaned = clean_html(html)
    markdown = md(cleaned)
    markdown = re.sub(r"\[\d+\]", "", markdown)
    markdown = re.sub(r"\n{3,}", "\n\n", markdown)
    return markdown.strip()


def fetch_page(url: str, session: requests.Session) -> tuple[str, str]:
    """Fetch a single page, returning (title, markdown). Returns empty strings on failure."""
    title = extract_page_title(url, session)
    if not title:
        return "", ""

    try:
        resp = session.get(
            "https://en.wikipedia.org/w/api.php",
            params={
                "action": "parse",
                "page": title,
                "prop": "text",
                "format": "json",
                "redirects": True,
            },
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()

        if "error" in data:
            return "", ""

        html = data["parse"]["text"]["*"]
        markdown = html_to_markdown(html)
        return title, markdown

    except Exception as e:
        with _print_lock:
            print(f"  [ERROR] {title}: {e}")
        return "", ""


def process_url(
    url: str, filename: str, output_dir: Path, session: requests.Session
) -> bool:
    """Download and save a single URL. Returns True on success."""
    title, markdown = fetch_page(url, session)
    if not markdown:
        return False

    filepath = output_dir / filename
    filepath.write_text(f"# {title}\n\n{markdown}\n", encoding="utf-8")
    time.sleep(REQUEST_DELAY)
    return True


def split_urls(raw_value: str) -> list[str]:
    parts = re.split(r",\s*(?=https?://)", raw_value)
    result = []
    for part in parts:
        url = re.sub(r"\s*\((?:NOT REQUIRED|not required)[^)]*\)\s*$", "", part).strip()
        if not url.startswith("http"):
            continue
        if "#" not in url:
            url = url.rstrip(",.;")
        result.append(url.strip())
    return result


def main(concurrency: int = 4, retry_failed: bool = False) -> None:
    # Fallback to env var if concurrency was not explicitly overridden
    if concurrency == 4:
        concurrency = int(os.environ.get("CONCURRENCY", "4"))

    output_dir = OUTPUT_DIR
    output_dir.mkdir(parents=True, exist_ok=True)

    # Load dataset from HuggingFace and save as local JSONL
    print("Loading FRAMES dataset from HuggingFace...")
    from datasets import load_dataset

    dataset = load_dataset("google/frames-benchmark", split="test")

    # Save queries as JSONL to project directory
    QUERIES_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(QUERIES_PATH, "w", encoding="utf-8") as f:
        for rec in dataset:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print(f"Saved {len(dataset)} queries to {QUERIES_PATH}")

    # Collect all required wiki URLs
    needed: dict[str, str] = {}
    for rec in dataset:
        for i in range(1, 12):
            key = f"wikipedia_link_{i}" if i <= 10 else "wikipedia_link_11+"
            raw = rec.get(key, "")
            if raw:
                for single_url in split_urls(str(raw)):
                    needed[single_url] = url_to_filename(single_url)

    if retry_failed:
        if not FAILED_FILE.exists():
            print("No failed records found")
            return
        failed_urls = json.loads(FAILED_FILE.read_text(encoding="utf-8"))
        to_fetch = {url: needed[url] for url in failed_urls if url in needed}
        print(f"Retrying failed files: {len(to_fetch)}")
    else:
        existing = (
            {f.name for f in output_dir.iterdir()} if output_dir.exists() else set()
        )
        to_fetch = {
            url: fname for url, fname in needed.items() if fname not in existing
        }
        print(
            f"Total {len(needed)} URLs, {len(needed) - len(to_fetch)} already exist, {len(to_fetch)} to download"
        )

    if not to_fetch:
        print("All downloads complete")
        return

    print(f"Concurrency: {concurrency}, Delay: {REQUEST_DELAY}s")

    session = make_session()
    success_count = 0
    failed_urls: list[str] = []

    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = {
            executor.submit(process_url, url, fname, output_dir, session): url
            for url, fname in to_fetch.items()
        }
        with tqdm(total=len(futures), desc="Download progress") as pbar:
            for future in as_completed(futures):
                if future.result():
                    success_count += 1
                else:
                    failed_urls.append(futures[future])
                pbar.update(1)

    # Save failed records
    if failed_urls:
        FAILED_FILE.write_text(
            json.dumps(failed_urls, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    print(
        f"\nDone: {success_count} succeeded, {len(failed_urls)} failed, {len(needed)} total"
    )
    if failed_urls:
        print(f"Failed records saved to {FAILED_FILE}, use retry_failed=True to retry")
