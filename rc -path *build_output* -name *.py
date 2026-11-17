[1mdiff --git a/src/nebuko/engine/lib/resolve_config/features/build_result/build_result.py b/src/nebuko/engine/lib/resolve_config/features/build_result/build_result.py[m
[1mindex 1c71af7..d9a86aa 100644[m
[1m--- a/src/nebuko/engine/lib/resolve_config/features/build_result/build_result.py[m
[1m+++ b/src/nebuko/engine/lib/resolve_config/features/build_result/build_result.py[m
[36m@@ -163,16 +163,18 @@[m [mdef _deepest_workflow_index(refs: list, workflow: str) -> Optional[int]:[m
     return None[m
 [m
 [m
[31m-def _agent_skill_specs(role_name: str) -> list:[m
[32m+[m[32mdef _agent_skill_specs(role_name: str, config_path: Optional[str] = None) -> list:[m
     """The role's declared skill entries; empty when the roster is unreadable.[m
 [m
     The resolver already loads the roster for routing (``TASK_TO_PROMPT``), so[m
     this reads the same file rather than duplicating the roster into the[m
[31m-    dispatch payload.[m
[32m+[m[32m    dispatch payload. ``config_path`` is the agent config this run selected[m
[32m+[m[32m    (issue #1389); it outranks the ambient ``AGENT_CONFIG_PATH`` because the[m
[32m+[m[32m    child only receives the override through this result dict.[m
     """[m
     from nebuko.engine.lib.config import load_config[m
 [m
[31m-    path = os.environ.get("AGENT_CONFIG_PATH", "agent-config.yaml")[m
[32m+[m[32m    path = config_path or os.environ.get("AGENT_CONFIG_PATH", "agent-config.yaml")[m
     try:[m
         agent = load_config(path).agents.get(role_name)[m
     except Exception:  # noqa: BLE001 - a bad roster must not break resolution[m
[36m@@ -370,7 +372,9 @@[m [mdef _build_result([m
         # Skill attachment hand-off (issue #955): both spec lists plus the[m
         # absolute skills directory, so the prompt builder and the agent child[m
         # resolve the same inject/discover split from the same files.[m
[31m-        "AGENT_SKILL_SPECS": specs_to_env(_agent_skill_specs(role_name)),[m
[32m+[m[32m        "AGENT_SKILL_SPECS": specs_to_env([m
[32m+[m[32m            _agent_skill_specs(role_name, config.get("agent_config_path"))[m
[32m+[m[32m        ),[m
         "STEP_SKILL_SPECS": wf_result.get("STEP_SKILL_SPECS", ""),[m
         "SKILLS_DIR": _skills_dir(),[m
         "STEP_PROMPT_FILE": wf_result.get("STEP_PROMPT_FILE", ""),[m
[36m@@ -435,6 +439,12 @@[m [mdef _build_result([m
         ),[m
         "SKIP_AGENT": "false",[m
     }[m
[32m+[m[32m    # Per-run agent config selection (issue #1389): the resolved path overrides[m
[32m+[m[32m    # the workflow input for this run only, and the raw flag value rides along[m
[32m+[m[32m    # so ``build_output`` can persist it in workflow state for later hand-offs.[m
[32m+[m[32m    if config.get("agent_config_path"):[m
[32m+[m[32m        result["AGENT_CONFIG_PATH"] = config["agent_config_path"][m
[32m+[m[32m        result["AGENT_CONFIG"] = config.get("agent_config_raw", "")[m
     result["WORKFLOW_POSITION"] = wf_result.get("WORKFLOW_POSITION", "")[m
     result["WORKFLOW_POSITION_PATH"] = wf_result.get("WORKFLOW_POSITION_PATH", "")[m
     result = _attach_position(result, config, workflows_dir, local_workflows_dir)[m
[1mdiff --git a/src/nebuko/engine/lib/resolve_config/sub_domains/workflow_command/workflow_command.py b/src/nebuko/engine/lib/resolve_config/sub_domains/workflow_command/workflow_command.py[m
[1mindex 6726b1c..8417b17 100644[m
[1m--- a/src/nebuko/engine/lib/resolve_config/sub_domains/workflow_command/workflow_command.py[m
[1m+++ b/src/nebuko/engine/lib/resolve_config/sub_domains/workflow_command/workflow_command.py[m
[36m@@ -9,7 +9,8 @@[m [mCanonical actions (issue #635):[m
                    optional (omitting it runs the repo's default workflow —[m
                    issue #1094), ``--step`` optional (enter at a dedicated step,[m
                    issue #789; a dotted ``a.b`` selector descends into mounted[m
[31m-                   sub-workflows, issue #822)[m
[32m+[m[32m                   sub-workflows, issue #822), ``--config`` optional (select the[m
[32m+[m[32m                   agent config for this run only, issue #1389)[m
   - ``resume``     no resource — resume the held step; ``--feedback`` optional[m
   - ``force-step`` resource ``step`` — jump to a step; ``--name`` required[m
                    (dotted ``a.b`` selectors accepted, issue #822)[m
[36m@@ -138,7 +139,7 @@[m [mRESOURCE_BY_ACTION: Dict[str, Optional[str]] = {[m
 }[m
 [m
 FLAGS_BY_ACTION: Dict[str, Set[str]] = {[m
[31m-    "run": {"name", "step"},[m
[32m+[m[32m    "run": {"name", "step", "config"},[m
     "force-step": {"name"},[m
     "resume": {"feedback"},[m
     "delete": {"issue", "from"},[m
[36m@@ -328,7 +329,10 @@[m [mdef _parse_flags([m
 #: (issue #804). Keyed by the invoked action (so the ``clean`` alias keeps its[m
 #: own spelling) with the grammar action as fallback.[m
 _USAGE_BY_ACTION: Dict[str, str] = {[m
[31m-    "run": "/nebuko run workflow --name <workflow> [--step <step[.step...]>]",[m
[32m+[m[32m    "run": ([m
[32m+[m[32m        "/nebuko run workflow --name <workflow> [--step <step[.step...]>] "[m
[32m+[m[32m        "[--config <agent-config>]"[m
[32m+[m[32m    ),[m
     "resume": "/nebuko resume [--feedback <text>]",[m
     "force-step": "/nebuko force-step step --name <step[.step...]>",[m
     "delete": "/nebuko delete comments [--issue <number>] [--from <comment-id>]",[m
[1mdiff --git a/src/nebuko/engine/lib/workflow_state/features/build_state/build_state.py b/src/nebuko/engine/lib/workflow_state/features/build_state/build_state.py[m
[1mindex a1e9b4e..4401d61 100644[m
[1m--- a/src/nebuko/engine/lib/workflow_state/features/build_state/build_state.py[m
[1m+++ b/src/nebuko/engine/lib/workflow_state/features/build_state/build_state.py[m
[36m@@ -96,6 +96,7 @@[m [mdef build_state([m
     gate_decision: Optional[GateDecision] = None,[m
     workflow_ref: Optional[str] = None,[m
     gate: Optional[GateLock] = None,[m
[32m+[m[32m    agent_config: Optional[str] = None,[m
     complexity_adjustment: Optional[ComplexityAdjustment] = None,[m
     complexity_review_request: Optional[ComplexityReviewRequest] = None,[m
     complexity_policy: Optional[ComplexityPolicy] = None,[m
[36m@@ -173,6 +174,10 @@[m [mdef build_state([m
             (issue #862), read from ``job.workflow_ref``. None preserves the[m
             previous value (``None`` on legacy blobs), so a run hosted outside[m
             the reusable workflow records nothing.[m
[32m+[m[32m        agent_config: Agent config selected for this run by[m
[32m+[m[32m            ``/nebuko run workflow --config <file>`` (issue #1389), as the raw[m
[32m+[m[32m            flag value. None preserves the previous value, so a hand-off[m
[32m+[m[32m            comment keeps the provider the human selected.[m
         thread: The reconciled thread ledger (issue #872). Non-None replaces the[m
             ledger wholesale (the engine folds the full comment list into it[m
             before advancing); None preserves the previous ledger.[m
[36m@@ -196,6 +201,7 @@[m [mdef build_state([m
         base_pr = None[m
         base_started_by = None[m
         base_workflow_ref = None[m
[32m+[m[32m        base_agent_config = None[m
         base_circuit_breaker = CircuitBreakerState()[m
         base_gate_decision: Optional[GateDecision] = None[m
         base_gate_history: list[GateDecision] = [][m
[36m@@ -216,6 +222,7 @@[m [mdef build_state([m
         base_pr = prev_state.pull_request[m
         base_started_by = prev_state.started_by[m
         base_workflow_ref = prev_state.workflow_ref[m
[32m+[m[32m        base_agent_config = prev_state.agent_config[m
         base_circuit_breaker = prev_state.circuit_breaker[m
         base_gate_decision = prev_state.gate_decision[m
         base_gate_history = list(prev_state.gate_history)[m
[36m@@ -302,6 +309,13 @@[m [mdef build_state([m
         workflow_ref if workflow_ref is not None else base_workflow_ref[m
     )[m
 [m
[32m+[m[32m    # Per-run agent config selection (issue #1389): a run that carries a[m
[32m+[m[32m    # ``--config`` records it; a later hand-off comment (a new job that carries[m
[32m+[m[32m    # no flag) preserves it, so the provider does not silently revert.[m
[32m+[m[32m    new_agent_config = ([m
[32m+[m[32m        agent_config if agent_config is not None else base_agent_config[m
[32m+[m[32m    )[m
[32m+[m
     # Human_gate decision (issue #648): when this run was started by an[m
     # identity-verified gate command, record the new decision as the latest[m
     # and append it to the full audit trail. Otherwise carry the previous[m
[36m@@ -397,6 +411,7 @@[m [mdef build_state([m
         callback=callback,[m
         started_by=new_started_by,[m
         workflow_ref=new_workflow_ref,[m
[32m+[m[32m        agent_config=new_agent_config,[m
         position=position_refs,[m
         soft_retry=new_soft_retry,[m
         # Per-issue circuit-breaker policy (issue #839): carried forward[m
[1mdiff --git a/src/nebuko/engine/lib/workflow_state/workflow_state.py b/src/nebuko/engine/lib/workflow_state/workflow_state.py[m
[1mindex f3f7567..144377e 100644[m
[1m--- a/src/nebuko/engine/lib/workflow_state/workflow_state.py[m
[1m+++ b/src/nebuko/engine/lib/workflow_state/workflow_state.py[m
[36m@@ -136,8 +136,9 @@[m [mclass CircuitBreakerState(BaseModel):  # @stub-protected[m
 # top-level state shape changes: v1 flat -> v2 recursive position -> v3[m
 # universal position -> v4 (``run_id`` + ``schema_version`` split) -> v5 (thread[m
 # ledger + per-step input records, issue #872) -> v6 (``turn_outcome`` replaces[m
[31m-# the legacy ``dispatch``, issue #964).[m
[31m-STATE_SCHEMA_VERSION = 6[m
[32m+[m[32m# the legacy ``dispatch``, issue #964) -> v7 (``agent_config`` per-run agent[m
[32m+[m[32m# config selection, issue #1389).[m
[32m+[m[32mSTATE_SCHEMA_VERSION = 7[m
 [m
 [m
 class WorkflowState(BaseModel):[m
[36m@@ -222,6 +223,13 @@[m [mclass WorkflowState(BaseModel):[m
             via the ``NEBUKO_WORKFLOW_REF`` env var. Provenance only — the[m
             checkout pin is resolved by the workflow YAML itself. None when the[m
             var is absent, so legacy blobs load and serialize unchanged.[m
[32m+[m[32m        agent_config: Agent config selected for this run by[m
[32m+[m[32m            ``/nebuko run workflow --config <file>`` (issue #1389), stored as the[m
[32m+[m[32m            raw flag value. It selects which credentials the child process[m
[32m+[m[32m            receives, so it is routing-adjacent like ``workflow_ref`` and[m
[32m+[m[32m            persists across hand-off comments — each comment is a new job that[m
[32m+[m[32m            would otherwise fall back to the workflow's own config. None when[m
[32m+[m[32m            the run never selected one, so legacy blobs load unchanged.[m
     """[m
 [m
     run_id: int = 0[m
[36m@@ -280,6 +288,11 @@[m [mclass WorkflowState(BaseModel):[m
     # from ``NEBUKO_WORKFLOW_REF`` (``job.workflow_ref``). Provenance only;[m
     # None on legacy blobs, which load and serialize unchanged.[m
     workflow_ref: Optional[str] = None[m
[32m+[m[32m    # Per-run agent config selection (issue #1389), from the ``--config`` flag[m
[32m+[m[32m    # of ``/nebuko run workflow``. Persisted so later hand-off comments (each a[m
[32m+[m[32m    # new job) keep the same provider; None on legacy blobs, which load[m
[32m+[m[32m    # unchanged.[m
[32m+[m[32m    agent_config: Optional[str] = None  # @stub-protected[m
     # Flat workflow step path, outermost-first, last ref = the step actually[m
     # executed (issue #638). Legacy v2 nested position blocks in posted[m
     # states are migrated to the flat refs by the validator below.[m
[36m@@ -371,6 +384,24 @@[m [mdef workflow_ref_from_env(env: Mapping[str, str]) -> Optional[str]:[m
     return value or None[m
 [m
 [m
[32m+[m[32mdef agent_config_from_env(env: Mapping[str, str]) -> Optional[str]:[m
[32m+[m[32m    """Read this run's selected agent config from ``AGENT_CONFIG``.[m
[32m+[m
[32m+[m[32m    The resolver emits the raw ``--config`` value (issue #1389) so the state[m
[32m+[m[32m    built after the run records it; the child process itself uses the resolved[m
[32m+[m[32m    ``AGENT_CONFIG_PATH``. An absent or empty value maps to ``None`` so a run[m
[32m+[m[32m    that selected nothing preserves the previous state's value.[m
[32m+[m
[32m+[m[32m    Args:[m
[32m+[m[32m        env: Environment mapping (normally ``os.environ``).[m
[32m+[m
[32m+[m[32m    Returns:[m
[32m+[m[32m        The non-empty ``AGENT_CONFIG`` value, or ``None``.[m
[32m+[m[32m    """[m
[32m+[m[32m    value = (env.get("AGENT_CONFIG") or "").strip()[m
[32m+[m[32m    return value or None[m
[32m+[m
[32m+[m
 def to_json(state: WorkflowState) -> str:[m
     from .features.to_json.to_json import state_to_json[m
     return state_to_json(state)[m
[36m@@ -409,6 +440,7 @@[m [mdef go_next([m
     callback: Optional[str] = None,[m
     workflow_ref: Optional[str] = None,[m
     gate: Optional[GateLock] = None,[m
[32m+[m[32m    agent_config: Optional[str] = None,[m
 ) -> WorkflowState:[m
     from .features.build_state.build_state import build_state[m
     return build_state([m
[36m@@ -436,4 +468,5 @@[m [mdef go_next([m
         callback=callback,[m
         workflow_ref=workflow_ref,[m
         gate=gate,[m
[32m+[m[32m        agent_config=agent_config,[m
     )[m
