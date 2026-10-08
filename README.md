# Nebuko

Nebuko is a AI multi-agent workflow as code — a schema and an engine — that lives where *you* are: in GitHub issues and runners.
It's foccused on:
- Easy to call agent using `@@` annotation: like "`@@genius` what do you think of this?" or "`@@review` PR #123"
- Auditable agent answer: agent answer like a teammate by comment in the issue + full execution stats as ependable comment
- Predictable and enforcement rules: create a workflow that put a review step mandatory avfter fev implementation, and loop until review is green

In futur we plan to support other git provider (GitLab, Codeberg, BitBucket, Gitea, etc.) and ticketing tool like Jira.

Built for **iterative dev** — small iterations, fast delivery, clean code, TDD — not for the "one big prompt builds an app" style.
The full story, including the three-layer model and the honest shipped-vs-planned bar, lives in [landscape.md](landscape.md).

## What is the product?

A new week, a new fantastic AI tool / process / skills / etc.
But how to integrate it easilly in your software factory to test it and easy remove it if it doesn't fit?
Devops already solve this with Infrastructure as code, let's use it for agent configuration, tooling and expend it to workflows.
It leave in your git repo, so you can test it and rollback it easilly.

Three layers, and this project owns Layer 2:

| Layer | What it is | Here |
|-------|-----------|------|
| **3 — AI workflow** | A workflow as code that explain how to do something with AI: code, review, analyze… And shareable. | `workflows/` |
| **2 — workflow spec + engine** | The wokflow as yaml definition and the full engine that runs it. | nebuko |
| **1 — AI agent runner** | Runs an individual agent with skill, tools, etc. | OpenHands SDK |

The payoff: a new Layer-3 capability — a workflow, an MCP, a websearch or context7 skill — is a **copy into your repo, try it, adopt or delete**. No stack update, no new SaaS, no migration.

And agents are **coworkers, not a black box**: they talk to you and each other in plain issue comments, so you see what they did and why.

## How it works

**Agents with a role and a personality** — defined as code in `agent-config.yaml` (provider, model + parameters, personality prompt file, skills, tools). Default agents are provided.

**Skills granted to agents** — you know them, skills, tools, MCPs, just use them as everywhere else.

**Workflows as code** — YAML that forces the AI onto a dedicated path, with `human_gate` approval points and conditional routing:

```yaml
- name: plan
  agent: genius
  human_gate: true          # a human must approve before anything is built
  conditions:
    - on_result: approved
      next: implement
    - on_result: rejected
      next: plan            # loop until human is happy

- name: implement
  agent: dev
  next: review
```

**Divide and conquer** — small dedicated agents bound long-context drift. When one finishes it posts what it did, found, and the next step, then can dispatch another agent. Every step is a comment you can read and redirect.

Exemple of agent answer:
<details>

*Ctx answer*

I have gathered comprehensive context. Here is my report.

---

## 🔍 Context Collection Report — workflow-not-found error not propagated to the GitHub comment (Issue #725)

The engine's `guards` phase returns a terminal outcome with `posted_body=None` when the resolver reports `SKIP_AGENT=true`, so the `/workflow '<name>'` reason set in `resolve_route.py` (`config_skip_reason` → `REASON`) never reaches `adapters/github.py:post()`. The resolver's failure detail is produced by `load_workflow_with_dirs` (`FileNotFoundError`, searched dirs) and carried through `build_result.py` into `{"SKIP_AGENT": "true", "REASON": ...}`. The legacy bash path (`legacy-flow.yaml`) did post this reason through a dedicated "Post Terminal Workflow Skip Comment" step using `peter-evans/create-or-update-comment`, guarded by `env.SKIP_AGENT == 'true' && env.REASON != ''`. The new engine has a hook for exactly this kind of notice (circuit-breaker warning posts via `self.adapter.post(...)`), plus a `CommentComposer` stub (issue #727), and `engine.run` returns `ExitCode.TERMINAL = 20` — matching the observed exit code 20. Search scope: engine package, adapters, `.github/scripts/resolve_config`, `.github/scripts/config/load_workflow`, GHA workflows, and 6 external references (GHA exit codes/troubleshooting, `peter-evans/create-or-update-comment`, GitHub REST issue comments, a workflow-not-found explainer, plus two blocked fetches).

<details>
<summary>FILES FOUND — 10 files covering the engine phase pipeline, the resolver's `/workflow` failure path, and the legacy terminal-skip comment mechanism.</summary>

### FILES FOUND
*10 files covering the engine phase pipeline, the resolver's `/workflow` failure path, and the legacy terminal-skip comment mechanism.*

- `engine/run_loop.py` — phase driver; `_resolver_skip_verdict` (L77–91) and the `guards` terminal return with `posted_body=None` (L216–231); `resolve()` bridge to legacy `resolve_config` (L313–338); `publish()` sink via `adapter.post` (L570–600); `main()` exit-code mapping (L804–834).
- `engine/models.py` — `ExitCode` enum (`TERMINAL = 20`, L62–69) and `RunOutcome` (L72–82).
- `.github/scripts/config/features/load_workflow/load_workflow.py` — `load_workflow` raises `FileNotFoundError` (L74–75); `load_workflow_with_dirs` raises the searched-dirs message (L136–138).
- `.github/scripts/resolve_config/features/resolve_route/resolve_route.py` — sets `wf_fail_reason` on `FileNotFoundError`/`ValueError` and stores `config_skip_reason` (L1105, L1143–1157, L1196–1200).
- `.github/scripts/resolve_config/features/build_result/build_result.py` — maps empty task to `{"SKIP_AGENT": "true", "REASON": config_skip_reason or "No valid task found"}` (L97–106).
- `.github/workflows/legacy-flow.yaml` — legacy "Post Terminal Workflow Skip Comment" step (`if: env.SKIP_AGENT == 'true' && env.REASON != '' && env.TARGET_NUMBER != '0'`, L577–597).
- `.github/workflows/nebuko-workflow.yaml` — single "Run orchestration engine" step (`uv run python -m engine.run`, L170–188).
- `adapters/base.py` — `PlatformAdapter.post(body) -> str` contract (L41–44).
- `adapters/github.py` — `GitHubAdapter.post` implementation (L90–95).
- `engine/comment_composer.py` — `CommentComposer` stub for the early workflow-state comment (issue #727) (L1–63).

</details>

<details>
<summary>RELEVANT SNIPPETS — `engine/run_loop.py` L77–91 — resolver skip verdict</summary>

### RELEVANT SNIPPETS

**`engine/run_loop.py` L77–91 — resolver skip verdict**
```python
77	def _resolver_skip_verdict(resolved: Any) -> dict[str, str] | None:
78	    """The resolver's own skip verdict, which outranks both runtime guards.
79	
80	    The bash exits as soon as ``SKIP_AGENT=true`` comes back from
81	    ``resolve_config`` (issue #458: a routine skip must never be flipped back
82	    to a run), and only evaluates the guards/breaker on a resolved task. Returns
83	    ``None`` when the resolver selected an agent, so the caller falls through to
84	    the runtime guards.
85	    """
86	    if isinstance(resolved, Mapping) and _is_true(resolved.get("SKIP_AGENT")):
87	        return {
88	            "SKIP_AGENT": "true",
89	            "REASON": str(resolved.get("REASON") or "resolver skipped the agent"),
90	        }
91	    return None
```

**`engine/run_loop.py` L209–231 — guards phase terminal return (`posted_body=None`)**
```python
209	            self._enter(phases, "resolve_config")
210	            resolved = self.resolve(trigger)
211	            self._run_resolved[_RUN_RESOLVED_KEY] = resolved
212	            history: Sequence[CommentEntry] = list(
213	                self.adapter.load_history(limit=self.history_limit)
214	            )
215	
216	            self._enter(phases, "guards")
217	            # The bash short-circuits on the resolver's SKIP_AGENT before the
218	            # loop guard and breaker ever run (issue #458); the circuit breaker
219	            # is gated on ``SKIP_AGENT == 'false'`` in the job too.
220	            verdict = _resolver_skip_verdict(resolved) or self.guard(trigger, history)
221	            if _is_true(verdict.get("SKIP_AGENT")):
222	                reason = str(verdict.get("REASON") or "agent skipped")
223	                log.info("[phase:guards] skipping agent — %s", reason)
224	                return RunOutcome(
225	                    exit_code=ExitCode.TERMINAL,
226	                    routing=RoutingDecision(status="hold", reason=reason),
227	                    prompt=None,
228	                    posted_body=None,
229	                    state=None,
230	                    phases=tuple(phases),
231	                )
```

**`engine/run_loop.py` L313–338 — resolver bridge (legacy `resolve_config`)**
```python
313	    def resolve(self, trigger: Trigger) -> Any:
314	        """Delegate to the legacy ``resolve_config`` result (memoized per run)."""
315	        key = (trigger.body, trigger.number)
316	        cached = self._resolve_cache.get(key)
317	        if cached is not None:
318	            return cached
319	
320	        ensure_import_path()
321	        from resolve_config.resolve_config import resolve_config
322	
323	        resolved = dict(
324	            resolve_config(
325	                trigger_body=trigger.body,
326	                prompt_base=str(self.repo_root / ".agents" / "prompts"),
327	                workflows_dir=str(self.repo_root / "workflows"),
328	                repo=self._repo(trigger),
329	                issue_number=trigger.number or None,
330	                debug=_is_true(self.env.get("CONFIG_DEBUG")),
331	            )
332	        )
```

**`engine/run_loop.py` L570–600 — publish sink (the existing adapter post path)**
```python
570	    def publish(self, agent_result: Any) -> str:
571	        """Run the legacy publish chain, then post through the adapter.
...
580	        ensure_import_path()
581	        from prepare_comment import build_comment_body, escape_agent_triggers
...
586	        body = build_comment_body(
...
594	        )
...
598	        escaped = escape_agent_triggers(body)
599	        self.adapter.post(escaped)
600	        return escaped
```

**`engine/run_loop.py` L390–400 — existing adapter.post from a guard verdict (circuit-breaker warning)**
```python
390	        if _is_true(verdict.get("CIRCUIT_BREAKER_TRIPPED")):
391	            verdict["SKIP_AGENT"] = "true"
392	            verdict.setdefault(
393	                "REASON",
394	                f"Circuit breaker tripped ({verdict.get('AGENT_CALLS')} agent calls)",
395	            )
396	            self.adapter.post(
397	                circuit_breaker_warning(
398	                    int(verdict.get("AGENT_CALLS") or 0), self._threshold()
399	                )
400	            )
401	        return verdict
```

**`engine/run_loop.py` L804–834 — main exit-code mapping**
```python
804	def main(argv: Sequence[str] | None = None) -> int:
805	    """Select the adapter, run once, map ``RunOutcome.exit_code`` to the process code."""
...
832	    adapter = build_adapter(args.adapter, **kwargs)
833	    outcome = Engine(adapter=adapter, repo_root=repo_root(), env=dict(os.environ)).run()
834	    return int(outcome.exit_code)
```

**`engine/models.py` L62–82 — ExitCode / RunOutcome**
```python
62	# @stub-protected
63	class ExitCode(IntEnum):
64	    """Process exit codes distinguishing the three terminal outcomes."""
65	
66	    ADVANCE = 0  # next step proceeds
67	    ERROR = 1  # uncaught failure / contract violation
68	    HOLD = 10  # human gate: stop, human resumes
69	    TERMINAL = 20  # terminal journal marker written
70	
71	
72	# @stub-protected
73	@dataclass(frozen=True)
74	class RunOutcome:
75	    """Everything one engine run produced, including the routing decision."""
76	
77	    exit_code: ExitCode
78	    routing: RoutingDecision
79	    prompt: str | None
80	    posted_body: str | None
81	    state: WorkflowState | None
82	    phases: tuple[str, ...]  # ordered "[phase:*]" markers actually emitted
```

**`.github/scripts/config/features/load_workflow/load_workflow.py` L61–85 and L129–138 — the raise sites**
```python
61	def load_workflow(path: str) -> WorkflowDefinition:
...
70	    Raises:
71	        FileNotFoundError: If the workflow file does not exist.
72	        ValueError: If the config fails validation or cannot be parsed.
73	    """
74	    if not os.path.isfile(path):
75	        raise FileNotFoundError(f"Workflow file not found: {path}")
...
129	    searched: list[str] = []
130	    for d in dirs:
131	        candidate = Path(d) / f"{wf_name}.yaml"
132	        searched.append(str(candidate))
133	        if candidate.is_file():
134	            load_workflow(str(candidate))
135	            return candidate
136	    raise FileNotFoundError(
137	        f"Workflow file not found: '{wf_name}.yaml' (searched: {searched})"
138	    )
```

**`.github/scripts/resolve_config/features/resolve_route/resolve_route.py` L1103–1157 — `wf_fail_reason` capture**
```python
1103	    wf_resolved = False
1104	    task = ""
1105	    wf_fail_reason = ""
...
1143	        except FileNotFoundError as e:
1144	            # Multi-dir miss: the exception message enumerates every path
1145	            # searched (issue #529).
1146	            print(f"Warning: {e}, falling back to @@​mention", file=sys.stderr)
1147	            wf_fail_reason = f"/workflow '{wf_name}': {e}"
1148	        except ValueError as e:
1149	            # A schema-invalid file (local override or built-in) still wins —
1150	            # fail loudly instead of silently falling back to another file of
1151	            # the same name (issue #529).
1152	            print(
1153	                f"Warning: workflow '{wf_name}' is schema-invalid ({e}); "
1154	                f"no silent fallback to another directory",
1155	                file=sys.stderr,
1156	            )
1157	            wf_fail_reason = f"/workflow '{wf_name}': schema-invalid ({e})"
```

**`.github/scripts/resolve_config/features/resolve_route/resolve_route.py` L1196–1200 — `config_skip_reason` emission**
```python
1196	    # Diagnostic for the terminal skip comment when /workflow failed and no
1197	    # mention fallback produced a task (issue #529).
1198	    if wf_fail_reason and not task:
1199	        result["config_skip_reason"] = wf_fail_reason
1200	    return result
```

**`.github/scripts/resolve_config/features/build_result/build_result.py` L97–106 — SKIP_AGENT/REASON mapping**
```python
97	    task = config.get("task", "")
98	    if not task:
99	        # Enriched reason: carries /workflow failure detail (name + searched
100	        # directories) when available (issue #529).
101	        skip_reason = config.get("config_skip_reason") or "No valid task found"
102	        return {"SKIP_AGENT": "true", "REASON": skip_reason}
103	
104	    # Validate task
105	    if task not in TASK_TO_PROMPT:
106	        return {"SKIP_AGENT": "true", "REASON": f"Unknown task: {task}"}
```

**`.github/workflows/legacy-flow.yaml` L577–597 — "reuse" mechanism: terminal skip comment**
```yaml
577	            # Only posted when the resolver produced a skip REASON (terminal
578	            # step / resolution failure). Routine guard skips (same-agent loop,
579	            # human ping) do not export REASON and remain silent as before.
580	            - name: Post Terminal Workflow Skip Comment
581	              if: env.SKIP_AGENT == 'true' && env.REASON != '' && env.TARGET_NUMBER != '0'
582	              uses: peter-evans/create-or-update-comment@v4
583	              with:
584	                issue-number: ${{ env.TARGET_NUMBER }}
585	                token: ${{ secrets.GH_PAT }}
586	                tag: "${{ env.BOT_MARKER }}"
587	                mode: create-or-update
588	                body: |
589	                  ${{ env.BOT_MARKER }}
590	                  ⏹️ **Workflow stopped** — no agent was run for this trigger.
591	
592	                  **Reason:** ${{ env.REASON }}
593	
594	                  This comment intentionally carries no workflow state and does not trigger further agent runs.
595	                  To continue, reply with an explicit trigger (e.g. `@@ genius`).
596	
597	                  🔗 [View workflow logs](${{ github.server_url }}/${{ github.repository }}/actions/runs/${{ github.run_id }})
```

**`.github/workflows/nebuko-workflow.yaml` L170–188 — single engine step (no separate comment step)**
```yaml
170	            # Stage B (issue #713): the orchestration that used to live in ~30
171	            # bash steps (normalize → resolve_config → guards → prompt → agent →
172	            # validate → publish → state → route) is now the single Stage A
173	            # engine. This job only wires the GitHub host and invokes it once.
174	            - name: Run orchestration engine
175	              run: uv run python -m engine.run
176	              env:
177	                ENGINE_ADAPTER: github
178	                GFA_SOURCE: github
179	                GH_TOKEN: ${{ secrets.GH_PAT }}
180	                GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
181	                AGENT_CONFIG_PATH: ${{ inputs.agent_config_path || 'agent-config.yaml' }}
```

**`adapters/base.py` L41–44 and `adapters/github.py` L90–95 — post contract/impl**
```python
41	    # @stub-protected
42	    def post(self, body: str) -> str:
43	        """Publish ``body`` as a comment; return its id/url."""
44	        ...
```
```python
90	    # @stub-protected
91	    def post(self, body: str) -> str:
92	        result = self._client_handle().post_comment(self._require_repo(), self._issue_number(), body)
93	        if isinstance(result, dict):
94	            return str(result.get("html_url") or result.get("id") or "")
95	        return str(result or "")
```

**`engine/comment_composer.py` L1–63 — CommentComposer stub (early workflow-state comment, issue #727)**
```python
1	"""Compose the early, pre-agent workflow-state comment (issue #727).
...
21	class CommentComposer:
...
31	    BOT_MARKER: str = "<!-- bot-workflow-terminal -->"
...
38	    # @stub-protected: signature defined by architect — do not modify
39	    def continue_token(self, state: "WorkflowState") -> str:
...
48	    # @stub-protected: signature defined by architect — do not modify
49	    def compose_run_start(
50	        self,
51	        state: "WorkflowState",
52	        *,
53	        caller_marker: str,
54	        display_name: str,
55	    ) -> str:
...
63	        raise NotImplementedError
```

</details>

<details>
<summary>EXTERNAL REFERENCES — 6 external references: GHA exit-code semantics, GHA troubleshooting, the comment action used by the legacy mechanism, the REST comment endpoint, a workflow-not-found explainer, and 2 fetches that were blocked (kept with their snippet).</summary>

### EXTERNAL REFERENCES
*6 external references: GHA exit-code semantics, GHA troubleshooting, the comment action used by the legacy mechanism, the REST comment endpoint, a workflow-not-found explainer, and 2 fetches that were blocked (kept with their snippet).*

**Setting exit codes for actions — GitHub Docs**
**URL:** https://docs.github.com/en/actions/how-tos/create-and-publish-actions/set-exit-codes
**Summary:** Confirms a nonzero process exit marks the step/action as `failure`; relevant because `engine.main` returns `int(outcome.exit_code)` (20 = `TERMINAL`), which is precisely what fails the GHA step before any comment step could run.
**Score:** 0.5

**Troubleshooting workflows — GitHub Docs**
**URL:** https://docs.github.com/en/actions/how-tos/troubleshoot-workflows
**Summary:** General GHA debugging guidance (run logs, debug logging); background on where a swallowed message would/would not surface.
**Score:** 0.3

**peter-evans/create-or-update-comment (README)**
**URL:** https://github.com/peter-evans/create-or-update-comment/blob/main/README.md
**Summary:** Documents the composite action the legacy terminal-skip step uses (`body`, `issue-number`, `token`, and the `tag`/`mode` inputs described for the create-or-update variant); the reference implementation of the "similar mechanism" to reuse.
**Score:** 0.7

**Create an issue comment — GitHub REST API**
**URL:** https://docs.github.com/en/rest/issues/comments?apiVersion=2022-11-28
**Summary:** The REST endpoint `POST /repos/{owner}/{repo}/issues/{issue_number}/comments` (201 Created) that `adapters/github.py:post` ultimately calls via the client handle.
**Score:** 0.6

**GitHub Actions Workflow Not Found: Check YAML Syntax**
**URL:** https://adhdecode.com/debugging/github-actions/workflow-not-found-check-yaml-syntax/
**Summary:** Explains how "Workflow not found" is surfaced by GitHub and that it frequently indicates an unrecoverable YAML/parse failure early in a run; useful framing for the user-facing message wording.
**Score:** 0.5

**GitHub Actions runner passes even exit code is not 0 (Stack Overflow)**
**URL:** https://stackoverflow.com/questions/73293404/github-actions-runner-passes-even-exit-code-is-not-0
**Summary:** Fetch failed (Cloudflare interstitial returned only "Just a moment..."), so only the search snippet is available; kept for completeness on exit-code/step-status semantics.
**Score:** 0.2

**"workflow was not found" when trying to call reusable workflow — GitHub Community Discussion #70251**
**URL:** https://github.com/orgs/community/discussions/70251
**Summary:** Fetch returned only the page title (JS-rendered body not retrievable); relevant topically to workflow-not-found wording but no substantive text extracted.
**Score:** 0.2

```json
[
  {
    "url": "https://docs.github.com/en/actions/how-tos/create-and-publish-actions/set-exit-codes",
    "date": "2026-10-02T09:52:20Z",
    "chunks": [
      "About exit codes\n\nGitHub uses the exit code to set the action's check run status, which can be success or failure.\n\nExit status | Check run status | Description\n\n0 | success | The action completed successfully and other tasks that depend on it can begin.\n\nNonzero value (any integer but 0) | failure | Any other exit code indicates the action failed. When an action fails, all concurrent actions are canceled and future actions are skipped. The check run and check suite both get a failure status."
    ],
    "summary": "Confirms a nonzero process exit marks the action as failure; relevant because engine.main returns int(outcome.exit_code) (20 = TERMINAL), matching the observed failing step.",
    "score": 0.5
  },
  {
    "url": "https://docs.github.com/en/actions/how-tos/troubleshoot-workflows",
    "date": "2026-10-02T09:52:20Z",
    "chunks": [
      "Initial troubleshooting suggestions\n\nThere are several ways you can troubleshoot failed workflow runs.\n\nUsing GitHub Copilot\n\nTo open a chat with GitHub Copilot about a failed workflow run, you can either: Next to the failed check in the merge box, click , then click Explain error. In the merge box, click on the failed check. At the top of the workflow run summary page, click Explain error.\n\nUsing workflow run logs\n\nEach workflow run generates activity logs that you can view, search, and download. For more information, see Using workflow run logs."
    ],
    "summary": "General GHA debugging guidance on run logs and debug logging; background on where a message would or would not surface.",
    "score": 0.3
  },
  {
    "url": "https://github.com/peter-evans/create-or-update-comment/blob/main/README.md",
    "date": "2026-10-02T09:52:20Z",
    "chunks": [
      "## Usage\n\n### Add a comment to an issue or pull request\n\n```yml\n      - name: Create comment\n        uses: peter-evans/create-or-update-comment@v5\n        with:\n          issue-number: 1\n          body: |\n            This is a multi-line test comment\n            - With GitHub **Markdown** :sparkles:\n            - Created by [create-or-update-comment][1]\n\n            [1]: https://github.com/peter-evans/create-or-update-comment\n          reactions: '+1'\n```",
      "### Action inputs\n\n| Name | Description | Default |\n| --- | --- | --- |\n| `token` | `GITHUB_TOKEN` (`issues: write`, `pull-requests: write`) or a `repo` scoped [PAT](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/creating-a-personal-access-token). | `GITHUB_TOKEN` |\n| `repository` | The full name of the repository in which to create or update a comment. | Current repository |\n| `issue-number` | The number of the issue or pull request in which to create a comment. | |\n| `comment-id` | The id of the comment to update. | |\n| `body` | The comment body. Cannot be used in conjunction with `body-path`. | |\n| `body-path` | The path to a file containing the comment body. Cannot be used in conjunction with `body`. | |\n| `edit-mode` | The mode when updating a comment, `replace` or `append`. | `append` |\n| `append-separator` | The separator to use when appending to an existing comment. (`newline`, `space`, `none`) | `newline` |"
    ],
    "summary": "Documents the composite action the legacy terminal-skip step uses (body/issue-number/token plus tag/mode for create-or-update); the reference mechanism to reuse.",
    "score": 0.7
  },
  {
    "url": "https://docs.github.com/en/rest/issues/comments?apiVersion=2022-11-28",
    "date": "2026-10-02T09:52:20Z",
    "chunks": [
      "Create an issue comment\n\nYou can use the REST API to create comments on issues and pull requests. Every pull request is an issue, but not every issue is a pull request.\n\nThis endpoint triggers notifications. Creating content too quickly using this endpoint may result in secondary rate limiting.",
      "HTTP response status codes for \"Create an issue comment\"\n\nStatus code | Description\n\n201 | Created\n\n403 | Forbidden\n\n404 | Resource not found\n\n410 | Gone\n\n422 | Validation failed, or the endpoint has been spammed."
    ],
    "summary": "The REST endpoint POST /repos/{owner}/{repo}/issues/{issue_number}/comments (201 Created) that adapters/github.py:post ultimately invokes.",
    "score": 0.6
  },
  {
    "url": "https://adhdecode.com/debugging/github-actions/workflow-not-found-check-yaml-syntax/",
    "date": "2026-10-02T09:52:20Z",
    "chunks": [
      "What This Error Actually Means\n\nThat red \u201cWorkflow not found\u201d message in your GitHub Actions run log. It\u2019s the kind of error that stops everything, and often feels\u2026wrong. You know the workflow file is there. You\u2019ve checked the path a dozen times. It\u2019s not a permissions issue. So why is GitHub Actions acting like your automation simply doesn\u2019t exist?\n\nThis error, despite its deceptively simple phrasing, rarely indicates a missing file. Instead, it almost universally points to a YAML parsing failure before GitHub Actions even attempts to execute your workflow.",
      "This commonly manifests in a few key scenarios. The first, and most frequent, is incorrect indentation. YAML uses indentation to define hierarchy, unlike languages that rely on braces or keywords. A single misplaced space, a tab instead of spaces, or inconsistent indentation levels will immediately break the parser."
    ],
    "summary": "Explains how 'Workflow not found' is surfaced by GitHub and that it often indicates an unrecoverable YAML/parse failure early in a run; useful framing for the user-facing message wording.",
    "score": 0.5
  },
  {
    "url": "https://stackoverflow.com/questions/73293404/github-actions-runner-passes-even-exit-code-is-not-0",
    "date": "2026-10-02T09:52:20Z",
    "chunks": [
      "Just a moment..."
    ],
    "summary": "Fetch failed (Cloudflare interstitial); only the failure notice was retrievable. Kept for completeness on exit-code/step-status semantics.",
    "score": 0.2
  },
  {
    "url": "https://github.com/orgs/community/discussions/70251",
    "date": "2026-10-02T09:52:20Z",
    "chunks": [
      "\"workflow was not found\" when trying to call reusable workflow \u00b7 community \u00b7 Discussion #70251 \u00b7 GitHub"
    ],
    "summary": "Fetch returned only the page title (JS-rendered body not retrievable); relevant topically to workflow-not-found wording but no substantive text extracted.",
    "score": 0.2
  }
]
```

</details>

<details>
<summary>OTHER FILES — Files examined that did not bear directly on the workflow-not-found propagation path.</summary>

### OTHER FILES
*Files examined that did not bear directly on the workflow-not-found propagation path.*

- `engine/agent_runner.py`, `engine/paths.py`, `engine/__init__.py`, `engine/run.py`, `engine/__main__.py` — entry points/bootstrap; `run.py` and `__main__.py` both delegate to `engine.run_loop.main`.
- `adapters/local.py` — local adapter variant, not used by the GitHub-hosted failing run.
- `.github/workflows/comment-flow.yaml` — comment-triggered workflow, unrelated to the `/workflow run` engine path.
- `AGENTS.md`, `README.md`, `landscape.md`, `latest_answer.md`, `console.log` — repo-level docs/logs; no additional engine-error handling found.
- `.agents/prompts/` — agent prompt templates; not involved in error propagation.

</details>

</details>

**Workflow state in comments** — LLMs are stateless, so every comment embeds a JSON state (current step, history, PR, dispatch). That's what the engine uses to pick the next action — and it's visible, so you know how it works under the hood.
<details>
{
  "version": 4,
  "workflow": "spec-driven-impl",
  "step": "context",
  "agent": "ctx",
  "phase": "analysis",
  "history": [
    {
      "step": "analyze",
      "agent": "genius",
      "summary": "Delegated context collection to @@ctx for the workflow-not-found error-propagation bug. Next: produce the Phase A architecture proposal (with Object Design Anchor) once context returns.",
      "position": [
        {
          "workflow": "spec-driven-impl",
          "step": "analyze"
        }
      ],
      "dispatch": {
        "task_hint": "ctx",
        "reason": "First pass on a new bug report; need engine phase pipeline, workflow-not-found emission point, and the existing error-to-comment mechanism before drafting an architecture proposal."
      },
      "comment_ids": [
        5949457387
      ]
    },
    {
      "step": "context",
      "agent": "ctx",
      "summary": "I have gathered comprehensive context. Here is my report.\n\n---\n\n## \ud83d\udd0d Context Collection Report \u2014 workflow-not-found error not propagated to the GitHub comment (Issue #725)\n\nThe engine's `guards` phase returns a terminal outcome with `posted_body=None` when the resolver reports `SKIP_AGENT=true`, so ",
      "position": [
        {
          "workflow": "spec-driven-impl",
          "step": "context"
        }
      ]
    }
  ],
  "metrics": {
    "run_id": "26070ff7-b52f-4c16-b0e1-b3b81f885eda",
    "model_name": "default",
    "agent_role": "ctx",
    "input_tokens": 4147645,
    "output_tokens": 54579,
    "tool_call_latency_ms": 5350.578029579092,
    "agent_runtime_ms": 572511.85,
    "nb_tool_calls": 180,
    "tool_call_error_rate": 2.2222222222222223
  },
  "current_context": {
    "last_run_status": "success"
  },
  "issue": {
    "number": 725,
    "url": "https://github.com/olivier-norture/git-flow-ai/issues/725",
    "title": "bug: don't report workflow not found error",
    "author": "olivier-norture",
    "repo": "olivier-norture/git-flow-ai"
  },
  "resolution": {
    "task": "ctx",
    "input_workflow": "spec-driven-impl",
    "input_step": "context",
    "input_complexity": "medium",
    "role_name": "ctx",
    "prompt_location": ".agents/prompts/ctx.md",
    "agent_selected": "true",
    "selected_workflow": "spec-driven-impl",
    "selected_step": "context",
    "next_step": "plan",
    "agent_skills": "call-agent,posting-contract",
    "reasoning": "none",
    "timestamp": "2026-10-02T09:53:34.068242+00:00"
  },
  "position": [
    {
      "workflow": "spec-driven-impl",
      "step": "context"
    }
  ]
}
</details>

**Automatic complexity management** - Allow agent to increase or decrease task complexity to use cheaper LLM or move to frontier model to provide best architecture.
**Split tasks** - Let agent create sub-workflow to decompose the plan in multiple dev/review steps. 

## Quick start

Runs on GitHub-hosted runners by default; pass `runs_on: "self-hosted"` to use your own [self-hosted runner](https://docs.github.com/en/actions/hosting-your-own-runners/managing-self-hosted-runners/adding-self-hosted-runners).

1. **Add the reusable workflow** to `.github/workflows/`:

   ```yaml
   jobs:
     agent:
       uses: nebuko/nebuko/.github/workflows/nebuko-workflow.yaml@main
       with:
         agent_config_path: "agent-config.yaml"
       secrets:
         OPENROUTER_API_KEY: ${{ secrets.OPENROUTER_API_KEY }}
         GH_PAT: ${{ secrets.GH_PAT }}
   ```

2. **Add `agent-config.yaml`** at your repo root — see [this repo's config](./agent-config.yaml) for the reference.
Or copy an existing one.

3. **Add secrets**: `OPENROUTER_API_KEY` (your LLM key) and `GH_PAT` (a PAT with `repo` and `workflow` scopes).

4. **Start using**: comment `/nebuko run workflow --name default` on an issue, or mention `@@genius`, `@@ctx`, `@@dev` or `@@review` directly.

> 💡 **Zero-copy setup**: workflows, prompts and scripts are fetched from the nebuko repo on every run — you only need `agent-config.yaml` locally. Define custom workflows by adding a local `workflows/` directory (local files take precedence).

## Configuration basics

One layered file, `agent-config.yaml`, in three layers — `[docs/configuration.md](docs/configuration.md)` covers the depth:

| Layer | Purpose | Example |
|-------|---------|---------|
| **Providers** | API endpoint + auth env var | `openrouter` |
| **LLMs** | Model, provider, sampling params | `qwen-3-6-27b` |
| **Agents** | Role → model + prompt file + skills + tools | `genius`, `dev`, `ctx`, … |

Cross-references resolve with `${ref}` (`${llms.qwen-3-6-27b}`). Sampling parameters cascade per-invocation > per-agent > per-LLM, and a `complexity` field auto-adjusts temperature (`low` 0.0 → `high` 0.3, never decreasing the agent's floor). Adding an agent is a ~5-line stanza — no workflow changes needed. If `agent-config.yaml` is absent, the workflow falls back to the legacy env-var approach.

Already paying for a coding subscription? You can point the agents at **CommandCode** (and, later, **OpenCode Go**) instead of a metered API key — see [docs/subscription-plans.md](docs/subscription-plans.md).

For the machine contracts (`AGENT_RUN_CONFIG` sentinel, dispatch statuses, workflow grammar, state schema) see [docs/reference.md](docs/reference.md); for the full agent roster and tool permissions see [docs/agents-and-skills.md](docs/agents-and-skills.md).