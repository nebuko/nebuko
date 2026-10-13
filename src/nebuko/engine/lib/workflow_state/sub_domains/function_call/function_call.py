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
        complexity: Complexity tier for the called agent. Empty means the
            caller declared none, so the resolver falls back to the called
            step's declared tier, then ``"medium"`` (issue #787).
        reason: Optional free-text explanation for the call.
        callback: Optional return address (issue #765). The agent to run when
            the callee finishes without an explicit decision of its own —
            the agent-as-tools "control returns to the caller" pattern. It is
            a fallback target: the callee's own explicit function_call /
            hold / loop still wins, and the workflow forward edge is used
            only when no callback is registered.
        sub_run: Provenance marker (issue #889). True only for an explicit
            typed ``call_agent`` the agent itself issued as its decision — an
            out-of-workflow sub-run that preserves the caller's position and
            that a human gate must let through. False for a ``dispatch_next``
            target promoted to a call_agent spec (a *workflow dispatch*), which
            stays held. Set by the producer (:mod:`build_output`); a default
            ``False`` is omitted from the serialized state (``exclude_defaults``),
            so legacy/omitted callers keep workflow-dispatch semantics.
    """

    function: str = Field(default="call_agent")
    agent_name: str = ""
    # "" = the caller declared no tier; the resolver applies the called step's
    # declared tier, else "medium" (issue #787).
    complexity: str = ""
    reason: Optional[str] = None
    callback: Optional[str] = None
    # Explicit typed-call provenance (issue #889): serialized only when true.
    sub_run: bool = False

    @model_validator(mode="after")
    def validate_known_function(self) -> "FunctionCall":
        if self.function not in SUPPORTED_FUNCTIONS:
            raise ValueError(
                f"Unknown function '{self.function}'. Must be one of: {sorted(SUPPORTED_FUNCTIONS)}"
            )
        if self.function == "call_agent" and not self.agent_name.strip():
            raise ValueError("call_agent requires a non-empty agent_name")
        return self
