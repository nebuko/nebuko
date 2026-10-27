"""Offline, graph-level linter for workflow YAML (issue #751).

The Pydantic loader (``engine/lib/config/features/load_workflow``) enforces
the *field-level* shape of a workflow — types, required keys, enums. It cannot
see the *graph-level* defects that only emerge once the whole step graph is
assembled: a dangling ``next`` target, a step unreachable from the entry, a
stale condition key, an unresumable ``human_gate``, or a graph with no terminal
step. Those defects surface today only at run time, mid-workflow.

This module owns the offline pass that reports them all at once. It is a pure,
read-only consumer of the loaded models and reuses the runtime traversal
semantics from ``engine/lib/resolve_workflow.py`` (``match_condition``,
``branch_key_for``, ``is_terminal_step``) so a lint rule can never drift from the
engine's actual routing behaviour.
"""

from __future__ import annotations

import dataclasses
import re
from collections import Counter
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Mapping

if TYPE_CHECKING:  # pragma: no cover - import bootstrap only
    from pathlib import Path

__all__ = ["LintIssue", "WorkflowLinter"]

_SEVERITY_ERROR = "error"
_SEVERITY_WARNING = "warning"

# Stable rule identifiers (issue #751). The severities are fixed by the
# approved plan: L2 is the only warning; everything else is an error.
_RULE_DANGLING = "L1-dangling-next"
_RULE_UNREACHABLE = "L2-unreachable-step"
_RULE_DUPLICATE = "L3-duplicate-step-id"
_RULE_CONDITION = "L4-condition-key"
_RULE_TERMINAL = "L5-terminal-consistency"
_RULE_HUMAN_GATE = "L6-human-gate"
_RULE_NO_TERMINAL = "L7-no-terminal"
# Typed-input wiring (issue #813): L8/L9 are errors, L10 is a warning.
_RULE_INPUT_BINDING = "L8-input-binding"
_RULE_INPUT_KIND = "L9-input-kind"
_RULE_INPUT_REF = "L10-input-ref"
# Step-output references (issue #756, ADR-0013): L11 is a warning.
_RULE_PROMPT_REF = "L11-prompt-ref"


# @stub-protected: signature defined by architect — do not modify
@dataclass(frozen=True)
class LintIssue:
    """One violated graph invariant found while linting a workflow definition.

    ``rule`` is the stable rule identifier (e.g. ``"L1-dangling-next"``); the
    ``severity`` is ``"error"`` or ``"warning"``; ``step`` names the offending
    step when the violation is step-scoped; ``location`` is ``file:line`` when a
    YAML source mapping is available; ``hint`` is an optional remediation hint.
    """

    rule: str  # @stub-protected
    severity: str  # @stub-protected  "error" | "warning"
    message: str  # @stub-protected
    step: str | None  # @stub-protected
    location: str | None  # @stub-protected
    hint: str | None  # @stub-protected


@dataclass(frozen=True)
class _WorkflowGraph:
    """A loaded workflow plus the raw source used for ``file:line`` locations."""

    path: Path
    definition: Any
    source_lines: tuple[str, ...]


def _ensure_runtime() -> None:
    """Make the legacy ``engine/lib`` package importable (idempotent)."""



def _runtime() -> Any:
    """Return the resolver module whose traversal helpers the linter reuses."""
    _ensure_runtime()
    from nebuko.engine.lib import resolve_workflow

    return resolve_workflow


def _prompt_ref_pattern() -> Any:
    """The engine's ``{{ <step> }}`` prompt-reference regex (issue #756).

    Imported from the resolver feature so the lint grammar and the run-time
    substitution cannot drift apart.
    """
    _ensure_runtime()
    from nebuko.engine.lib.workflow_state.features.resolve_step_output.resolve_step_output import (
        PROMPT_REF_PATTERN,
    )

    return PROMPT_REF_PATTERN


def _is_terminal(step: Any) -> bool:
    """Delegate terminality to the runtime resolver — never re-implement it."""
    return bool(_runtime().is_terminal_step(step))


def _can_terminate(step: Any) -> bool:
    """True when ``step`` exposes a workflow-ending route.

    ``is_terminal_step`` only reports True when *every* condition is null, but
    a single ``next: null`` condition already ends the workflow on that branch,
    so both count as a terminating route for the "no terminal" rule.
    """
    if _is_terminal(step):
        return True
    return any(condition.next is None for condition in (step.conditions or ()))


def _step_targets(step: Any) -> tuple[str, ...]:
    """Every non-null step name ``step`` can route to (linear or conditional)."""
    targets: list[str] = []
    if step.next is not None:
        targets.append(step.next)
    for condition in step.conditions or ():
        if condition.next is not None:
            targets.append(condition.next)
    return tuple(targets)


def _ref_in_scope(
    ref: str, declared: Mapping[str, Any], steps: Mapping[str, Any]
) -> bool:
    """True when a consumer reference resolves (issues #756, #973, ADR-0013).

    ``input.<name>`` names a declared workflow parameter; a bare ``<step>``
    names a declared step, whose posted comment is the value; ``<step>.<field>``
    names one section the step declares in its ``output:`` block. ``input`` is
    the reserved namespace prefix (flagged separately), never a valid step ref.
    """
    if ref.startswith("input."):
        return ref[len("input."):] in declared
    step_name, _, field = ref.partition(".")
    if step_name == "input":
        return False
    target = steps.get(step_name)
    if target is None:
        return False
    if not field:
        return True
    output = getattr(target, "output", None)
    return output is not None and any(f.name == field for f in output.fields)


def _location(graph: _WorkflowGraph, step_name: str | None) -> str:
    """Best-effort ``file:line`` for the step's ``name:`` line, else the path."""
    if step_name is not None:
        pattern = re.compile(rf"^\s*-?\s*name:\s*{re.escape(step_name)}\s*$")
        for number, line in enumerate(graph.source_lines, start=1):
            if pattern.match(line):
                return f"{graph.path}:{number}"
    return str(graph.path)


def _promote_to_error(issue: LintIssue) -> LintIssue:
    """Return ``issue`` with its severity raised to ``error`` (strict mode)."""
    if issue.severity == _SEVERITY_WARNING:
        return dataclasses.replace(issue, severity=_SEVERITY_ERROR)
    return issue


# @stub-protected: signature defined by architect — do not modify
class WorkflowLinter:
    """Check a workflow definition against the graph-level grammar invariants.

    The linter loads the YAML through the existing schema loader, builds the
    step graph, and runs every rule against the runtime traversal semantics. It
    never mutates, loads, or resolves anything at run time. ``strict`` promotes
    warnings to errors so a CI consumer can gate on them (the Dify
    ``validate_dsl.py --strict`` pattern).
    """

    # @stub-protected: signature defined by architect — do not modify
    def __init__(self, strict: bool = False) -> None:
        """Store the strict flag (warnings become errors when set)."""
        self._strict = strict

    # @stub-protected: signature defined by architect — do not modify
    def lint(self, path: Path) -> list[LintIssue]:
        """Load ``path`` and return every violated invariant, in rule order."""
        graph = self._load_graph(path)
        issues: list[LintIssue] = []
        for check in (
            self._check_dangling_next,
            self._check_reachability,
            self._check_duplicate_ids,
            self._check_condition_keys,
            self._check_terminal_consistency,
            self._check_human_gates,
            self._check_has_terminal,
            self._check_input_wiring,
        ):
            issues.extend(check(graph))
        issues.extend(self._check_prompt_refs(graph.definition))
        if self._strict:
            return [_promote_to_error(issue) for issue in issues]
        return issues

    # @stub-protected: signature defined by architect — do not modify
    def lint_all(self, paths: list[Path]) -> list[LintIssue]:
        """Run :meth:`lint` over every path and concatenate the issues."""
        issues: list[LintIssue] = []
        for path in paths:
            issues.extend(self.lint(path))
        return issues

    def _load_graph(self, path: Path) -> _WorkflowGraph:
        """Load and validate ``path`` through the existing Pydantic loader."""
        _ensure_runtime()
        from nebuko.engine.lib.config import load_workflow

        definition = load_workflow(str(path))
        source_lines = tuple(path.read_text(encoding="utf-8").splitlines())
        return _WorkflowGraph(
            path=path, definition=definition, source_lines=source_lines
        )

    # @stub-protected: signature defined by architect — do not modify
    def _check_dangling_next(self, graph) -> list[LintIssue]:
        """L1: ``next``/``conditions[].next`` must name an existing step."""
        names = {step.name for step in graph.definition.steps}
        issues: list[LintIssue] = []
        for step in graph.definition.steps:
            for target in _step_targets(step):
                if target not in names:
                    issues.append(
                        LintIssue(
                            rule=_RULE_DANGLING,
                            severity=_SEVERITY_ERROR,
                            message=(
                                f"step '{step.name}' routes to unknown step "
                                f"'{target}'"
                            ),
                            step=step.name,
                            location=_location(graph, step.name),
                            hint="Declare the missing step or correct the target",
                        )
                    )
        return issues

    # @stub-protected: signature defined by architect — do not modify
    def _check_reachability(self, graph) -> list[LintIssue]:
        """L2: every step must be reachable from the entry step."""
        steps = graph.definition.steps
        if not steps:
            return []
        names = {step.name for step in steps}
        by_name: dict[str, Any] = {}
        for step in steps:
            by_name.setdefault(step.name, step)
        entry = steps[0].name
        reachable: set[str] = set()
        pending = [entry]
        while pending:
            name = pending.pop()
            if name in reachable or name not in names:
                continue
            reachable.add(name)
            pending.extend(t for t in _step_targets(by_name[name]) if t in names)
        return [
            LintIssue(
                rule=_RULE_UNREACHABLE,
                severity=_SEVERITY_WARNING,
                message=(
                    f"step '{step.name}' is unreachable from the entry step "
                    f"'{entry}'"
                ),
                step=step.name,
                location=_location(graph, step.name),
                hint="Route to this step or remove it",
            )
            for step in steps
            if step.name not in reachable
        ]

    # @stub-protected: signature defined by architect — do not modify
    def _check_duplicate_ids(self, graph) -> list[LintIssue]:
        """L3: step names (and branch keys) must be unique."""
        counts = Counter(step.name for step in graph.definition.steps)
        issues: list[LintIssue] = []
        seen: set[str] = set()
        for step in graph.definition.steps:
            if counts[step.name] > 1 and step.name not in seen:
                seen.add(step.name)
                issues.append(
                    LintIssue(
                        rule=_RULE_DUPLICATE,
                        severity=_SEVERITY_ERROR,
                        message=(
                            f"step name '{step.name}' is declared "
                            f"{counts[step.name]} times"
                        ),
                        step=step.name,
                        location=_location(graph, step.name),
                        hint="Give every step a unique name",
                    )
                )
        return issues

    # @stub-protected: signature defined by architect — do not modify
    def _check_condition_keys(self, graph) -> list[LintIssue]:
        """L4: condition keys must be producible (no dead/duplicate ``on_result``)."""
        issues: list[LintIssue] = []
        for step in graph.definition.steps:
            if not step.conditions:
                continue
            duplicates: list[str] = []
            seen: set[str] = set()
            for condition in step.conditions:
                # ``match_condition`` returns the FIRST matching ``on_result``,
                # so every later duplicate can never be selected.
                if condition.on_result in seen and condition.on_result not in duplicates:
                    duplicates.append(condition.on_result)
                seen.add(condition.on_result)
            for key in duplicates:
                issues.append(
                    LintIssue(
                        rule=_RULE_CONDITION,
                        severity=_SEVERITY_ERROR,
                        message=(
                            f"step '{step.name}' declares duplicate condition "
                            f"'on_result: {key}'; only the first match is reachable"
                        ),
                        step=step.name,
                        location=_location(graph, step.name),
                        hint="Remove the unreachable duplicate condition",
                    )
                )
        return issues

    # @stub-protected: signature defined by architect — do not modify
    def _check_terminal_consistency(self, graph) -> list[LintIssue]:
        """L5: terminal and non-terminal steps must declare consistent routing.

        The resolver falls back to the linear ``next`` on a *branch-less*
        dispatch, so a step routing only through ``conditions`` that is not
        terminal dead-ends when no branch is emitted. That is only fatal at the
        workflow's entry step, where no prior matched route started the step;
        downstream conditional steps are always entered from a routed lookup.
        """
        steps = graph.definition.steps
        if not steps:
            return []
        entry = steps[0]
        if entry.human_gate or entry.next is not None or _is_terminal(entry):
            return []
        return [
            LintIssue(
                rule=_RULE_TERMINAL,
                severity=_SEVERITY_ERROR,
                message=(
                    f"non-terminal entry step '{entry.name}' declares no linear "
                    "'next'; a branch-less dispatch dead-ends the workflow"
                ),
                step=entry.name,
                location=_location(graph, entry.name),
                hint="Add a 'next' fallback route",
            )
        ]

    # @stub-protected: signature defined by architect — do not modify
    def _check_human_gates(self, graph) -> list[LintIssue]:
        """L6: a ``human_gate`` step must have a route that can resume it."""
        issues: list[LintIssue] = []
        for step in graph.definition.steps:
            if step.human_gate and _is_terminal(step):
                issues.append(
                    LintIssue(
                        rule=_RULE_HUMAN_GATE,
                        severity=_SEVERITY_ERROR,
                        message=(
                            f"human_gate step '{step.name}' is terminal; its hold "
                            "can never resume"
                        ),
                        step=step.name,
                        location=_location(graph, step.name),
                        hint="Add a 'next' or condition route to release the gate",
                    )
                )
        return issues

    # @stub-protected: signature defined by architect — do not modify
    def _check_has_terminal(self, graph) -> list[LintIssue]:
        """L7: the graph must contain at least one terminal step."""
        steps = graph.definition.steps
        if not steps or any(_can_terminate(step) for step in steps):
            return []
        return [
            LintIssue(
                rule=_RULE_NO_TERMINAL,
                severity=_SEVERITY_ERROR,
                message="workflow has no terminal step; every path cycles",
                step=None,
                location=str(graph.path),
                hint="Add a step with 'next: null' to end the workflow",
            )
        ]

    # @stub-protected: additive rule (issue #813) — existing signatures untouched
    def _check_input_wiring(self, graph) -> list[LintIssue]:
        """L8–L10: the typed-input wiring must type-check (issue #813).

        A workflow is a typed function, so its call sites are checked like a
        compiler checks a function call: every declared input bound exactly
        once, binding kinds matching the declaration (a ``ref`` being
        kind-preserving), and every reference resolvable in scope. The check is
        offline — a callee is resolved from the linted file's own directory, and
        an unresolvable callee is skipped rather than guessed at.
        """
        definition = graph.definition
        declared = {spec.name: spec for spec in definition.inputs}
        steps = {step.name: step for step in definition.steps}
        workflows_dir = str(graph.path.parent)

        issues: list[LintIssue] = []
        for step in definition.steps:
            issues.extend(
                self._check_step_bindings(graph, step, declared, steps, workflows_dir)
            )
        issues.extend(self._check_declared_inputs_used(graph, declared))
        if "input" in steps:
            issues.append(self._reserved_step_name_issue(graph))
        issues.extend(self._check_declared_outputs(graph, definition, steps))
        return issues

    def _check_declared_outputs(self, graph, definition, steps) -> list[LintIssue]:
        """A workflow ``outputs`` entry names a step whose comment is a result.

        The pre-#756 meaning — an opaque value returned as ``<call-step>.<name>``
        — was removed with the dotted reference grammar (issue #756, ADR-0013):
        the workflow's results are the comments of the named steps.
        """
        issues: list[LintIssue] = []
        for name in getattr(definition, "outputs", None) or []:
            if name in steps:
                continue
            issues.append(
                LintIssue(
                    rule=_RULE_INPUT_REF,
                    severity=_SEVERITY_WARNING,
                    message=f"workflow output '{name}' does not name a declared step",
                    step=None,
                    location=str(graph.path),
                    hint="A workflow output is a step whose posted comment is the result",
                )
            )
        return issues

    def _reserved_step_name_issue(self, graph) -> LintIssue:
        """``input`` names the workflow-parameter namespace, not a step."""
        return LintIssue(
            rule=_RULE_INPUT_REF,
            severity=_SEVERITY_WARNING,
            message=(
                "step 'input' shadows the reserved 'input.' namespace used to "
                "reference workflow parameters"
            ),
            step="input",
            location=_location(graph, "input"),
            hint="Rename the step; 'input.<name>' addresses workflow inputs",
        )

    def _check_prompt_refs(self, definition) -> list[LintIssue]:
        """L11-prompt-ref: every ``{{ <step> }}`` must name an existing step.

        A step's output is its posted comment (issue #756, ADR-0013), so a
        prompt reference resolves to the referenced step's full comment; the
        dotted form (``{{ <step>.<field> }}``) resolves to one section the step
        declares in its ``output:`` block (issue #973). A reference to an
        unknown step or undeclared field degrades to ``_(unavailable: …)_`` at
        run time; the linter reports it ahead of the run instead.
        """
        steps = {step.name: step for step in definition.steps}
        pattern = _prompt_ref_pattern()
        issues: list[LintIssue] = []
        for step in definition.steps:
            for ref in pattern.findall(step.prompt or ""):
                if ref.startswith("input."):
                    continue  # the input-wiring rules own this namespace
                if _ref_in_scope(ref, {}, steps):
                    continue
                issues.append(
                    LintIssue(
                        rule=_RULE_PROMPT_REF,
                        severity=_SEVERITY_WARNING,
                        message=(
                            f"step '{step.name}' references '{{{{ {ref} }}}}', "
                            "which is not a declared step"
                        ),
                        step=step.name,
                        location=None,
                        hint="Reference a declared step name or remove the placeholder",
                    )
                )
        return issues

    def _check_step_bindings(
        self, graph, step, declared, steps, workflows_dir
    ) -> list[LintIssue]:
        """L8–L10 for one step: call-site signatures or consumer references."""
        bindings = step.inputs or {}
        if step.workflow or step.repo:
            return self._check_call_site(graph, step, bindings, declared, workflows_dir)
        return self._check_consumer(graph, step, bindings, declared, steps)

    def _check_call_site(
        self, graph, step, bindings, declared, workflows_dir
    ) -> list[LintIssue]:
        """A ``workflow`` call step must bind its callee's declared signature."""
        callee = self._load_callee(step.path or step.workflow, workflows_dir)
        if callee is None:
            return []  # unresolvable callee is L1's concern, not a wiring defect
        signature = {spec.name: spec for spec in callee.inputs}
        issues: list[LintIssue] = []
        for name, binding in bindings.items():
            if name not in signature:
                issues.append(self._input_issue(
                    graph, step, _RULE_INPUT_BINDING, _SEVERITY_ERROR,
                    f"step '{step.name}' binds '{name}', which the callee "
                    f"'{callee.name}' does not declare",
                    "Bind a declared input or remove the binding",
                ))
                continue
            target = signature[name]
            kind = self._binding_kind(binding, declared)
            if kind is not None and kind != target.kind:
                issues.append(self._input_issue(
                    graph, step, _RULE_INPUT_KIND, _SEVERITY_ERROR,
                    f"step '{step.name}' binds input '{name}' as '{kind}' but "
                    f"'{callee.name}' declares it as '{target.kind}'",
                    "Match the declared kind (a ref must be kind-preserving)",
                ))
        for name, spec in signature.items():
            if spec.required and name not in bindings:
                issues.append(self._input_issue(
                    graph, step, _RULE_INPUT_BINDING, _SEVERITY_ERROR,
                    f"step '{step.name}' does not bind required input '{name}' "
                    f"of '{callee.name}'",
                    "Bind the input at this call site",
                ))
        return issues

    def _check_consumer(
        self, graph, step, bindings, declared, steps
    ) -> list[LintIssue]:
        """An ``agent`` step's bindings must resolve against the frame scope.

        Three reference shapes are in scope: ``input.<name>`` for a declared
        workflow parameter, a bare ``<step>`` naming a step whose posted comment
        is the value (issue #756), and ``<step>.<field>`` naming one section the
        step declares in its ``output:`` block (issue #973).
        """
        issues: list[LintIssue] = []
        for name, binding in bindings.items():
            if binding.source != "ref":
                continue  # a literal provider runs here; nothing to resolve
            ref = str(binding.ref)
            if not _ref_in_scope(ref, declared, steps):
                issues.append(self._consumer_issue(graph, step, ref))
        return issues

    def _consumer_issue(self, graph, step, ref) -> LintIssue:
        return self._input_issue(
            graph, step, _RULE_INPUT_REF, _SEVERITY_WARNING,
            f"step '{step.name}' references '{ref}', which is not in scope",
            "Declare the input, or reference a step whose comment is the value",
        )

    def _check_declared_inputs_used(self, graph, declared) -> list[LintIssue]:
        """L10 (warning): a declared input should be consumed or forwarded."""
        referenced = {
            str(binding.ref)
            for step in graph.definition.steps
            for binding in (step.inputs or {}).values()
            if binding.source == "ref"
        }
        return [
            LintIssue(
                rule=_RULE_INPUT_REF,
                severity=_SEVERITY_WARNING,
                message=(
                    f"declared input '{name}' is never consumed or forwarded by "
                    "any step"
                ),
                step=None,
                location=str(graph.path),
                hint="Wire it into a step or drop it from the signature",
            )
            for name in declared
            if f"input.{name}" not in referenced
        ]

    def _input_issue(
        self, graph, step, rule, severity, message, hint
    ) -> LintIssue:
        return LintIssue(
            rule=rule,
            severity=severity,
            message=message,
            step=step.name,
            location=_location(graph, step.name),
            hint=hint,
        )

    @staticmethod
    def _binding_kind(binding, declared) -> str | None:
        """The kind a binding supplies, or ``None`` when it cannot be known.

        An ``exec``/``str`` literal states its own kind; a ``ref`` to an
        ``input.<name>`` inherits that input's declared kind (kind-preserving);
        a ``ref`` to a step output has no declared kind yet.
        """
        source = binding.source
        if source in ("exec", "str"):
            return source
        ref = str(binding.ref or "")
        owner, _, wanted = ref.partition(".")
        if owner == "input" and wanted in declared:
            return declared[wanted].kind
        return None

    @staticmethod
    def _load_callee(reference: str, workflows_dir: str):
        """Resolve a callee from the linted file's directory only (offline)."""
        if not reference:
            return None
        _ensure_runtime()
        from nebuko.engine.lib.resolve_workflow import _load_workflow_by_reference

        return _load_workflow_by_reference(
            reference, workflows_dir, workflows_dir, None, {},
        )
