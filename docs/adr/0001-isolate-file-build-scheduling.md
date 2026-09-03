---
status: accepted
---

# Isolate File Build scheduling from Entity processing

External File Builds use their own durable batch, task, runner, and per-process
concurrency limit instead of the Entity processing queue. Discovery and Enrich
continue to perform required File Builds synchronously through the same execution
service and record them as inline tasks, because enqueueing and waiting would let
large directory batches delay or deadlock Entity completion. File mutations stay
available during execution: checksum and deletion snapshots fence publication,
while changed, deleted, or superseded work terminates without committing results.

## Consequences

- Each Build Worker process accepts up to 16 concurrent tasks by default; total
  deployment concurrency is the number of enabled processes multiplied by 16.
- Build and Entity scheduling do not share an application-level embedding limit,
  so deployments must size their common embedding service for both workloads.
- An externally requested build clears existing derived data when execution
  starts. A failure leaves the file unbuilt and does not restore an earlier
  protocol result.
- Callback delivery remains best-effort, and status queries remain authoritative.
