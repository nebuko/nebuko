from typing import Optional

from pydantic import BaseModel, Field, model_validator

# Registry of supported function-call names. Today only call_agent exists;
# future imperative actions (post_comment, update_issue, ...) register here.
SUPPORTED_FUNCTIONS = {"call_agent"}


class FunctionCall(BaseModel):
    """Imperative agent-to-agent hand-off carried by AGENT_RUN_CONFIG.

    Second section alongside Dispatch inside the AGENT_RUN_CONFIG sentinel:
    Dispatch expresses how *this run* ends inside a workflow (the engine
    resolves routing); FunctionCall is an explicit command naming the exact
    agent to run next, overriding workflow continuation. Consumed once by
    the next run's resolver and cleared from state (consumption by
    supersession, same idempotency model as dispatch).

    Fields:
        function: Registered function name (today: "call_agent").
        agent_name: Target agent, validated by the resolver against the
            roster (kept free-form here so the state model stays decoupled
            from resolve_workflow's TASK_TO_PROMPT).
        complexity: Complexity level for the called agent.
        reason: Optional free-text explanation for the call.
    """

    function: str = Field(default="call_agent")
    agent_name: str = ""
    complexity: str = "medium"
    reason: Optional[str] = None

    @model_validator(mode="after")
    def validate_known_function(self) -> "FunctionCall":
        if self.function not in SUPPORTED_FUNCTIONS:
            raise ValueError(
                f"Unknown function '{self.function}'. Must be one of: {sorted(SUPPORTED_FUNCTIONS)}"
            )
        return self