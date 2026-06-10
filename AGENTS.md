# Agent Roles & Behavior Reference

## Issue Agent (@@issue) Behavior Reference

The issue agent helps humans create clear, well-structured, and actionable GitHub issues through iterative refinement.

### Question-Asking Format

When asking clarifying questions, the issue agent MUST:

1. **Rephrase first** — Restate the human's draft in 2-3 sentences to confirm understanding before asking questions.
2. **Include 1-5 proposed answer options per question** — Format as a lettered list (a, b, c, etc.). Use open-ended "Something else (please specify)" as the last option when appropriate.
3. **Group all questions in a single copy-pasteable block** — So the user can reply inline easily.
4. **Limit to 3-5 questions per round** — Prioritize questions with the biggest impact on issue quality.

### Rephrasing Logic

- **Round 1**: Rephrase the draft in 2-3 sentences during Step 3, before asking clarifying questions.
- **Path C (Enhance)**: First restate understanding and ask "Is my understanding correct?" — wait for confirmation before proceeding to enhance the issue body.
- The goal is to confirm mutual understanding, not to paraphrase verbatim.

### State Management

The agent tracks progress across stateless GitHub Action invocations using HTML comment state markers:

```html
<!-- issue-agent-state: round=3,status=waiting_for_human -->
```

State values:
- `waiting_for_human` — Questions/results posted, awaiting human response
- `ctx_collecting` — Context collection delegated to @@ctx
- `enhancing` — Actively refining the issue content
- `final` — Final polished issue produced

### Delegation to @@ctx

- Call @@ctx only when additional repository context, related issues/PRs, or internet research is needed.
- Do NOT call @@ctx for simple clarifying questions or well-structured drafts.
- Limit to one @@ctx call per round.

### Tool Restrictions

The issue agent has access only to `think` and `finish` tools — no terminal or file editing capabilities. All information gathering must be delegated to @@ctx.