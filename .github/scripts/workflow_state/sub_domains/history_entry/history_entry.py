from typing import Optional

from pydantic import BaseModel, Field

class HistoryEntry(BaseModel):
    """A summarized record of one workflow step."""

    step: str
    agent: str
    summary: str = ""
    comment_id: Optional[int] = None
    commit_id: Optional[str] = None
    artifacts: list[str] = Field(default_factory=list)
    pin: bool = False