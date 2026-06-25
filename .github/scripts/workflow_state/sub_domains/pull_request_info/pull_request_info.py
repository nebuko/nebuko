from pydantic import BaseModel

class PullRequestInfo(BaseModel):
    """Static pull request metadata embedded in workflow state."""

    number: int
    url: str
    title: str
    author: str
    repo: str