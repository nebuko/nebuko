#!/usr/bin/env python3
"""
GitHub issue creation utility for the multi-agent workflow.

Provides functions to create sub-issues under a parent issue using the
GitHub REST API. Called from the GitHub Actions workflow to implement
issue splitting when the genius agent decomposes a complex task into
smaller sub-tasks.

Usage:
    python issue_creation.py create --repo owner/repo --parent 123 --plan plan.json
    python issue_creation.py list --repo owner/repo --parent 123
    python issue_creation.py link --repo owner/repo --parent 123 --child 456
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.request import Request, urlopen
from urllib.error import HTTPError

from github_client import ensure_bot_marker

# Injected REST seam for issue creation (issue #590, Stage A): local mode passes
# a filesystem-writing callable instead of the GitHub REST path. The default
# stays exactly the current ``GitHubClient.create_issue`` call.
# @stub-protected
CreateIssue = Callable[[str, str, List[str]], Optional[dict]]


@dataclass
class TaskDefinition:
    """A single task within a split plan."""
    title: str
    body: str
    labels: List[str] = field(default_factory=list)
    dependencies: List[int] = field(default_factory=list)


@dataclass
class SplitPlan:
    """A plan to split a parent issue into multiple sub-issues."""
    tasks: List[TaskDefinition]
    auto_trigger: bool = True


# ── GitHub API Client (Port/Adapter Pattern) ────────────────────────


class GithubAPIError(Exception):
    """Raised when a GitHub API call fails."""
    def __init__(self, status_code: int, message: str, response_body: str = ""):
        self.status_code = status_code
        self.message = message
        self.response_body = response_body
        super().__init__(f"HTTP {status_code}: {message}")


class GitHubClient:
    """Thin adapter for GitHub REST API operations related to issues.
    
    Uses urllib to avoid external dependencies. Relies on GH_TOKEN env var.
    """

    BASE_URL = "https://api.github.com"

    def __init__(self, repo: str, token: Optional[str] = None):
        self.repo = repo
        self.token = token or os.environ.get("GH_TOKEN", "")
        self.headers = {
            "Authorization": f"token {self.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def _request(self, method: str, path: str, body: Optional[dict] = None) -> dict:
        """Execute an HTTP request against the GitHub API.
        
        Args:
            method: HTTP method (GET, POST, etc.).
            path: API path (e.g., /repos/owner/repo/issues).
            body: Optional JSON body for POST/PUT requests.
            
        Returns:
            Parsed JSON response as dict.
            
        Raises:
            GithubAPIError: If the API returns an error status.
        """
        url = f"{self.BASE_URL}{path}"
        data = json.dumps(body).encode("utf-8") if body else None
        
        req = Request(url, data=data, headers=self.headers, method=method)
        
        try:
            with urlopen(req) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as e:
            resp_body = e.read().decode("utf-8", errors="replace")
            raise GithubAPIError(e.code, str(e), resp_body)
        except Exception as e:
            raise GithubAPIError(0, f"Request failed: {e}", "")

    def create_issue(self, title: str, body: str, labels: List[str]) -> Optional[dict]:
        """Create a new issue in the repository.
        
        Args:
            title: Issue title.
            body: Issue body/description.
            labels: List of label names.
            
        Returns:
            A dict with "number" (display number) and "id" (internal ID),
            or None if the response is missing required fields.
        """
        path = f"/repos/{self.repo}/issues"
        payload = {"title": title, "body": body}
        if labels:
            payload["labels"] = labels
        
        result = self._request("POST", path, payload)
        number = result.get("number")
        iid = result.get("id")
        if number is None or iid is None:
            return None
        return {"number": number, "id": iid}

    def add_sub_issue(self, parent_number: int, sub_issue_id: int, position: Optional[int] = None) -> None:
        """Link an issue as a sub-issue of a parent.
        
        Uses the sub-issues API endpoint.
        https://docs.github.com/en/rest/issues/sub-issues
        
        Args:
            parent_number: Parent issue number.
            sub_issue_id: Internal ID of the child issue (not display number).
            position: Position in the sub-issue list (optional).
        """
        path = f"/repos/{self.repo}/issues/{parent_number}/sub_issues"
        body = {"sub_issue_id": sub_issue_id}
        if position is not None:
            body["position"] = position
        
        self._request("POST", path, body)

    def get_issue_by_number(self, issue_number: int) -> Optional[int]:
        """Fetch the internal ID of an issue by its display number.
        
        Args:
            issue_number: Display number of the issue.
            
        Returns:
            Internal ID of the issue, or None if not found.
        """
        path = f"/repos/{self.repo}/issues/{issue_number}"
        result = self._request("GET", path)
        return result.get("id")

    def list_sub_issues(self, parent_number: int) -> List[dict]:
        """List all sub-issues of a parent issue.
        
        Args:
            parent_number: Parent issue number.
            
        Returns:
            List of issue objects.
        """
        path = f"/repos/{self.repo}/issues/{parent_number}/sub_issues"
        return self._request("GET", path)

    def create_comment(self, issue_number: int, body: str) -> int:
        """Post a comment on an issue.
        
        Args:
            issue_number: Issue number to comment on.
            body: Comment text.
            
        Returns:
            The comment ID.
        """
        path = f"/repos/{self.repo}/issues/{issue_number}/comments"
        result = self._request("POST", path, {"body": ensure_bot_marker(body)})
        return result.get("id")


# ── Split Plan Operations ────────────────────────────────────────────


def load_split_plan(path: str) -> SplitPlan:
    """Load a split plan from a JSON file.
    
    Expected schema:
    {
        "tasks": [
            {"title": "...", "body": "...", "labels": ["label1"]}
        ],
        "auto_trigger": true
    }
    
    Args:
        path: Path to the JSON file.
        
    Returns:
        Parsed SplitPlan instance.
        
    Raises:
        FileNotFoundError: If the plan file does not exist.
        ValueError: If the plan schema is invalid.
    """
    if not os.path.isfile(path):
        raise FileNotFoundError(f"Split plan file not found: {path}")
    
    with open(path) as f:
        raw = json.load(f)
    
    if not isinstance(raw, dict):
        raise ValueError("Split plan must be a JSON object")
    
    tasks_raw = raw.get("tasks", [])
    if not tasks_raw or not isinstance(tasks_raw, list):
        raise ValueError("Split plan must contain a non-empty 'tasks' array")
    
    tasks = []
    for i, t in enumerate(tasks_raw):
        if not isinstance(t, dict) or not t.get("title"):
            raise ValueError(f"Task #{i} must have a 'title' field")
        labels = t.get("labels", [])
        if not isinstance(labels, list):
            raise ValueError(f"Task #{i} 'labels' must be a list")
        dependencies = t.get("dependencies", [])
        if not isinstance(dependencies, list):
            raise ValueError(f"Task #{i} 'dependencies' must be a list")
        tasks.append(TaskDefinition(
            title=t["title"],
            body=t.get("body", ""),
            labels=labels,
            dependencies=dependencies,
        ))
    
    return SplitPlan(
        tasks=tasks,
        auto_trigger=raw.get("auto_trigger", True),
    )


def create_sub_issues(
    client: GitHubClient,
    plan: SplitPlan,
    parent_number: int,
    *,
    create_issue: CreateIssue | None = None,
) -> List[Dict[str, Any]]:
    """Execute a split plan: create issues and link them as sub-issues.
    
    Processes each task independently — failures on one task do not abort
    the remaining tasks. A summary comment is posted on the parent issue
    if any task fails.
    
    Args:
        client: GitHub API client.
        plan: The split plan to execute.
        parent_number: Parent issue number.
        
    Returns:
        List of dicts with "index", "number", and "dependencies" for each
        successfully created issue.
        
    Raises:
        ValueError: If a newly created issue has no valid issue number.
    """
    created: List[Dict[str, Any]] = []
    failed_tasks: List[Tuple[int, str, str]] = []  # (idx, title, error)
    total = len(plan.tasks)

    for idx, task in enumerate(plan.tasks, start=1):
        prefixed_title = f"[Sub-task {idx}/{total}] {task.title}"
        labels_with_parent = list(task.labels) + [f"parent-{parent_number}"]
        print(f"[{idx}/{total}] Creating sub-issue: {prefixed_title}")

        try:
            result = client.create_issue(prefixed_title, task.body, labels_with_parent)
            if result is None:
                raise ValueError(
                    f"Failed to create issue: API response missing required "
                    f"fields for '{task.title}'"
                )
            issue_num = result["number"]
            issue_id = result["id"]
            print(f"  → Created issue #{issue_num} (id={issue_id})")
            
            client.add_sub_issue(parent_number, issue_id, position=idx)
            print(f"  → Linked as sub-issue of #{parent_number} (position {idx})")
            
            created.append({
                "index": idx,
                "number": issue_num,
                "dependencies": task.dependencies,
            })
        
        except Exception as e:
            error_msg = str(e)
            print(f"  → FAILED: {error_msg}")
            failed_tasks.append((idx, task.title, error_msg))
    
    # Post summary on parent if there were failures
    if failed_tasks:
        summary_lines = [
            "⚠️ **Partial decomposition** — some sub-issues could not be created:",
            "",
            f"**Succeeded:** {len(created)}/{len(plan.tasks)}",
            "",
            "**Failed tasks:**",
        ]
        for idx, title, err in failed_tasks:
            summary_lines.append(f"- Task #{idx} \"{title}\": `{err}`")
        summary_lines.append("")
        summary_lines.append("Please resolve these manually or re-trigger the workflow.")
        
        try:
            client.create_comment(parent_number, "\n".join(summary_lines))
        except Exception:
            print(f"WARNING: Failed to post summary comment on parent issue #{parent_number}")
    
    return created


def store_split_plan_state(
    client: GitHubClient,
    parent_number: int,
    tasks_with_numbers: List[Dict[str, Any]],
) -> None:
    """Post a hidden comment on the parent issue with the dependency graph state.
    
    The comment uses HTML comment syntax so it's invisible in the UI but
    accessible via the API and readable by agents scanning issue history.
    
    Args:
        client: GitHub API client.
        parent_number: Parent issue number.
        tasks_with_numbers: List of task dicts with "index", "number", "dependencies".
    """
    state = {
        "tasks": [
            {
                "index": t["index"],
                "issue_number": t["number"],
                "dependencies": t.get("dependencies", []),
            }
            for t in tasks_with_numbers
        ],
        "completed": [],
    }
    body = f"<!-- split-plan-state: {json.dumps(state)} -->"
    try:
        client.create_comment(parent_number, body)
        print(f"  → Stored split plan state on parent issue #{parent_number}")
    except Exception as e:
        print(f"  → FAILED to store split plan state: {e}")


def trigger_workflows(
    client: GitHubClient,
    tasks_with_numbers: List[Dict[str, Any]],
) -> None:
    """Post @@genius comments only on tasks with no pending dependencies.
    
    Args:
        client: GitHub API client.
        tasks_with_numbers: List of task dicts with "index", "number", "dependencies".
            Only tasks whose "dependencies" list is empty will be triggered.
    """
    trigger_body = "@@genius"
    triggered = 0
    skipped = 0
    
    for task in tasks_with_numbers:
        if task.get("dependencies"):
            skipped += 1
            continue
        
        num = task["number"]
        try:
            client.create_comment(num, trigger_body)
            print(f"  → Triggered workflow on issue #{num}")
            triggered += 1
        except Exception as e:
            print(f"  → FAILED to trigger issue #{num}: {e}")
    
    if skipped:
        print(f"  → Skipped {skipped} task(s) with unmet dependencies")
    print(f"  → Total triggered: {triggered}/{len(tasks_with_numbers)}")


# ── CLI Interface ────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(
        description="GitHub issue creation utility for multi-agent workflow"
    )
    subparsers = parser.add_subparsers(dest="command", help="Command to execute")

    # `create` command
    create_parser = subparsers.add_parser("create", help="Create sub-issues from a plan")
    create_parser.add_argument("--repo", required=True, help="Repository (owner/repo)")
    create_parser.add_argument("--parent", type=int, required=True, help="Parent issue number")
    create_parser.add_argument("--plan", required=True, help="Path to split plan JSON file")
    create_parser.add_argument(
        "--no-trigger", action="store_true",
        help="Do NOT post @@genius on created issues (skip workflow trigger)",
    )

    # `list` command
    list_parser = subparsers.add_parser("list", help="List sub-issues of a parent")
    list_parser.add_argument("--repo", required=True, help="Repository (owner/repo)")
    list_parser.add_argument("--parent", type=int, required=True, help="Parent issue number")

    # `link` command
    link_parser = subparsers.add_parser("link", help="Link existing issue as sub-issue")
    link_parser.add_argument("--repo", required=True, help="Repository (owner/repo)")
    link_parser.add_argument("--parent", type=int, required=True, help="Parent issue number")
    link_parser.add_argument("--child", type=int, required=True, help="Child issue number")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    client = GitHubClient(repo=args.repo)

    if args.command == "create":
        plan = load_split_plan(args.plan)
        tasks_with_numbers = create_sub_issues(client, plan, args.parent)
        
        if tasks_with_numbers:
            store_split_plan_state(client, args.parent, tasks_with_numbers)
        
        if plan.auto_trigger and not args.no_trigger:
            print("Triggering workflows on ready sub-issues...")
            trigger_workflows(client, tasks_with_numbers)
        
        created_numbers = [t["number"] for t in tasks_with_numbers]
        print(f"\nCreated {len(created_numbers)} sub-issue(s): {created_numbers}")

    elif args.command == "list":
        subs = client.list_sub_issues(args.parent)
        if subs:
            for sub in subs:
                print(f"#{sub['number']}: {sub['title']}")
        else:
            print(f"No sub-issues found for #{args.parent}")

    elif args.command == "link":
        sub_issue_id = client.get_issue_by_number(args.child)
        if sub_issue_id is None:
            print(f"ERROR: Could not find issue #{args.child}")
            sys.exit(1)
        client.add_sub_issue(args.parent, sub_issue_id)
        print(f"Linked #{args.child} (id={sub_issue_id}) as sub-issue of #{args.parent}")


if __name__ == "__main__":
    main()
