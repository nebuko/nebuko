---
title: "External references"
audience: contributor
covers:
  - defaults/prompts/context.md
sidebar_group: "Reference"
sidebar_label: "External references"
sidebar_order: 14
---

# External references

Contract for the web references `@@context` collects, so downstream agents
actually see page content rather than 1–2 sentence search snippets:

- **Inline curated JSON (agent access path).** `@@context` reads each reference
  page in full, then emits a fenced ```json block inside the
  `### EXTERNAL REFERENCES` section of its report: one object per reference
  with `url`, `date` (ISO-8601 UTC fetch date), `chunks` (verbatim, unedited
  excerpts, ≤ ~2K tokens per article → ≤ ~20K across a run), `summary`
  (1–3 sentences on relevance to the issue), and `score` (0.0–1.0 relevance,
  not credibility). Schema:
  [External Reference JSON](external-references.md).
  This rides the normal comment-history injection, so tool-less agents
  (notably `@@architect`) consume the content with zero new machinery.

Fetch failures degrade gracefully: the reference stays in the report with the
search snippet as its sole chunk, the failure is noted in its summary, and it
is scored accordingly.

## JSON schema

Contract for the fenced ```json block `@@context` emits inside
`### EXTERNAL REFERENCES` of its context report. It is an **array** with one
object per reference; the human-readable entries and this block must cover the
same references. This block is the access path for tool-less agents (e.g. `@@architect`)
([External Reference Sharing](external-references.md)).

| Field | Type | Description |
|-------|------|-------------|
| `url` | string | The reference URL. |
| `date` | string | ISO-8601 UTC fetch date. |
| `chunks` | string[] | Verbatim, unedited excerpts of the page relevant to the issue (≤ ~2,000 tokens per reference, ≈ ≤ 8,000 chars; ≤ ~20K tokens across the whole run). |
| `summary` | string | 1–3 sentences on the article's relevance/interest to the specific issue or question. |
| `score` | number | Float in `0.0`–`1.0`: level of interest/relevance to the issue (not source credibility). |

```json
[
  {
    "url": "https://docs.searxng.org/dev/search_api.html",
    "date": "2026-09-23T09:25:00Z",
    "chunks": ["SearXNG supports querying via a simple HTTP API ..."],
    "summary": "Documents the /search endpoint used by @@context; confirms results[].content is snippet-only.",
    "score": 0.9
  }
]
```

The block must be valid JSON: no comments, no trailing commas, no prose
outside the fence. There is no code model for this contract (prompt-only);
this table is the source of truth.
