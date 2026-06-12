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

import argparse
import json
import os
import sys
from dataclasses import dataclass, field
from typing import Optional
from urllib.request import Request, urlopen
from urllib.error import HTTPError


@dataclass
class TaskDefinition:
    """A single task within a split plan."""
    title: str
    body: str
    labels: list[str] = field(default_factory=list)


@dataclass
class SplitPlan:
    """A plan to split a parent issue into multiple sub-issues."""
    tasks: list[TaskDefinition]
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

    def create_issue(self, title: str, body: str, labels: list[str]) -> int:
        """Create a new issue in the repository.
        
        Args:
            title: Issue title.
            body: Issue body/description.
            labels: List of label names.
            
        Returns:
            The issue number of the newly created issue.
        """
        path = f"/repos/{self.repo}/issues"
        payload = {"title": title, "body": body}
        if labels:
            payload["labels"] = labels
        
        result = self._request("POST", path, payload)
        return result.get("number")

    def add_sub_issue(self, parent_number: int, child_number: int, position: Optional[int] = None) -> None:
        """Link an issue as a sub-issue of a parent.
        
        Uses the sub-issues API endpoint.
        https://docs.github.com/en/rest/issues/sub-issues
        
        Args:
            parent_number: Parent issue number.
            child_number: Child issue number to link.
            position: Position in the sub-issue list (optional).
        """
        path = f"/repos/{self.repo}/issues/{parent_number}/sub_issues"
        body = {"issue_number": child_number}
        if position is not None:
            body["position"] = position
        
        self._request("POST", path, body)

    def list_sub_issues(self, parent_number: int) -> list[dict]:
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
        result = self._request("POST", path, {"body": body})
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
        tasks.append(TaskDefinition(
            title=t["title"],
            body=t.get("body", ""),
            labels=t.get("labels", []),
        ))
    
    return SplitPlan(
        tasks=tasks,
        auto_trigger=raw.get("auto_trigger", True),
    )


def create_sub_issues(
    client: GitHubClient,
    plan: SplitPlan,
    parent_number: int,
) -> list[int]:
    """Execute a split plan: create issues and link them as sub-issues.
    
    Args:
        client: GitHub API client.
        plan: The split plan to execute.
        parent_number: Parent issue number.
        
    Returns:
        List of created issue numbers.
    """
    created_numbers = []
    
    for idx, task in enumerate(plan.tasks, start=1):
        print(f"[{idx}/{len(plan.tasks)}] Creating sub-issue: {task.title}")
        
        issue_num = client.create_issue(task.title, task.body, task.labels)
        print(f"  → Created issue #{issue_num}")
        
        client.add_sub_issue(parent_number, issue_num, position=idx)
        print(f"  → Linked as sub-issue of #{parent_number} (position {idx})")
        
        created_numbers.append(issue_num)
    
    return created_numbers


def trigger_workflows(
    client: GitHubClient,
    issue_numbers: list[int],
) -> None:
    """Post @@genius comments on sub-issues to trigger their workflows.
    
    Args:
        client: GitHub API client.
        issue_numbers: List of issue numbers to trigger.
    """
    trigger_body = "@@genius"
    
    for num in issue_numbers:
        print(f"  → Triggering workflow on issue #{num}")
        client.create_comment(num, trigger_body)


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
        created = create_sub_issues(client, plan, args.parent)
        
        if plan.auto_trigger and not args.no_trigger:
            print("Triggering workflows on sub-issues...")
            trigger_workflows(client, created)
        
        print(f"\nCreated {len(created)} sub-issue(s): {created}")

    elif args.command == "list":
        subs = client.list_sub_issues(args.parent)
        if subs:
            for sub in subs:
                print(f"#{sub['number']}: {sub['title']}")
        else:
            print(f"No sub-issues found for #{args.parent}")

    elif args.command == "link":
        client.add_sub_issue(args.parent, args.child)
        print(f"Linked #{args.child} as sub-issue of #{args.parent}")


if __name__ == "__main__":
    main()
