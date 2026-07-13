# Stable Markdown Reference And Move API Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement stable Markdown resource references plus the `/api/v1/knowledgeItems/move` API so files and directory subtrees can move without rewriting stored Markdown, chunks, or embeddings.

**Architecture:** Store Markdown links as stable `byqa-ref://<reference_id>` tokens backed by a new reference table. Resolve tokens at user-facing read/search/download boundaries by joining the current `knowledge_fs_entry.virtual_path`; move operations update filesystem rows and storage locators only. Delete and restore flows maintain reference status transitions so missing targets fall back to the original Markdown target instead of exposing internal tokens or dead absolute paths.

**Tech Stack:** Python 3.12, FastAPI, Pydantic v2, OpenGauss SQL, async repository/service layer, MinIO-compatible storage provider, pytest, existing `knowledge_common` path/reference helpers.

---

## Source Documents

- Spec: `docs/superpowers/specs/2026-07-08-stable-markdown-resource-reference-design.md`
- API contract: `docs/modules/knowledge/api.md`
- Relevant skills for execution: @superpowers:subagent-driven-development, @superpowers:executing-plans, @superpowers:test-driven-development, @superpowers:verification-before-completion

## File Structure

- Create: `src/by_qa/knowledge_base/sql/0xx_knowledge_file_reference.sql`
  - Migration for `knowledge_file_reference`, indexes, and constraints.
- Create: `src/by_qa/knowledge_base/repositories/knowledge_file_reference_repository.py`
  - All SQL for creating, reading, resolving, breaking, and restoring references.
- Modify: `src/by_qa/knowledge_base/services/markdown_reference_rewriter.py`
  - Change from path-rewriting pure service to DB-backed token writer.
- Create: `src/by_qa/knowledge_base/services/markdown_reference_resolver.py`
  - Batch resolver for `byqa-ref://<id>` tokens in read/search/download output.
- Modify: `src/by_qa/knowledge_base/services/knowledge_item_ingestion_service.py`
  - Move Markdown rewrite into the upload transaction, return the created file row, and run pending reference compensation.
- Modify: `src/by_qa/knowledge_base/services/zip_batch_import_service.py`
  - Stop route/service-level pre-rewrite and rely on ingestion transaction; keep non-Markdown-first ordering.
- Modify: `src/by_qa/knowledge_base/services/knowledge_base_service.py`
  - Resolve Markdown reads/downloads, implement move orchestration, and mark inbound references broken on file/directory delete.
- Modify: `src/by_qa/knowledge_base/services/knowledge_item_search_service.py`
  - Batch resolve `chunk_text` before building `SearchHit`.
- Modify: `src/by_qa/knowledge_base/repositories/knowledge_item_search_repository.py`
  - Return current `knowledge_fs_entry.virtual_path` for outward `file_path`.
- Modify: `src/by_qa/knowledge_base/repositories/knowledge_fs_entry_repository.py`
  - Add helpers needed by move validation, subtree moves, and delete-time reference marking.
- Modify: `src/by_qa/knowledge_base/api/schemas.py`
  - Add move request/response models and reference query models.
- Modify: `src/by_qa/knowledge_base/api/routes.py`
  - Add move/reference endpoints and remove route-level Markdown reference rewrite.
- Modify: `src/by_qa/knowledge_base/infrastructure/runtime.py`
  - Wire the reference repository/resolver into services.
- Modify: `src/by_qa/knowledge_common/markdown_reference.py`
  - Add shared bare-token detection while keeping Markdown link span detection stable.
- Modify: `src/by_qa/knowledge_build/services/document_chunking_service.py`
  - Treat `byqa-ref://<id>` token spans as atomic chunk boundaries.
- Test: `tests/knowledge_base/unit/`
  - Repository, rewriter, resolver, move, delete, search service tests.
- Test: `tests/knowledge_base/integration/test_kb_api_stateful_integration.py`
  - API-level upload/read/move/delete/restore/search coverage.
- Test: `tests/knowledge_build/unit/`
  - Chunking token boundary tests.

## Implementation Tasks

### Task 1: Add Reference Table And Repository

**Files:**
- Create: `src/by_qa/knowledge_base/sql/0xx_knowledge_file_reference.sql`
- Create: `src/by_qa/knowledge_base/repositories/knowledge_file_reference_repository.py`
- Test: `tests/knowledge_base/unit/test_knowledge_file_reference_repository.py`

- [ ] **Step 1: Write repository tests first**

Cover:
- `create_reference()` inserts `resolved`, `unresolved`, and `broken`-compatible rows.
- `list_by_reference_ids()` joins target `knowledge_fs_entry` and exposes target deletion state.
- `resolve_pending_for_path()` updates `unresolved` and `broken` rows by exact `target_path`.
- `mark_targets_deleted()` accepts multiple `(target_fs_entry_id, virtual_path)` pairs and writes each row's own `target_path`.
- `list_sources_by_target()` supports resolved lookup by `target_fs_entry_id` and broken lookup by `target_path`.

Run:

```bash
NO_PROXY=127.0.0.1,localhost HTTPS_PROXY= HTTP_PROXY= no_proxy=127.0.0.1,localhost http_proxy= https_proxy= uv run python -m pytest tests/knowledge_base/unit/test_knowledge_file_reference_repository.py -v
```

Expected: FAIL because the migration/repository do not exist.

- [ ] **Step 2: Add SQL migration**

Create `knowledge_file_reference` with:

```sql
kid bigserial PRIMARY KEY,
knowledge_base_id bigint NOT NULL REFERENCES knowledge_base(kid),
source_fs_entry_id bigint NOT NULL REFERENCES knowledge_fs_entry(kid),
target_fs_entry_id bigint NULL REFERENCES knowledge_fs_entry(kid),
original_target text NOT NULL,
target_path text NULL,
target_suffix text NOT NULL DEFAULT '',
target_kind text NOT NULL DEFAULT 'FILE',
status text NOT NULL,
last_resolved_at timestamptz NULL,
created_at timestamptz NOT NULL DEFAULT NOW(),
updated_at timestamptz NOT NULL DEFAULT NOW()
```

Add indexes:
- `idx_kfr_source`
- `idx_kfr_pending_path`
- `idx_kfr_target`

Add constraints:
- `status IN ('resolved', 'unresolved', 'broken')`
- `target_kind IN ('FILE')`

- [ ] **Step 3: Implement repository methods**

Implement methods named in the spec:

```python
create_reference(...)
list_by_source(...)
list_by_reference_ids(...)
resolve_pending_for_path(...)
mark_targets_deleted(...)
mark_target_restored(...)
list_sources_by_target(...)
```

Keep SQL-only behavior in the repository. Do not perform Markdown string replacement here.

- [ ] **Step 4: Run repository tests**

Run the same pytest command from Step 1.

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/by_qa/knowledge_base/sql/0xx_knowledge_file_reference.sql src/by_qa/knowledge_base/repositories/knowledge_file_reference_repository.py tests/knowledge_base/unit/test_knowledge_file_reference_repository.py
git commit -m "feat: add knowledge file reference repository"
```

### Task 2: Add Shared Reference Token Detection

**Files:**
- Modify: `src/by_qa/knowledge_common/markdown_reference.py`
- Test: `tests/knowledge_common/test_markdown_reference.py` or existing equivalent test file

- [ ] **Step 1: Write failing tests**

Add tests for:
- `detect_reference_spans()` remains unchanged for `[]()` and `![]()`.
- A new helper detects bare `byqa-ref://12345` spans.
- Invalid partial tokens like `byqa-ref://` are ignored.
- Adjacent punctuation does not become part of the token.

Run:

```bash
NO_PROXY=127.0.0.1,localhost HTTPS_PROXY= HTTP_PROXY= no_proxy=127.0.0.1,localhost http_proxy= https_proxy= uv run python -m pytest tests/knowledge_common/test_markdown_reference.py -v
```

Expected: FAIL because the bare-token helper does not exist.

- [ ] **Step 2: Implement bare-token helper**

Add a small function such as:

```python
def detect_reference_token_spans(text: str) -> list[tuple[int, int, int]]:
    ...
```

Return `(start, end, reference_id)` for tokens matching `byqa-ref://<digits>`.

- [ ] **Step 3: Run tests**

Run the same pytest command from Step 1.

Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add src/by_qa/knowledge_common/markdown_reference.py tests/knowledge_common/test_markdown_reference.py
git commit -m "feat: detect stable markdown reference tokens"
```

### Task 3: Convert Rewriter To Transactional Token Writer

**Files:**
- Modify: `src/by_qa/knowledge_base/services/markdown_reference_rewriter.py`
- Modify: `src/by_qa/knowledge_base/repositories/knowledge_fs_entry_repository.py`
- Test: `tests/knowledge_base/unit/test_markdown_reference_rewriter.py`

- [ ] **Step 1: Write failing tests**

Cover:
- Existing file target creates `status='resolved'`, `target_fs_entry_id=<id>`, `target_path=None`, and tokenized Markdown.
- Missing file target creates `status='unresolved'`, `target_fs_entry_id=None`, `target_path=<normalized path>`, and tokenized Markdown.
- `#anchor`, empty targets, external URLs, escaping above KB root, and directory targets remain original.
- Query/fragment is stored in `target_suffix`, while `original_target` remains the exact user target.
- `target_kind` is stored as uppercase `FILE`.

Run:

```bash
NO_PROXY=127.0.0.1,localhost HTTPS_PROXY= HTTP_PROXY= no_proxy=127.0.0.1,localhost http_proxy= https_proxy= uv run python -m pytest tests/knowledge_base/unit/test_markdown_reference_rewriter.py -v
```

Expected: FAIL because the service still rewrites to absolute paths.

- [ ] **Step 2: Add fs lookup helper**

Add a repository helper that returns a non-deleted file entry by normalized virtual path and explicitly distinguishes file from directory. Directory targets must not be registered in the first version.

- [ ] **Step 3: Implement DB-backed rewriter**

Change the rewriter API to accept:

```python
text: str
source_dir: str
knowledge_base_id: int
source_fs_entry_id: int
cursor: Any
reference_repository: KnowledgeFileReferenceRepository
fs_entry_repository: KnowledgeFsEntryRepository
```

For each span:
1. Normalize with existing `split_target` / `normalize_kb_path`.
2. Insert the reference row and get `kid`.
3. Replace only the target inside the original Markdown span with `byqa-ref://<kid>`.

- [ ] **Step 4: Run tests**

Run the same pytest command from Step 1.

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/by_qa/knowledge_base/services/markdown_reference_rewriter.py src/by_qa/knowledge_base/repositories/knowledge_fs_entry_repository.py tests/knowledge_base/unit/test_markdown_reference_rewriter.py
git commit -m "feat: store markdown references as stable tokens"
```

### Task 4: Add Batch Resolver And Read/Download Integration

**Files:**
- Create: `src/by_qa/knowledge_base/services/markdown_reference_resolver.py`
- Modify: `src/by_qa/knowledge_base/services/knowledge_base_service.py`
- Modify: `src/by_qa/knowledge_base/infrastructure/runtime.py`
- Test: `tests/knowledge_base/unit/test_markdown_reference_resolver.py`
- Test: `tests/knowledge_base/unit/test_knowledge_base_service_read_file.py`

- [ ] **Step 1: Write failing resolver tests**

Cover:
- Resolved + visible target returns `virtual_path + target_suffix`.
- Resolved + deleted target returns `original_target`.
- Resolved + missing joined target returns `original_target`.
- Unresolved returns `original_target`.
- Broken returns `original_target`.
- Unresolved/broken do not append `target_suffix` again.
- Multiple texts resolve with one repository call for all ids.

Run:

```bash
NO_PROXY=127.0.0.1,localhost HTTPS_PROXY= HTTP_PROXY= no_proxy=127.0.0.1,localhost http_proxy= https_proxy= uv run python -m pytest tests/knowledge_base/unit/test_markdown_reference_resolver.py -v
```

Expected: FAIL because resolver does not exist.

- [ ] **Step 2: Implement resolver**

Expose:

```python
async def resolve_texts(
    self,
    *,
    knowledge_base_id: int,
    texts: list[str],
) -> list[str]:
    ...
```

Use `detect_reference_token_spans()` and `KnowledgeFileReferenceRepository.list_by_reference_ids()`.

- [ ] **Step 3: Write failing read/download tests**

Cover:
- `read_file` slices lines first, then resolves tokens.
- Markdown `download_file` resolves tokens before returning bytes.
- Non-Markdown downloads do not invoke resolver.

Run:

```bash
NO_PROXY=127.0.0.1,localhost HTTPS_PROXY= HTTP_PROXY= no_proxy=127.0.0.1,localhost http_proxy= https_proxy= uv run python -m pytest tests/knowledge_base/unit/test_knowledge_base_service_read_file.py -v
```

Expected: FAIL because service does not call resolver.

- [ ] **Step 4: Wire resolver into `KnowledgeBaseService`**

Inject the resolver through `build_knowledge_base_service()`. In `read_file`, resolve only the selected output text. In `download_file`, resolve Markdown content before returning user-facing bytes.

- [ ] **Step 5: Run tests**

Run both pytest commands from this task.

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/by_qa/knowledge_base/services/markdown_reference_resolver.py src/by_qa/knowledge_base/services/knowledge_base_service.py src/by_qa/knowledge_base/infrastructure/runtime.py tests/knowledge_base/unit/test_markdown_reference_resolver.py tests/knowledge_base/unit/test_knowledge_base_service_read_file.py
git commit -m "feat: resolve stable markdown references on read"
```

### Task 5: Move Rewriting Into Upload Transaction

**Files:**
- Modify: `src/by_qa/knowledge_base/services/knowledge_item_ingestion_service.py`
- Modify: `src/by_qa/knowledge_base/api/routes.py`
- Modify: `src/by_qa/knowledge_base/infrastructure/runtime.py`
- Test: `tests/knowledge_base/unit/test_knowledge_item_ingestion_service.py`
- Test: route/upload tests if present under `tests/knowledge_base/unit/`

- [ ] **Step 1: Write failing ingestion tests**

Cover:
- Markdown upload creates `fs_entry` first, rewrites to tokenized bytes, writes storage, updates metadata, and commits.
- If storage write fails after references are inserted, transaction rolls back and storage cleanup still runs.
- `upload_file()` returns a row containing `fs_entry_id`, `knowledge_base_id`, `virtual_path`, and `mime_type`.
- Non-Markdown upload does not call rewriter.
- Uploading any file calls pending compensation after file metadata exists.

Run:

```bash
NO_PROXY=127.0.0.1,localhost HTTPS_PROXY= HTTP_PROXY= no_proxy=127.0.0.1,localhost http_proxy= https_proxy= uv run python -m pytest tests/knowledge_base/unit/test_knowledge_item_ingestion_service.py -v
```

Expected: FAIL because upload returns `None` and route-level rewrite still owns Markdown behavior.

- [ ] **Step 2: Change ingestion constructor**

Inject:
- `knowledge_file_reference_repository`
- `markdown_reference_rewriter`

Wire both in `build_knowledge_item_ingestion_service()`.

- [ ] **Step 3: Rewrite Markdown inside upload transaction**

After `create_file_entry()` and before `storage_provider.write()`:
1. Detect Markdown by MIME/path.
2. Decode bytes.
3. Call DB-backed rewriter with `source_fs_entry_id`.
4. Replace `request.file_content` for storage and checksum purposes with tokenized bytes.

- [ ] **Step 4: Add pending compensation**

After file metadata update, call:

```python
resolve_pending_for_path(
    knowledge_base_id=knowledge_base_id,
    target_path="/" + normalized_object_path,
    target_fs_entry_id=fs_entry_id,
)
```

This must occur before commit.

- [ ] **Step 5: Remove route-level rewrite**

In `api/routes.py`, remove direct `MarkdownReferenceRewriter` construction and calls from the upload endpoint. Route should pass the original uploaded bytes to ingestion.

- [ ] **Step 6: Run ingestion and route tests**

Run the pytest command from Step 1 plus any upload route test file touched.

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/by_qa/knowledge_base/services/knowledge_item_ingestion_service.py src/by_qa/knowledge_base/api/routes.py src/by_qa/knowledge_base/infrastructure/runtime.py tests/knowledge_base/unit/test_knowledge_item_ingestion_service.py
git commit -m "feat: rewrite markdown references during ingestion"
```

### Task 6: Update Zip Batch Import Flow

**Files:**
- Modify: `src/by_qa/knowledge_base/services/zip_batch_import_service.py`
- Test: `tests/knowledge_base/unit/test_zip_batch_import_service.py`

- [ ] **Step 1: Write failing tests**

Cover:
- Zip import no longer constructs service-level `MarkdownReferenceRewriter`.
- Non-Markdown files are uploaded before Markdown files.
- Markdown upload receives original Markdown bytes and lets ingestion rewrite inside its transaction.
- Batch-end pending compensation is called for successful uploaded files if needed.
- If a referenced file in the zip fails to upload, the source Markdown reference remains unresolved.

Run:

```bash
NO_PROXY=127.0.0.1,localhost HTTPS_PROXY= HTTP_PROXY= no_proxy=127.0.0.1,localhost http_proxy= https_proxy= uv run python -m pytest tests/knowledge_base/unit/test_zip_batch_import_service.py -v
```

Expected: FAIL because current zip service still constructs a batch-aware rewriter.

- [ ] **Step 2: Remove external rewrite from zip service**

Keep the non-Markdown-first, Markdown-second ordering. Adapt fake ingestion services to the new `upload_file()` return row.

- [ ] **Step 3: Add batch compensation if not covered by per-file upload**

If per-file compensation fully covers the flow, keep a no-op or narrow helper out of the service. If batch compensation is still required for zip-specific ordering, call the repository through ingestion/service boundaries, not by duplicating SQL in zip service.

- [ ] **Step 4: Run tests**

Run the pytest command from Step 1.

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/by_qa/knowledge_base/services/zip_batch_import_service.py tests/knowledge_base/unit/test_zip_batch_import_service.py
git commit -m "refactor: let ingestion own zip markdown reference rewrite"
```

### Task 7: Resolve Search Output And Current File Paths

**Files:**
- Modify: `src/by_qa/knowledge_base/repositories/knowledge_item_search_repository.py`
- Modify: `src/by_qa/knowledge_base/services/knowledge_item_search_service.py`
- Modify: `src/by_qa/knowledge_base/infrastructure/runtime.py`
- Test: `tests/knowledge_base/unit/test_knowledge_item_search_service.py`
- Test: `tests/knowledge_base/unit/test_knowledge_item_search_repository.py`

- [ ] **Step 1: Write failing tests**

Cover:
- `chunk_text` containing `byqa-ref://<id>` is resolved before `SearchHit`.
- Multiple search hits from the same KB use resolver batch mode.
- Search `file_path` uses current `knowledge_fs_entry.virtual_path`, not stale `knowledge_chunk_retrieval_mv.full_path`.

Run:

```bash
NO_PROXY=127.0.0.1,localhost HTTPS_PROXY= HTTP_PROXY= no_proxy=127.0.0.1,localhost http_proxy= https_proxy= uv run python -m pytest tests/knowledge_base/unit/test_knowledge_item_search_service.py tests/knowledge_base/unit/test_knowledge_item_search_repository.py -v
```

Expected: FAIL because search currently emits raw `chunk_text` and projection `full_path`.

- [ ] **Step 2: Update search repository SELECTs**

Keep retrieval projection for candidate recall, but join `knowledge_fs_entry fe` and return:

```sql
ltrim(fe.virtual_path, '/') AS full_path
```

for outward paths.

- [ ] **Step 3: Batch resolve search chunks**

In `KnowledgeItemSearchService.search()`, group final hits by `knowledge_base_id` or KB code resolved to ID, call resolver once per group, then construct `SearchHit`.

- [ ] **Step 4: Run tests**

Run the pytest command from Step 1.

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/by_qa/knowledge_base/repositories/knowledge_item_search_repository.py src/by_qa/knowledge_base/services/knowledge_item_search_service.py src/by_qa/knowledge_base/infrastructure/runtime.py tests/knowledge_base/unit/test_knowledge_item_search_service.py tests/knowledge_base/unit/test_knowledge_item_search_repository.py
git commit -m "feat: resolve markdown references in search results"
```

### Task 8: Protect Stable Tokens During Chunking

**Files:**
- Modify: `src/by_qa/knowledge_build/services/document_chunking_service.py`
- Modify: `src/by_qa/knowledge_common/markdown_reference.py`
- Test: `tests/knowledge_build/unit/test_document_chunking_service.py`

- [ ] **Step 1: Write failing chunking tests**

Cover:
- A bare `byqa-ref://12345` token is never split across two chunks.
- Markdown link spans that contain stable tokens remain protected.
- If a token/span exceeds chunk size, it becomes its own chunk and does not produce token fragments.

Run:

```bash
NO_PROXY=127.0.0.1,localhost HTTPS_PROXY= HTTP_PROXY= no_proxy=127.0.0.1,localhost http_proxy= https_proxy= uv run python -m pytest tests/knowledge_build/unit/test_document_chunking_service.py -v
```

Expected: FAIL for bare-token chunk boundary cases.

- [ ] **Step 2: Include token spans in chunk boundary logic**

Update `_reference_spans_overlapping()` to combine:
- Markdown link/image spans from `detect_reference_spans()`
- Bare token spans from `detect_reference_token_spans()`

- [ ] **Step 3: Run tests**

Run the pytest command from Step 1.

Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add src/by_qa/knowledge_build/services/document_chunking_service.py src/by_qa/knowledge_common/markdown_reference.py tests/knowledge_build/unit/test_document_chunking_service.py
git commit -m "feat: keep stable markdown reference tokens atomic"
```

### Task 9: Implement Move API Models And Validation

**Files:**
- Modify: `src/by_qa/knowledge_base/api/schemas.py`
- Modify: `src/by_qa/knowledge_base/api/routes.py`
- Test: `tests/knowledge_base/unit/test_move_item_schemas.py`
- Test: route tests under `tests/knowledge_base/unit/` if present

- [ ] **Step 1: Write failing schema tests**

Cover:
- `sourcePath` must be a non-empty list.
- Paths must start with `/` and must not contain `..`.
- Root `/` cannot be moved.
- Exactly one of `targetDirectoryPath` and `targetFilePath` is required.
- `targetDirectoryPath` accepts one or many sources.
- `targetFilePath` accepts only one file source; service-level validation rejects directory source.
- Duplicate source paths fail whole-request validation.
- `overwrite` defaults to `false`; `true` is rejected or ignored according to API docs.

Run:

```bash
NO_PROXY=127.0.0.1,localhost HTTPS_PROXY= HTTP_PROXY= no_proxy=127.0.0.1,localhost http_proxy= https_proxy= uv run python -m pytest tests/knowledge_base/unit/test_move_item_schemas.py -v
```

Expected: FAIL because models do not exist.

- [ ] **Step 2: Add Pydantic models**

Add:
- `MoveKnowledgeItemsRequest`
- `MoveKnowledgeItemResult`
- `MoveKnowledgeItemsSummary`
- `MoveKnowledgeItemsResponse`

Use serialization aliases matching `docs/modules/knowledge/api.md`.

- [ ] **Step 3: Add route**

Add `POST /api/v1/knowledgeItems/move`. Structural validation errors return the standard error envelope; per-source business failures are represented in `resultObject.data`.

- [ ] **Step 4: Run tests**

Run the pytest command from Step 1.

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/by_qa/knowledge_base/api/schemas.py src/by_qa/knowledge_base/api/routes.py tests/knowledge_base/unit/test_move_item_schemas.py
git commit -m "feat: add knowledge item move API schema"
```

### Task 10: Implement Move Service And Repository Helpers

**Files:**
- Modify: `src/by_qa/knowledge_base/services/knowledge_base_service.py`
- Modify: `src/by_qa/knowledge_base/repositories/knowledge_fs_entry_repository.py`
- Modify: `src/by_qa/knowledge_base/repositories/knowledge_fetch_cache_repository.py` if cache invalidation needs a helper
- Test: `tests/knowledge_base/unit/test_knowledge_base_service_move.py`
- Test: `tests/knowledge_base/unit/test_knowledge_fs_entry_repository.py`

- [ ] **Step 1: Write failing service tests**

Cover:
- Single file to existing or auto-created `targetDirectoryPath`.
- Multiple files to existing or auto-created `targetDirectoryPath`.
- Directory subtree to `targetDirectoryPath`.
- Single file to `targetFilePath`.
- `targetFilePath` parent directories auto-create.
- Existing final target fails when `overwrite=false`.
- Moving a directory into itself or child fails whole request.
- Mixed source list with one missing path returns per-item failure without moving invalid item.
- Storage-provider `storage_path_bound_to_logical_path` true moves original and Markdown locators and rolls back storage moves on DB failure.
- Resolved references do not update `knowledge_file_reference.target_path` during move.
- Moving source Markdown does not recalculate unresolved `target_path`.

Run:

```bash
NO_PROXY=127.0.0.1,localhost HTTPS_PROXY= HTTP_PROXY= no_proxy=127.0.0.1,localhost http_proxy= https_proxy= uv run python -m pytest tests/knowledge_base/unit/test_knowledge_base_service_move.py tests/knowledge_base/unit/test_knowledge_fs_entry_repository.py -v
```

Expected: FAIL because move service does not exist.

- [ ] **Step 2: Add repository helpers**

Add focused helpers for:
- Getting any child entry by path and type.
- Creating parent directories for `targetDirectoryPath`.
- Creating parent directories for `targetFilePath`.
- Moving a file or directory entry to a new parent/name.
- Updating descendant `virtual_path` for directory moves.
- Listing subtree file entries with storage locators before storage-bound moves.

Prefer reusing existing `create_directory_entry()`, `create_file_entry()`, `rename_entry()`, and subtree listing code where practical.

- [ ] **Step 3: Implement service orchestration**

Add a method such as:

```python
async def move_knowledge_items(
    self,
    request: MoveKnowledgeItemsRequest,
) -> MoveKnowledgeItemsResponse:
    ...
```

Behavior:
- Normalize and validate all structural rules before moving.
- Resolve target mode from `targetDirectoryPath` or `targetFilePath`.
- Auto-create required directories.
- Process each source in a transaction.
- Return `targetPath` as the actual final path.
- Invalidate fetch cache for moved file IDs.

- [ ] **Step 4: Connect route to service**

Route calls `KnowledgeBaseService.move_knowledge_items()` and returns the documented envelope.

- [ ] **Step 5: Run tests**

Run the pytest command from Step 1.

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/by_qa/knowledge_base/services/knowledge_base_service.py src/by_qa/knowledge_base/repositories/knowledge_fs_entry_repository.py src/by_qa/knowledge_base/api/routes.py tests/knowledge_base/unit/test_knowledge_base_service_move.py tests/knowledge_base/unit/test_knowledge_fs_entry_repository.py
git commit -m "feat: move knowledge files and directories"
```

### Task 11: Mark References Broken On File And Directory Delete

**Files:**
- Modify: `src/by_qa/knowledge_base/services/knowledge_item_ingestion_service.py`
- Modify: `src/by_qa/knowledge_base/services/knowledge_base_service.py`
- Modify: `src/by_qa/knowledge_base/infrastructure/runtime.py`
- Test: `tests/knowledge_base/unit/test_knowledge_item_delete_references.py`
- Test: `tests/knowledge_base/unit/test_knowledge_directory_delete_references.py`

- [ ] **Step 1: Write failing delete tests**

Cover:
- Deleting a target file marks inbound references `broken`, writes target deletion-time `virtual_path` to `target_path`, and clears `target_fs_entry_id`.
- Deleting a source Markdown leaves reference rows intact.
- Deleting a directory subtree marks inbound references for every file in the subtree.
- Directory root itself is not treated as a reference target.
- Resolver fallback still returns `original_target` if delete marking is missed and target row is already `is_deleted=true`.

Run:

```bash
NO_PROXY=127.0.0.1,localhost HTTPS_PROXY= HTTP_PROXY= no_proxy=127.0.0.1,localhost http_proxy= https_proxy= uv run python -m pytest tests/knowledge_base/unit/test_knowledge_item_delete_references.py tests/knowledge_base/unit/test_knowledge_directory_delete_references.py -v
```

Expected: FAIL because delete flows do not update references.

- [ ] **Step 2: Inject reference repository into delete-capable services**

Wire `KnowledgeFileReferenceRepository` into:
- `KnowledgeItemIngestionService`
- `KnowledgeBaseService`

- [ ] **Step 3: Update file delete**

Before `soft_delete_file_entry()`, call `mark_targets_deleted()` for the target file row if the deleted entry is a file.

- [ ] **Step 4: Update directory delete**

Before `soft_delete_subtree()`, list subtree file entries and call `mark_targets_deleted()` for the whole list.

- [ ] **Step 5: Run tests**

Run the pytest command from Step 1.

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/by_qa/knowledge_base/services/knowledge_item_ingestion_service.py src/by_qa/knowledge_base/services/knowledge_base_service.py src/by_qa/knowledge_base/infrastructure/runtime.py tests/knowledge_base/unit/test_knowledge_item_delete_references.py tests/knowledge_base/unit/test_knowledge_directory_delete_references.py
git commit -m "feat: mark markdown references broken on delete"
```

### Task 12: Add Reverse Reference Query

**Files:**
- Modify: `src/by_qa/knowledge_base/api/schemas.py`
- Modify: `src/by_qa/knowledge_base/api/routes.py`
- Modify: `src/by_qa/knowledge_base/services/knowledge_base_service.py`
- Modify: `src/by_qa/knowledge_base/repositories/knowledge_file_reference_repository.py`
- Test: `tests/knowledge_base/unit/test_knowledge_reference_query.py`

- [ ] **Step 1: Write failing tests**

Cover:
- Existing target file inbound query uses `target_fs_entry_id`.
- Deleted target path inbound query uses `target_path` for broken rows.
- Default source query excludes deleted source files.
- Response includes source path, original target, target suffix, target path, and status.

Run:

```bash
NO_PROXY=127.0.0.1,localhost HTTPS_PROXY= HTTP_PROXY= no_proxy=127.0.0.1,localhost http_proxy= https_proxy= uv run python -m pytest tests/knowledge_base/unit/test_knowledge_reference_query.py -v
```

Expected: FAIL because endpoint does not exist.

- [ ] **Step 2: Add models and service method**

Add request/response models for `POST /api/v1/knowledgeItems/references`. Implement inbound lookup only for the first version.

- [ ] **Step 3: Add route**

Return the standard success envelope with the inbound reference list.

- [ ] **Step 4: Run tests**

Run the pytest command from Step 1.

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/by_qa/knowledge_base/api/schemas.py src/by_qa/knowledge_base/api/routes.py src/by_qa/knowledge_base/services/knowledge_base_service.py src/by_qa/knowledge_base/repositories/knowledge_file_reference_repository.py tests/knowledge_base/unit/test_knowledge_reference_query.py
git commit -m "feat: query inbound markdown references"
```

### Task 13: Add Stateful API Integration Coverage

**Files:**
- Modify: `tests/knowledge_base/integration/test_kb_api_stateful_integration.py`

- [ ] **Step 1: Add upload/read/restore integration tests**

Cover:
- Upload `a.md` referencing existing `b.md`, then `readFile(a.md)` outputs current `/.../b.md`.
- Upload `a.md` before `b.md`, then `readFile(a.md)` outputs original `b.md`; uploading `b.md` later resolves it.
- Delete `b.md` and verify `readFile/search` returns original target and never returns `byqa-ref://`.
- Re-upload the last known path and verify reference resolves again.

- [ ] **Step 2: Add move integration tests**

Cover:
- Move target file and verify `readFile/search` output the new path without rebuilding chunks.
- Move directory subtree and verify all inbound references to files under the subtree output new paths.
- Move source Markdown and verify unresolved pending path does not change.
- Use `targetDirectoryPath` with auto-created directory.
- Use `targetFilePath` with auto-created parent directories.

- [ ] **Step 3: Add zip and directory delete integration tests**

Cover:
- Zip import resolves internal Markdown-to-file references.
- Directory delete breaks inbound references for all files under the subtree.
- Reverse reference query returns resolved and broken inbound rows according to target state.

- [ ] **Step 4: Run stateful integration tests**

Run:

```bash
NO_PROXY=127.0.0.1,localhost HTTPS_PROXY= HTTP_PROXY= no_proxy=127.0.0.1,localhost http_proxy= https_proxy= uv run python -m pytest tests/knowledge_base/integration/test_kb_api_stateful_integration.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/knowledge_base/integration/test_kb_api_stateful_integration.py
git commit -m "test: cover stable markdown references through api"
```

### Task 14: Final Verification And Documentation Sync

**Files:**
- Modify only if needed: `docs/modules/knowledge/api.md`
- Modify only if needed: `docs/superpowers/specs/2026-07-08-stable-markdown-resource-reference-design.md`

- [ ] **Step 1: Run focused unit test suites**

Run:

```bash
NO_PROXY=127.0.0.1,localhost HTTPS_PROXY= HTTP_PROXY= no_proxy=127.0.0.1,localhost http_proxy= https_proxy= bash scripts/knowledge_base/run_unit_tests.sh
```

Expected: PASS.

Run:

```bash
NO_PROXY=127.0.0.1,localhost HTTPS_PROXY= HTTP_PROXY= no_proxy=127.0.0.1,localhost http_proxy= https_proxy= bash scripts/knowledge_build/run_unit_tests.sh
```

Expected: PASS.

- [ ] **Step 2: Run API integration tests**

Run:

```bash
NO_PROXY=127.0.0.1,localhost HTTPS_PROXY= HTTP_PROXY= no_proxy=127.0.0.1,localhost http_proxy= https_proxy= uv run python -m pytest tests/knowledge_base/integration/test_kb_api_stateful_integration.py -v
```

Expected: PASS.

- [ ] **Step 3: Run lint/format checks**

Run:

```bash
NO_PROXY=127.0.0.1,localhost HTTPS_PROXY= HTTP_PROXY= no_proxy=127.0.0.1,localhost http_proxy= https_proxy= uv run pre-commit run --all-files
```

Expected: PASS.

- [ ] **Step 4: Verify docs match behavior**

Check:
- `targetDirectoryPath` auto-creates the target directory.
- `targetFilePath` auto-creates parent directories.
- Exactly one target field is required.
- Broken references fall back to `original_target`.
- Stored Markdown is tokenized; original uploaded Markdown bytes are not preserved.

- [ ] **Step 5: Commit final documentation fixes if any**

```bash
git add docs/modules/knowledge/api.md docs/superpowers/specs/2026-07-08-stable-markdown-resource-reference-design.md
git commit -m "docs: sync stable reference implementation notes"
```

Skip this commit if no documentation changes are needed.

## Execution Notes

- Do not migrate old Markdown/chunk contents. Existing absolute paths stay as they are.
- Do not modify stored Markdown or chunks during move operations.
- Do not register directory links in the first version; leave those Markdown targets unchanged.
- Do not expose `byqa-ref://<id>` through `readFile`, Markdown download, or search results.
- Keep `target_path` meaningful only when `target_fs_entry_id IS NULL`.
- Treat same-path reupload after delete as product-level restore/replacement.
- Prefer small commits after each task so regressions can be bisected cleanly.
