# by-qa Domain Language

by-qa manages knowledge files and the derived content that makes those files
searchable and usable by question-answering flows.

## Knowledge construction

**Knowledge File**:
A stable file identity inside a knowledge base, independent of its current name
or path.
_Avoid_: File path, document path

**File Build**:
The complete derivation of Markdown, chunks, embeddings, and retrieval data from
one Knowledge File. Its only model dependency is the embedding model.
_Avoid_: Embedding task, Markdown conversion

**Build Batch**:
One accepted File Build request and the newly created File Build Tasks that it
owns. Files matched to existing tasks are reported as reuse hits but do not
belong to the new batch.
_Avoid_: Parent task, job

**File Build Task**:
One durable attempt to build one Knowledge File from a captured content checksum
and Build Profile.
_Avoid_: Batch item, file path task

**Reuse Hit**:
An acceptance outcome in which an equivalent active, successful, or unsupported
File Build Task already exists, so the current Build Batch creates no task for
that file.
_Avoid_: Reused batch task, shared task

**Build Profile**:
The readable, canonical description of the parser, chunking, embedding, and
retrieval protocol that determines File Build output.
_Avoid_: Build hash, model version

**Built Content**:
Derived data whose latest task succeeded with the Knowledge File's current
checksum and whose Markdown, chunks, embeddings, and retrieval data are still
present.
_Avoid_: Latest task, protocol freshness

**Protocol Freshness**:
Whether Built Content was produced by the current Build Profile. A profile
change does not trigger File Build by itself.
_Avoid_: Content freshness, file freshness

**Inline File Build**:
A File Build owned and synchronously completed by an Entity processing task,
without joining an externally requested Build Batch.
_Avoid_: Priority build, nested batch
