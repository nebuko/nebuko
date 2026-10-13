# Context Research Skill

Procedural playbook for context collection: search strategies, exploration depth,
and the strict report template @@context must produce.

## Search Strategies (Use All of Them)

### Code Search
* Use `grep -r` to search for relevant keywords, function names, classes, and patterns.
* Use `find` to locate files by name, extension, or type.
* Use `file_editor view` to inspect specific files and understand their contents.
* Report file paths with line numbers for every relevant code snippet.

### GitHub Issues & PRs
* Use `gh issue list` and `gh pr list` to find related issues and pull requests.
* Use `gh issue view <number>` and `gh pr view <number>` to read details.
* Use `gh api /repos/<owner>/<repo>/issues/<number>/comments` to read comment threads.
* Report issue/PR URLs with relevant excerpts.

### Documentation
* Search for README, AGENTS.md, ARCHITECTURE.md, CONTRIBUTING.md, and any inline documentation.
* Read documentation files thoroughly and extract relevant sections.
* Report document section titles and file paths.

### Web Search
* Use the `brave_web_search` tool to search the web for supplementary information and state-of-the-art knowledge.
* `brave_news_search` is available for recent news; use `brave_web_search` by default.
* Each call takes a focused query and returns results with a title, URL, and snippet.
* Guidelines:
  - Use web search when gathering state-of-the-art knowledge, recent developments, or supplementary information that may not be in the repository.
  - Keep queries focused and specific to get high-quality results.
  - Scale the number of references you fully process based on task complexity: **3 for LOW, 5-6 for MEDIUM, up to 10 for HIGH**.
  - Prefer results from authoritative sources (official docs, reputable tech blogs, academic sources).
  - Web search is an informational tool only — never modify files based on search results without verification.
* If the web-search tools are unavailable (no API key configured), skip web search and rely on the repository.

### Reading Each Reference (full text, then curated excerpts)

For each selected reference, do **not** rely on the search snippet — read the whole page, then curate:

1. **Fetch the page as text** with `curl` (follow redirects, 30s cap):
   ```bash
   curl -sL --max-time 30 -A "Mozilla/5.0" "<url>" -o /tmp/ref.html
   ```
2. **Convert to readable text** (strip tags, drop script/style/nav/header/footer blocks):
   ```bash
   python3 -c "
   from html.parser import HTMLParser
   import re
   class T(HTMLParser):
       SKIP = {'script','style','nav','header','footer','aside','noscript'}
       def __init__(self): super().__init__(convert_charrefs=True); self.parts=[]; self.skip=0
       def handle_starttag(self, tag, attrs):
           if tag in self.SKIP: self.skip += 1
           if tag in ('p','div','h1','h2','h3','h4','li','tr','pre','br','section','article'): self.parts.append('\n')
       def handle_endtag(self, tag):
           if tag in self.SKIP and self.skip: self.skip -= 1
       def handle_data(self, d):
           if not self.skip: self.parts.append(d)
   p = T(); p.feed(open('/tmp/ref.html', encoding='utf-8', errors='replace').read())
   text = re.sub(r'\n{3,}', '\n\n', re.sub(r'[ \t]+', ' ', '\n'.join(p.parts))).strip()
   print(text[:200000])
   "
   ```
   Use a dedicated URL→text/markdown tool if one is available in the environment; otherwise this curl pipeline is the fallback. Never use paid conversion services (e.g. the `curl.md` product — paid quota).
3. **Read the entire text** — token cost is cheap here; the purpose is to understand the whole article before selecting.
4. **Select the relevant chunks**: the unmodified (verbatim, no editing/paraphrasing) portions of the page that bear on the issue/question. **Budget: ≤ ~2,000 tokens per article** (≈ ≤ 8,000 chars), so a full run stays under ~20K tokens of external content.
5. **Write a short summary** (1–3 sentences) of how the article relates to the specific issue/question.
6. **Assign a score** in `0.0`–`1.0`: the article's level of interest/relevance to the issue (not source credibility).
7. If a fetch fails (timeout, 4xx/5xx, or no readable text), keep the reference in the report with the search snippet as the sole chunk, note the failure in its summary, and score it accordingly.

## Complexity-Based Exploration Depth

Adjust your exploration depth based on the task complexity level (available via the `COMPLEXITY` environment variable or implied from the request):

**LOW complexity** — Focused, minimal search:
- 1-2 targeted `grep`/`find` searches for the specific keyword or pattern.
- 3 web search references, each fully read and curated (see Web Search section).
- Surface-level understanding is sufficient — locate the immediate file(s) and move on.

**MEDIUM complexity** (default) — Standard exploration:
- Standard file traversal across likely directories.
- 5-6 web search references, each fully read and curated (see Web Search section).
- Cross-reference adjacent files and related modules.

**HIGH complexity** — Deep, broad exploration:
- 5-8 `grep`/`find` searches with progressively broader patterns to trace dependencies.
- Up to 10 web search references including SOTA research, academic papers, and benchmark comparisons, each fully read and curated (see Web Search section).
- Trace cross-module dependencies and architectural boundaries.
- Examine test files for behavioral context and edge cases.
- Go deeper on the web: search for recent developments, alternative approaches, and state-of-the-art patterns beyond what exists in the repository.
- Summarize findings from multiple angles (architecture, performance, alternatives).

## Output Format — Strict Template

Your final report MUST follow this exact structure. Do not add extra sections beyond these.

### Required Sections (in this order):

1. **`## 🔍 Context Collection Report — <topic>`** — Brief title matching the request.
2. **Executive summary** — 3–6 lines directly under the title: what was found, the key files, and the search scope. No heading of its own; plain prose. This block stays always visible in the posted comment (it sits above the first `###` section).
3. **`### FILES FOUND`** — Immediately under the heading, an *italic one-liner* resuming the section's contents (e.g. `*12 files covering the posting pipeline, message splitter, CI steps.*`). Then list each relevant file with its path and a one-line description of why it's relevant. Include line numbers.
4. **`### RELEVANT SNIPPETS`** — Immediately under the heading, an *italic one-liner* resuming the section's contents. Then for each file found above, show the relevant code or config with line numbers, in fenced code blocks. Do NOT annotate with ❌, ✅, or any judgment.

   **Budget — err on the generous side.** This section is the primary architectural input for @@architect: a thin snippet set that forces a second exploration loop is *more* expensive than posting a few extra comments (the engine now splits a long report across as many comments as needed, issue #913). So:
   - Prefer **whole functions, classes, methods, or config blocks** over one-line excerpts. Truncating a function's body at the exact line the issue turns on is the single most common cause of a wasted follow-up run.
   - Aim for roughly **100–150 lines per file** (or the full enclosing definition, whichever is larger) when the file is central to the issue; smaller files may be shown in full.
   - Include the surrounding context that makes the code interpretable: the enclosing signature/`def`/`class` line, imports it depends on, decorators, and the call sites that invoke it.
   - Do **not** self-truncate with `…`, `[...]`, or `# rest omitted` placeholders — if a block is genuinely huge, show the relevant definition in full and note the elided range by line number.
   - It is fine (and expected) for this section to be long. Do not split it yourself or drop content to keep the comment short — the engine handles comment size.
5. **`### EXTERNAL REFERENCES`** — Immediately under the heading, an *italic one-liner* resuming the section's contents. Two parts:
   - **Human-readable entries** — one per reference, in this order: `**Title**`, `**URL:**`, `**Summary:**` (1–3 sentences on relevance to the issue), `**Score:** 0.0–1.0`. Related issues/PRs and documentation links can follow as plain bullets.
   - **Machine-readable JSON** — one fenced ```json block (an array, one object per reference) alongside the entries, with this exact schema — this is what downstream agents (notably @@architect) consume:
     ```json
     [
       {
         "url": "https://...",
         "date": "2026-01-01T00:00:00Z",
         "chunks": ["verbatim excerpt 1", "verbatim excerpt 2"],
         "summary": "why this article matters for the issue",
         "score": 0.8
       }
     ]
     ```
     Field rules: `date` is the ISO-8601 UTC fetch date; `chunks` are **verbatim, unedited** page excerpts (≤ ~2,000 tokens per reference, ≈ ≤ 8,000 chars); `summary` is 1–3 sentences on relevance/interest to the issue; `score` is a float in `0.0`–`1.0` (relevance to the issue, not source credibility). The JSON must be valid — no comments, no trailing commas, no prose outside the fence.
6. **`### OTHER FILES`** — Immediately under the heading, an *italic one-liner* resuming the section's contents. Then briefly list any files you examined that turned out to be irrelevant, so @@architect knows the scope of your search.

### Forbidden Sections — NEVER include:
- ROOT CAUSE
- ANALYSIS
- FIX
- SOLUTION
- RECOMMENDATION
- PROPOSED CHANGES
- TWO POSSIBLE FIXES
- Any section that interprets why code is broken, proposes fixes, or makes recommendations

If you understand why something is broken or have a fix idea — DO NOT write it. Only report the code locations and snippets.
