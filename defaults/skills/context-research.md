# Context Research Skill

Playbook for @@context: how to search, how deep to go, and the strict report template.

## Search Strategies

Use only what the request needs; batch commands and bound output (`sed -n`, `head`).

* **Code:** `grep -r` for keywords, function/class names and patterns; `find` for files by name or type; `file_editor view` for contents. Always record path and line numbers.
* **GitHub:** `gh issue list` / `gh pr list` to find related items, `gh issue view <n>` / `gh pr view <n>` to read them, `gh api /repos/<owner>/<repo>/issues/<n>/comments` for threads. Report URLs with relevant excerpts.
* **Docs:** README, AGENTS.md, ARCHITECTURE.md, CONTRIBUTING.md and inline docs. Report file paths and section titles.
* **Web:** `brave_web_search` (default) or `brave_news_search` (recent news) with focused queries, preferring authoritative sources. Use it only for information the repository cannot hold. If the tools are unavailable, skip the web and rely on the repository.

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

## Report Template (strict)

Use exactly these parts, in this order, and add nothing else.

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
6. **`### OTHER FILES`**
   An italic one-liner, then a brief list of files examined and found irrelevant.

### Forbidden

Never write ROOT CAUSE, ANALYSIS, FIX, SOLUTION, RECOMMENDATION, PROPOSED CHANGES, or any section that explains why code is broken or proposes changes. Even if you know the answer, report only locations and snippets.
