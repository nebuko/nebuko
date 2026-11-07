---
title: "Comment recall (fetch_comment_bodies)"
audience: contributor
covers:
  - src/nebuko/engine/fetch_comment_bodies_tool.py
  - src/nebuko/engine/engine_tools.py
sidebar_group: "Concepts"
sidebar_label: "Comment recall"
sidebar_order: 27
---

# Comment recall (fetch_comment_bodies)

Agents receive the latest message verbatim plus one-line summaries of the
earlier thread comments. When a summary is not enough — an agent (typically
`@@architect` on a rejected gate) needs the full text of an earlier comment —
it calls the non-terminal engine tool `fetch_comment_bodies`.

## Using it

Call the tool with the ids you want the full text of:

```json
{"comment_ids": [6413627704, 6413699200]}
```

The observation returns the bodies found in this run's cached thread and the
ids that were not found (already trimmed out of the cache, or never part of
the thread):

```json
{"bodies": {"6413627704": "...full comment text..."}, "not_found": [6413699200]}
```

## How it works

The tool is declared in `DEFAULT_ENGINE_TOOLS`
(`src/nebuko/engine/engine_tools.py`) as `payload_kind="query"` with payload
field `comment_ids`, alongside `finish` and `finalize`. Its executor
(`src/nebuko/engine/fetch_comment_bodies_tool.py`) looks ids up in the per-run
cached comment bodies and wraps every returned body in the same
untrusted-content framing the unverified appendix uses: a full comment body is
untrusted third-party text, exactly like any other included comment, and is
treated as such by the consumer.
