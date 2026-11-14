# Branch & PR lifecycle

How the engine turns one issue into a scaffolded branch and an open PR.

## Usage

```python
from nebuko.engine.models import IssueRef
from nebuko.engine.lib.git.branch_scaffold import scaffold_issue

result = scaffold_issue(
    IssueRef(number=1153, title="Prevent multiple branches"),
    base="origin/main",
)
print(result.branch_name)   # feature/1153-prevent-multiple-branches
print(result.pr_url)        # https://github.com/.../pull/...
```

## Mechanics

1. **Slug** — the issue title is slugified (lowercase, non-alphanumerics
   collapsed to `-`, trimmed to 60 chars).
2. **Branch naming** — the branch is `feature/<issue_number>-<slug>`.
3. **Base** — the branch is cut from `base` (`origin/main` by default).
4. **PR** — a draft PR is opened against `base` linking back to the issue.
