"""Workflow entry-point sub-domain (issue #789).

Value object describing *where a fresh run enters a workflow graph*, backing the
``/nebuko run workflow --name <wf> [--step <step>]`` command.

This is deliberately distinct from ``/nebuko force-step``: ``force-step``
replays/forks a step inside an already-active run (it mutates that run's
state), while ``run --step`` seeds a brand-new run whose state points directly
at the requested step (default: the workflow's first step). Keeping the two
verbs semantically separate is what makes the routing cascade legible.

The object encapsulates the entry decision — workflow name, requested step,
and the shape-aware descent overrides produced when the target step is a
sub-workflow mount — replacing the ad-hoc ``first_step_overrides`` dict the
``run`` branch used to build for first-step descent (issue #609).
"""

from typing import Callable, Optional

from pydantic import BaseModel

from config import WorkflowDefinition, WorkflowStep

__all__ = ["WorkflowEntryPoint", "StepEntryResolver"]

#: Port for the shape-aware step resolution. The routing feature injects
#: ``_resolve_step_entry``; keeping it as a callable parameter means this value
#: object does not depend on the application layer.
StepEntryResolver = Callable[[WorkflowStep], dict]


class WorkflowEntryPoint(BaseModel):
    """A validated workflow-entry decision (workflow name + target step).

    Attributes:
        workflow: Top-level workflow named on the command line.
        step: Requested step name; ``""`` means the workflow's first step
            (backward compatible with a bare ``/nebuko run workflow --name``).
        task: Resolved agent role for the target step (populated by
            :meth:`resolve`).
        run_script: Resolved script-step marker when the target is a
            deterministic ``script`` step, else ``None``.
        config_workflow: Workflow the seeded state should point at. Equals
            ``workflow`` for a plain agent step, or the innermost mounted child
            when the target descends into a sub-workflow (issues #604/#609).
        config_step: Step the seeded state should point at; the innermost leaf
            step when the target descends into a sub-workflow.
        config_parent_workflow, config_parent_workflow_step: Parent reference
            recorded for return-to-parent when the target is a mount step.
        skip_reason: Fail-loud diagnostic when the entry is not runnable
            (unknown step, schema failure, missing sub-workflow scaffold).
    """

    workflow: str
    step: str = ""

    task: str = ""
    run_script: Optional[dict] = None
    config_workflow: str = ""
    config_step: str = ""
    config_parent_workflow: Optional[str] = None
    config_parent_workflow_step: Optional[str] = None

    skip_reason: Optional[str] = None

    def is_valid(self) -> bool:
        """True when the entry resolved to a runnable step."""
        return self.skip_reason is None

    def resolve(
        self,
        wf_def: WorkflowDefinition,
        step_entry_resolver: StepEntryResolver,
    ) -> "WorkflowEntryPoint":
        """Return a copy with the target step resolved to a dispatchable entry.

        Args:
            wf_def: The loaded top-level workflow definition.
            step_entry_resolver: Shape-aware port (``_resolve_step_entry``)
                that resolves the target ``WorkflowStep`` to a dispatch entry,
                descending sub-workflow mounts exactly like ``force-step``.

        Returns:
            A new :class:`WorkflowEntryPoint`. When the target cannot be
            resolved the copy carries a ``skip_reason`` and an empty ``task``.
        """
        if not wf_def.steps:
            return self._invalid(f"workflow '{self.workflow}' has no steps")

        target = self._select_step(wf_def)
        if target is None:
            return self._invalid(
                f"step '{self.step}' not found in workflow '{self.workflow}'"
            )

        entry = step_entry_resolver(target)
        if entry.get("skip_reason"):
            return self._invalid(entry["skip_reason"])
        if not entry.get("task") and not entry.get("run_script"):
            return self._invalid(f"step '{target.name}' has no runnable source")

        return self.model_copy(
            update={
                "task": entry.get("task", ""),
                "run_script": entry.get("run_script"),
                "config_workflow": entry.get("config_workflow") or self.workflow,
                "config_step": entry.get("config_step") or self.step,
                "config_parent_workflow": entry.get("config_parent_workflow"),
                "config_parent_workflow_step": entry.get(
                    "config_parent_workflow_step"
                ),
            }
        )

    def _select_step(self, wf_def: WorkflowDefinition) -> Optional[WorkflowStep]:
        """Return the target step, or ``None`` when a named step is absent."""
        if not self.step:
            return wf_def.steps[0]
        return next((s for s in wf_def.steps if s.name == self.step), None)

    def _invalid(self, reason: str) -> "WorkflowEntryPoint":
        """Return a copy flagged with ``reason`` and no runnable task."""
        # The shared ``_resolve_step_entry`` helper prefixes sub-workflow
        # diagnostics with its originating verb; drop it so a ``run --step``
        # failure reads naturally (issue #789).
        prefix = "force-next-step: "
        if reason.startswith(prefix):
            reason = reason[len(prefix):]
        return self.model_copy(update={"task": "", "skip_reason": reason})
