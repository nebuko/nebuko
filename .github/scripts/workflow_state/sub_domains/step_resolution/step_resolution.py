from pydantic import BaseModel


class StepResolution(BaseModel):
    """Captures the full context of a step resolution decision for debugging.

    Stores the inputs that led to an agent selection, the selected configuration,
    and metadata about the matching rule. Populated from resolve_config() output
    after each workflow step resolution.

    Fields:
        # Decision inputs
        input_workflow: Workflow name requested at resolution time.
        input_step: Step name requested at resolution time.
        input_complexity: Complexity level used in resolution.

        # Decision outputs
        role_name: Resolved agent role (e.g., "dev", "ctx", "genius").
        prompt_location: Path to the resolved prompt file.
        agent_selected: "true" if an agent was selected, "false" otherwise.
        selected_workflow: Workflow name from resolution result.
        selected_step: Step name from resolution result.
        next_step: Next step name from resolution result.
        skip_agent: "true" if agent execution should be skipped.

        # Configuration applied
        config_model: LLM model used.
        config_temperature: Temperature parameter.
        config_top_p: Top-p parameter.
        config_top_k: Top-k parameter.
        config_repetition_penalty: Repetition penalty parameter.
        agent_round: Agent round number.
        agent_skills: Skills passed to the agent.
        step_skills: Skills defined in the workflow step.

        # Debugging metadata
        reasoning: LLM rationale for the agent/step choice.
        matched_rule: Which workflow rule/config matched (e.g., "workflow:default/step:analyze").
        parent_workflow: Parent workflow name if in a sub-workflow.
        parent_workflow_step: Parent step name if in a sub-workflow.
        timestamp: ISO-8601 timestamp of when resolution occurred.
    """

    # Decision inputs
    input_workflow: str = ""
    input_step: str = ""
    input_complexity: str = ""

    # Decision outputs
    role_name: str = ""
    prompt_location: str = ""
    agent_selected: str = "false"
    selected_workflow: str = ""
    selected_step: str = ""
    next_step: str = ""
    skip_agent: str = "false"

    # Configuration applied
    config_model: str = ""
    config_temperature: str = ""
    config_top_p: str = ""
    config_top_k: str = ""
    config_repetition_penalty: str = ""
    agent_round: str = ""
    agent_skills: str = ""
    step_skills: str = ""

    # Debugging metadata
    reasoning: str = ""
    matched_rule: str = ""
    parent_workflow: str = ""
    parent_workflow_step: str = ""
    timestamp: str = ""
