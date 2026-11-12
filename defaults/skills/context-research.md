---
description: "Read-only research playbook: how to search, how deep to go, and the report template."
---

# Context Research Skill

Playbook for @@context: how to search, how deep to go, and the strict report template.

## Search Strategies

Use only what the request needs; batch commands and bound output (`sed -n`, `head`).

**Use `terminal_batch`, not `terminal`.** It takes a `commands: list[str]` and runs each entry in order in one persistent shell, so a multi-step explore costs one model turn instead of one per command. Each entry must be a single statement (a chain joined by `;`/`&&`/`|` is one statement) — a newline is rejected, so split those into separate entries. `cd` carries across entries. Example: `commands=["cd src", "ls", "grep -rn 'def main' . | head -20"]`.

* **Code:** `grep -r` for keywords, function/class names and patterns; `find` for files by name or type; `read` for contents (use `offset`/`limit` for large files). Always record path and line numbers.
* **GitHub:** `gh issue list` / `gh pr list` to find related items, `gh issue view <n>` / `gh pr view <n>` to read them, `gh api /repos/<owner>/<repo>/issues/<n>/comments` for threads. Report URLs with relevant excerpts.
* **Docs:** README, AGENTS.md, ARCHITECTURE.md, CONTRIBUTING.md and inline docs. Report file paths and section titles.

### Reading a web reference

Never rely on the search snippet alone. For each selected reference:

1. Fetch the page, then convert it to text. Prefer a URL→text tool if the environment has one; otherwise:
   ```bash
   curl -sL --max-time 30 -A "Mozilla/5.0" "<url>" -o ref.html
   python3 -c "
   import re,sys
   h=open('ref.html',encoding='utf-8',errors='replace').read()
   h=re.sub(r'(?is)<(script|style|nav|header|footer|aside|noscript)\b.*?</\1>','',h)
   h=re.sub(r'(?i)</?(p|div|h[1-4]|li|tr|pre|br|section|article)\b[^>]*>','\n',h)
   t=re.sub(r'\n{3,}','\n\n',re.sub(r'[ \t]+',' ',re.sub(r'<[^>]+>','',h)))
   print(t.strip()[:200000])"
   ```
   Never use paid conversion services (e.g. `curl.md`).
2. Read the text, then keep only the verbatim passages that bear on the request: **≤ ~8,000 chars per reference**.
3. Write a 1–3 sentence summary of how it relates to the request, and a `0.0`–`1.0` score for relevance (not source credibility).
4. If the fetch fails (timeout, 4xx/5xx, no readable text), keep the reference with the search snippet as its only chunk, say so in the summary, and score it low.

## Exploration Depth

Read the level from the `COMPLEXITY` environment variable, or infer it from the request. Default is MEDIUM.

| Level | Repo search | Web references (each fully read) |
|---|---|---|
| LOW | 1–2 targeted `grep`/`find`; locate the file(s) and stop | 3 |
| MEDIUM | Likely directories plus adjacent files and related modules | 5–6 |
| HIGH | 5–8 progressively broader searches; trace dependencies, module boundaries and tests | up to 10, including recent work, alternatives and benchmarks |

Do not investigate failures that already exist on the base branch.

## Evidence rules

Every factual statement in the report carries one evidence type:

| Type | Means | Counts as proof of |
|---|---|---|
| `measured` | You ran a command and quote its output | timing, performance, runtime behaviour, what actually gets loaded or called |
| `read` | You read it in a file, log or page and cite `path:line` or URL | what the code or doc says |
| `inferred` | You deduced it without running or reading it directly | nothing: it is a lead for @@architect, not evidence |

1. **Measure claims about time or behaviour.** Reading code is only `inferred` evidence for "X is slow", "Y is loaded at startup" or "Z is called before W". Run the cheapest experiment that settles it, and quote the command and its output. For example: `python -X importtime -c "import pkg"`, a timed run, `python -c "import pkg, sys; print('mod' in sys.modules)"`, a small repro, `git log -S`, or timestamps from a CI log.
2. **Run the experiment before reading widely.** One timing run usually tells you which files deserve reading.
3. **Measuring stays read-only.** Never modify tracked files. Send generated artifacts to a scratch directory (`PYTHONPYCACHEPREFIX`, `--out`, `$TMPDIR`), and do not install or upgrade dependencies.
4. **Compare conditions when they differ.** When results depend on conditions (cold vs warm cache, with vs without a flag), measure each condition and report both numbers.
5. **Say what you could not verify** (`### NOT VERIFIED`), and report relevant facts nobody asked for (`### SURPRISES`). The request only covers what @@architect knew to ask.

### Stop rule and cheapest first

1. **List the deltas.** Every difference the request reports is one delta (counts, versions, timings, SHAs, …). Name each with a short id.
2. **Measure cheapest first.** Compare identifiers before running anything heavy: tested SHAs, tool versions, collected test IDs, config. Then targeted runs of the specific failing IDs. A full suite is the last resort; run each long job at most once and launch independent ones in one batch, then poll once. Read only what a measurement needs.
3. **Stop rule.** Once ONE measured reproduction matches the reported numbers, stop investigating. Keep going only for a delta that still has no verdict.
4. **Report the match.** State under `### WHAT THE MEASUREMENTS SHOW` which reported deltas you reproduced and by what command. That is fact-reporting, not analysis: the conclusion stays with @@architect.

## Claim verification (issue #1250)

When the request carries claim IDs (C1, C2, …), open the report with a verdict per claim, **before** `### FILES FOUND`:

```
### CLAIM VERIFICATION
| ID | Verdict | Evidence type | Evidence (URL/path:line/command, fetched date) | Verbatim excerpt |
|---|---|---|---|---|
| C1 | confirmed | read | https://…, 2026-05-01 | "…" |
| C2 | refuted | measured | `python -X importtime -c "import pkg"` | "… 5.35s openhands.sdk" |
```

Verdicts: `confirmed`, `refuted`, `contested`, `inconclusive`. Evidence types follow the Evidence rules above. A verdict that rests only on `inferred` evidence is `inconclusive`. Ask for evidence fields, not yes/no answers ("list maintained libraries with latest version and date, async support, license, last release").

Source policy:
- **Versions and package existence:** registry APIs only (`pypi.org/pypi/<pkg>/json`, `registry.npmjs.org/<pkg>`, `gh api repos/<o>/<r>/releases/latest`).
- **Every package the plan names:** check the registry for existence, and record exact name, maintainer, last release.
- **Pricing and limits:** the vendor's official page only, with the fetch date. JS-rendered pages `curl` cannot read are `inconclusive`, never guessed.
- **Conflicting sources:** report `contested` and list both sides. A claim resting on a single source is flagged `single-source`.

The Forbidden list still applies to *recommendations*: a verdict on a factual claim is allowed; choosing an approach is not.

## Report Template (strict)

Use exactly these parts, in this order, and add nothing else.
Each section content **must** be insude a <details> </details> block.

1. **`## 🔍 Context Collection Report — <topic>`**
2. **Executive summary:** 3–6 lines of plain prose directly under the title, with no heading. Cover what was found, the key files and the search scope.
3. **`### FILES FOUND`**
   An italic one-liner summarising the section, then one entry per file: path with line numbers and a one-line reason it is relevant.
4. **`### RELEVANT SNIPPETS`**
   An italic one-liner, then for each file the relevant code or config in fenced blocks with line numbers. No ❌/✅ or other judgment.
   This is @@architect's main input, and a thin set forces a costly second run. The engine splits long reports across comments, so err on the generous side:
   * Prefer whole functions, classes, methods or config blocks; roughly 100–150 lines per central file, smaller files in full.
   * Include the signature, needed imports, decorators and call sites.
   * Never use `…`, `[...]` or `# rest omitted`. If a block is huge, show the relevant definition in full and note the skipped line range.
5. **`### EXTERNAL REFERENCES`**
   An italic one-liner, then two parts:
   * **Entries**, one per reference: `**Title**`, `**URL:**`, `**Summary:**`, `**Score:**`. Related issues, PRs and doc links follow as plain bullets.
   * **One fenced `json` block**, an array with one object per reference, which downstream agents consume:
     ```json
     [{"url": "https://...", "date": "2026-01-01T00:00:00Z", "chunks": ["verbatim excerpt"], "summary": "why it matters", "score": 0.8}]
     ```
     `date` is the ISO-8601 UTC fetch date and `chunks` are verbatim, unedited excerpts. The JSON must be valid: no comments, no trailing commas, no prose inside the fence.
6. **`### MEASUREMENTS`** (required whenever you ran an experiment; otherwise write `None run` and why none was needed)
   One entry per experiment: the exact command, the condition (e.g. cold vs warm cache), and the verbatim output, trimmed to the relevant lines.
7. **`### SURPRISES`**
   Facts you met that bear on the request but were not asked for, each with its evidence type and source. State facts only, never what to do about them. Write `None` if there are none.
8. **`### NOT VERIFIED`**
   Requested facts or claims you could not settle, and why (no access, JS-rendered page, could not reproduce). Write `None` if everything was settled.
9. **`### OTHER FILES`**
   An italic one-liner, then a brief list of files examined and found irrelevant.
10. **`### WHAT THE MEASUREMENTS SHOW`** (only when the request reports numbers to reproduce)
    One line per reported delta: which reported numbers your measurements reproduced, and the exact command that produced them. Facts only — which numbers matched, which did not. No cause, no explanation, no next step.

### Forbidden

Never write ROOT CAUSE, ANALYSIS, FIX, SOLUTION, RECOMMENDATION, PROPOSED CHANGES, or any section that explains why code is broken or proposes changes. Even if you know the answer, report only locations, snippets and measurements. A measured number is a fact, not an analysis.
