from pydantic import BaseModel

class IssueInfo(BaseModel):
    """Static issue metadata embedded in workflow state."""

    number: int
    url: str
    title: str
    author: str
    repo: str