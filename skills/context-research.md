# Context Research Skill

Procedural playbook for context collection — search strategies and exploration depth.

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
* Use the SearXNG instance at `https://searxng.norture.fr` to perform web searches for supplementary information and state-of-the-art knowledge.
* Query the API via `curl` using the JSON format:
  ```bash
  curl -s --get "https://searxng.norture.fr/search" --data-urlencode "q=<your query>" --data-urlencode "format=json"
  ```
* The response is structured JSON with these key fields:
  - `results` — array of search results, each containing:
    - `title` — result title
    - `url` — result URL
    - `content` — snippet/description
    - `engine` — search engine used (google, duckduckgo, wikipedia, etc.)
    - `score` — relevance score
    - `category` — result category
  - `suggestions` — query suggestions
  - `number_of_results` — total result count
* Example:
  ```bash
  curl -s --get "https://searxng.norture.fr/search" \
    --data-urlencode "q=latest advances in large language models" \
    --data-urlencode "format=json" | jq '.results[:5] | .[] | "\(.title) - \(.url) (\(.engine))"'
  ```
* Guidelines:
  - Use web search when gathering state-of-the-art knowledge, recent developments, or supplementary information that may not be in the repository.
  - Keep queries focused and specific to get high-quality results.
  - Scale result count based on task complexity: 1-3 items for LOW, 3-5 for MEDIUM, 5-10 for HIGH.
  - Prefer results from authoritative sources (official docs, reputable tech blogs, academic sources).
  - Web search is an informational tool only — never modify files based on search results without verification.
* Note: `jq` is available in the terminal for JSON parsing. If not, use Python: `python3 -c "import json,sys; print(json.dumps(sys.stdin.read(), indent=2))"`

## Complexity-Based Exploration Depth

Adjust your exploration depth based on the task complexity level (available via the `COMPLEXITY` environment variable or implied from the request):

**LOW complexity** — Focused, minimal search:
- 1-2 targeted `grep`/`find` searches for the specific keyword or pattern.
- 1-3 web search results.
- Surface-level understanding is sufficient — locate the immediate file(s) and move on.

**MEDIUM complexity** (default) — Standard exploration:
- Standard file traversal across likely directories.
- 3-5 web search results.
- Cross-reference adjacent files and related modules.

**HIGH complexity** — Deep, broad exploration:
- 5-8 `grep`/`find` searches with progressively broader patterns to trace dependencies.
- 5-10 web search results including SOTA research, academic papers, and benchmark comparisons.
- Trace cross-module dependencies and architectural boundaries.
- Examine test files for behavioral context and edge cases.
- Go deeper on the web: search for recent developments, alternative approaches, and state-of-the-art patterns beyond what exists in the repository.
- Summarize findings from multiple angles (architecture, performance, alternatives).