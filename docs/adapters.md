# Platform adapters

How to connect a hosting platform (GitHub, GitLab, Codeberg, Jira, a local
directory, ...) to the nebuko engine, and what the engine expects from it.

## Overview

`src/nebuko/adapters/base.py::PlatformAdapter` is the **single platform seam**
(issue #590, Stage A). The engine is platform-free: `nebuko.engine.*` speaks only
the types defined in the port module — `Trigger`, `CommentEntry` (engine side),
and, since the port redesign, `PostedComment`, `PosterCapabilities` and
`HistoryPage`. A host implements the port once; the engine never learns which
platform it is talking to.

## Port surface reference

Every adapter implements the required methods; the opt-in methods may raise
`NotImplementedError` until the platform needs them.

| Method | Return | Required |
|---|---|---|
| `read_trigger()` | `Trigger` | yes |
| `load_history(limit=None)` | `Sequence[CommentEntry]` | yes |
| `post(body)` | `PostedComment` | yes |
| `capabilities()` | `PosterCapabilities` | yes |
| `load_state()` / `save_state(state)` | `WorkflowState \| None` / `None` | yes |
| `delete_issue_comments(issue, from_comment="")` | `int` | yes |
| `artifact_dir()` / `write_artifact(name, content)` | `Path` / `None` | yes |
| `edit_comment(comment_id, body)` | `PostedComment` | opt-in |
| `add_reaction(comment_id, emoji)` | `None` | opt-in |
| `load_history_page(cursor=None)` | `HistoryPage` | opt-in |

### `PostedComment`

The typed result of `post()` — callers no longer parse an untyped id/url
string:

```python
class PostedComment(BaseModel):
    comment_id: str
    permalink: Optional[str] = None   # human-readable URL, when the platform has one
    anchor: Optional[str] = None      # in-page anchor, when distinct from the permalink
    author: Optional[str] = None
    created_at: Optional[str] = None  # ISO-8601, when the platform reports it
```

On GitHub, `permalink` and `anchor` are the same `html_url`; platforms with
separate in-page anchors (e.g. GitLab note anchors) populate them differently.

### `PosterCapabilities`

A frozen model declaring what the platform's comment renderer supports:

| Flag | Default | Meaning |
|---|---|---|
| `markdown` | `True` | Markdown is rendered at all |
| `details` | `True` | `<details>` collapsible blocks render |
| `tasklist` | `True` | `- [ ]` task lists render |
| `comment_anchor` | `True` | comments have stable in-page anchors |
| `edit_comment` | `False` | `edit_comment` is implemented |
| `reactions` | `False` | `add_reaction` is implemented |
| `history_pagination` | `False` | `load_history_page` is implemented |
| `raw_html` | `False` | arbitrary raw HTML survives rendering |

Declare honestly: a flag claims a capability the platform actually has, not one
you wish it had.

### `HistoryPage`

One page of paginated history: `entries` (untyped this round — refined when
the first paginating adapter lands) and `next_cursor` (`None` when exhausted).

## Writing a new adapter

Checklist:

1. Implement every required method above, plus `capabilities()`.
2. Return `PostedComment` from `post()` — populate at least `comment_id`.
3. Declare honest capability flags in `capabilities()`.
4. Add the opt-in methods you do not support yet as explicit
   `raise NotImplementedError(...)` one-liners, so the port surface stays
   uniform and callers fail loudly instead of silently.
5. Extend `PORT_METHODS` and the conformance tests in
   `tests/engine/test_platform_adapter.py` (see below).

## Comment rendering and degradation

Engine renderers must not emit markup a platform cannot render. They build a
`CommentRenderer` from the adapter's capabilities once, then route the
assembled body through it (`nebuko/adapters/rendering.py`):

```python
from nebuko.adapters.rendering import CommentRenderer

renderer = CommentRenderer(self.adapter.capabilities())
body = renderer.render(assembled_body)
```

Degradation rules: when `details` is `False`, `<details>` blocks are unwrapped
to their content with the `<summary>` kept as a heading line; when `tasklist`
is `False`, `- [ ]` / `- [x]` markers become plain `-` bullets; raw HTML is
escaped unless `raw_html` is `True`.

Before (GitHub, all capabilities on):

```
<details>
<summary>Cost breakdown</summary>

- [x] measured
- [ ] estimated
</details>
```

After (Jira, `details=False, tasklist=False`):

```
Cost breakdown

- measured
- estimated
```

## Step-output anchoring

Each step's output *is* the comment that posts it. The engine records a
`StepOutputRecord` (`step`, `comment_id`, plus `permalink`, `anchor`,
`posted_at` taken from the `PostedComment`) in the workflow state under
`current_context["step_outputs"]`. `upsert_into` is **latest write wins**: a
re-run of the same step replaces the existing locator for that step, so state
always points at the newest version of the comment. On state restore, the
engine resolves the locator against the bounded thread read to find the prior
comment's body.

## Testing your adapter

Copy the conformance pattern from `tests/engine/test_platform_adapter.py`:

- every name in `PORT_METHODS` exists on your adapter class;
- `post()` returns a `PostedComment` instance with a non-empty `comment_id`;
- `capabilities()` returns a `PosterCapabilities` instance;
- unsupported opt-in methods raise `NotImplementedError` when called;
- a degradation test drives `CommentRenderer` with reduced capabilities over a
  details/task-list body and asserts plain-text output.
