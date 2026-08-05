# Agent Run RAG Public Contract

## Scope

This contract extends the synchronous `POST /api/v1/agent/runs` response only. It does not add Agent SSE, real-time trace, answer-level citations, or changes to Agent Runtime, Tool Registry, database, authentication, or Chat SSE behavior.

## Extension shape

The only extension point is the optional field:

```text
steps[].tool_calls[].rag
```

It is emitted only for a completed `knowledge_search` Tool Call whose normalized result can be safely interpreted at the API boundary. Existing fields remain unchanged, and clients that do not know `tool_calls` or `rag` can continue to parse the original response fields.

## Public fields

`rag` contains:

- `status`: one of `success_with_sources`, `no_relevant_sources`, `knowledge_base_empty`, `rag_unavailable`, `embedding_failed`, `output_unavailable`, or `failed`;
- `warning`: a fixed, bounded warning that retrieved text is untrusted reference material;
- `error_code`: a bounded allowlisted code, or `null` for a successful retrieval;
- `references`: a list of safe reference projections.

Each reference contains only fields already present in the internal `RAGReference`:

- `document_id`, at most 256 characters;
- `chunk_id`, at most 256 characters;
- `chunk_index`, a non-negative integer within the public bound;
- `content`, at most 1200 characters;
- `distance`, a finite non-negative cosine distance within the public bound;
- `truncated`, derived from safe content truncation.

The public contract does not add document names, source names, URLs, rank, citation numbers, or inferred trust metadata.

## Error and malformed-output behavior

The `knowledge_search` Tool already returns stable domain error codes for empty knowledge bases, no relevant context, storage failures, embedding unavailability, embedding failures, and RAG service unavailability. The API boundary maps only those known codes. Unknown codes become `failed` and do not expose the original value.

If the normalized Tool output is truncated, malformed JSON, has the wrong top-level shape, or has no list-valued `results`, the API returns `status=output_unavailable` with `output_truncated` or `output_malformed`. It returns an empty `references` list and never attempts to parse partial JSON.

Malformed individual references are dropped. A malformed identifier, index, content field, or distance cannot cause raw data to be returned. Missing optional fields such as `distance` are retained as `null` when the remaining reference metadata is valid.

## Security boundary

The response never includes raw Tool arguments, raw Tool output, query text, Tool messages, Prompt text, model reasoning, Provider responses, Python exceptions or stacks, API keys, database connection details, absolute paths, or nested internal `source` objects. Tool-level errors use a separate allowlist and fixed safe messages.

## Compatibility and testing

Direct answers continue to use the original response shape. Calculator Tool Calls receive the existing safe Tool summary and do not receive a `rag` field. The contract is verified with response-model and HTTP-level tests using injected `AgentRunOutcome` fixtures; those tests do not claim PostgreSQL, pgvector, embedding-model, or external-service smoke coverage.
