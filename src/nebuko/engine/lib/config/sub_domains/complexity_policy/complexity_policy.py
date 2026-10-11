"""Layered, repo-tunable complexity policy domain.

The complexity governance subsystem (issue #869) maps a run's *evidence* — LLM
metrics, human interaction signals, and intake context — onto a complexity
*tier* under a policy resolved from layered config. Two layers exist:

* ``builtin`` — the shipped rubric, defined in code (this module).
* ``repo`` — an optional per-repository override loaded from the same
  agent-config source as ``agents.<name>.complexity_overrides``.

Sections merge independently (``None`` inherits the lower layer), so a repo can
retune just ``target_distribution`` without redefining the bands. Policies are
declarative data only — never executable plugin code (the plan explicitly
rejects running repo code as governance logic).

This module is deliberately dependency-light (pydantic only): it is imported by
``config`` and therefore must not reach into ``workflow_state`` or the agent
runtime.
"""

from typing import Literal, Optional

from pydantic import BaseModel, Field

#: A complexity tier.
Tier = Literal["low", "medium", "high"]

#: Evidence families a signal rule may read.
Family = Literal["llm", "human", "context"]

#: Ordered tiers, lowest first — the shared index used by delta/clamp helpers.
TIERS: tuple[Tier, ...] = ("low", "medium", "high")

# ── Builtin signal names (referenced verbatim by band gates) ──────────────
MULTI_ISSUE_LINKAGE = "multi-issue-linkage"
REOPENED_ISSUE = "reopened-issue"
HIGH_TOKEN_BURN = "high-token-burn"
LARGE_SCOPE = "large-scope"
HUMAN_FRICTION = "human-friction"

#: Signals that can, on their own strength, justify the HIGH tier.
BUILTIN_HIGH_SIGNALS: tuple[str, ...] = (
    MULTI_ISSUE_LINKAGE,
    REOPENED_ISSUE,
    HIGH_TOKEN_BURN,
    LARGE_SCOPE,
    HUMAN_FRICTION,
)


class MetricBound(BaseModel):
    """Inclusive numeric bound applied to one evidence field."""

    min: Optional[float] = None  # @stub-protected
    max: Optional[float] = None  # @stub-protected


class Envelope(BaseModel):
    """Per-family evidence bounds declared by a band (declarative calibration).

    The envelope documents the numeric range a band expects for each evidence
    family; it is surfaced to repositories and humans as config, not enforced as
    a hard runtime gate.
    """

    llm: dict[str, MetricBound] = Field(default_factory=dict)  # @stub-protected
    human: dict[str, MetricBound] = Field(default_factory=dict)  # @stub-protected
    context: dict[str, MetricBound] = Field(default_factory=dict)  # @stub-protected


class SignalRule(BaseModel):
    """A single declarative threshold over one evidence field.

    ``field`` addresses a member of the ``family``'s evidence mapping: an LLM
    metric name from :mod:`metrics_schema`, or a ``HumanSignals`` /
    ``ContextSignals`` attribute.
    """

    signal: str  # @stub-protected
    family: Family  # @stub-protected
    field: str  # @stub-protected
    threshold: float  # @stub-protected
    direction: Literal["gte", "lte"] = "gte"  # @stub-protected

    def fires(self, value: float) -> bool:
        """Whether ``value`` satisfies this rule's threshold/direction."""
        if self.direction == "gte":
            return value >= self.threshold
        return value <= self.threshold


class ComplexityBand(BaseModel):
    """A tier's classification gate and human-readable guidance."""

    min_score: int  # @stub-protected
    all_of: list[str] = Field(default_factory=list)  # @stub-protected
    any_of: list[str] = Field(default_factory=list)  # @stub-protected
    guidance: str  # @stub-protected
    envelope: Envelope = Field(default_factory=Envelope)  # @stub-protected


class ComplexityPolicyLayer(BaseModel):
    """One layer of policy config; ``None`` sections inherit the lower layer."""

    scope: Literal["builtin", "repo"]  # @stub-protected
    bands: Optional[dict[Tier, ComplexityBand]] = None  # @stub-protected
    signal_rules: Optional[list[SignalRule]] = None  # @stub-protected
    target_distribution: Optional[dict[str, float]] = None  # @stub-protected
    auto_adjust: Optional[bool] = None  # @stub-protected
    max_auto_delta: Optional[int] = None  # @stub-protected


class ComplexityPolicy(BaseModel):
    """A fully-resolved complexity policy (builtin merged with any repo layer)."""

    bands: dict[Tier, ComplexityBand]  # @stub-protected
    signal_rules: list[SignalRule]  # @stub-protected
    target_distribution: dict[str, float]  # @stub-protected
    default_tier: Tier = "medium"  # @stub-protected
    downward_bias: bool = True  # @stub-protected
    auto_adjust: bool = True  # @stub-protected
    max_auto_delta: int = 1  # @stub-protected

    @classmethod
    def resolve(cls, layers: list[ComplexityPolicyLayer]) -> "ComplexityPolicy":  # @stub-protected
        """Merge an ordered ``builtin -> repo`` layer stack into one policy.

        Each section (``bands``, ``signal_rules``, ``target_distribution``,
        ``auto_adjust``, ``max_auto_delta``) is taken from the last layer that
        sets it, so a repo layer overrides only the sections it declares.

        Raises:
            ValueError: when no layers are supplied, when the first layer is not
                the ``builtin`` scope, or when a required section is unresolved.
        """
        if not layers:
            raise ValueError("at least one complexity policy layer is required")
        if layers[0].scope != "builtin":
            raise ValueError("the first complexity policy layer must be 'builtin' scope")

        bands: Optional[dict[Tier, ComplexityBand]] = None
        signal_rules: Optional[list[SignalRule]] = None
        target_distribution: Optional[dict[str, float]] = None
        auto_adjust: Optional[bool] = None
        max_auto_delta: Optional[int] = None
        for layer in layers:
            if layer.bands is not None:
                bands = layer.bands
            if layer.signal_rules is not None:
                signal_rules = layer.signal_rules
            if layer.target_distribution is not None:
                target_distribution = layer.target_distribution
            if layer.auto_adjust is not None:
                auto_adjust = layer.auto_adjust
            if layer.max_auto_delta is not None:
                max_auto_delta = layer.max_auto_delta

        missing = [
            name
            for name, value in (
                ("bands", bands),
                ("signal_rules", signal_rules),
                ("target_distribution", target_distribution),
            )
            if value is None
        ]
        if missing:
            raise ValueError(
                f"complexity policy layers did not resolve: {', '.join(missing)}"
            )

        return cls(
            bands=bands,
            signal_rules=signal_rules,
            target_distribution=target_distribution,
            auto_adjust=True if auto_adjust is None else auto_adjust,
            max_auto_delta=1 if max_auto_delta is None else max_auto_delta,
        )

    def _read(self, evidence, family: Family, field: str) -> Optional[float]:
        """Read one evidence field as a float, or ``None`` when absent/unset."""
        if family == "llm":
            raw = (evidence.llm or {}).get(field)
        else:
            model = evidence.human if family == "human" else evidence.context
            raw = getattr(model, field, None)
        if raw is None:
            return None
        if isinstance(raw, bool):
            return 1.0 if raw else 0.0
        try:
            return float(raw)
        except (TypeError, ValueError):
            return None

    def derive_signals(self, evidence) -> set[str]:  # @stub-protected
        """Return the set of ``SignalRule.signal`` names that fire on ``evidence``."""
        fired: set[str] = set()
        for rule in self.signal_rules:
            value = self._read(evidence, rule.family, rule.field)
            if value is not None and rule.fires(value):
                fired.add(rule.signal)
        return fired

    def _score(self, evidence) -> int:  # @stub-protected
        """Strength score: each fired rule contributes its field magnitude (>=1).

        Magnitude weighting means a stronger observation (e.g. many linked
        issues or repeated change-requests) counts for more than a marginal one,
        which is what lets a genuinely heavy issue clear the HIGH gate.
        """
        score = 0
        for rule in self.signal_rules:
            value = self._read(evidence, rule.family, rule.field)
            if value is not None and rule.fires(value):
                score += max(1, int(value))
        return score

    def _band_accepts(self, band: ComplexityBand, signals: set[str]) -> bool:
        """Whether a band's ``all_of``/``any_of`` gates are satisfied."""
        if band.all_of and not all(s in signals for s in band.all_of):
            return False
        if band.any_of and not any(s in signals for s in band.any_of):
            return False
        return True

    def classify(self, evidence) -> Tier:  # @stub-protected
        """Map an evidence bundle onto a tier (the intake remap).

        Thin, signal-free evidence falls back to ``default_tier`` — or to
        ``low`` when ``downward_bias`` is set, so an unevidenced issue is not
        pre-emptively priced as a heavy one.
        """
        signals = self.derive_signals(evidence)
        score = self._score(evidence)
        chosen: Tier = self.default_tier
        best: Optional[Tier] = None
        for name in TIERS:
            band = self.bands[name]
            if score >= band.min_score and self._band_accepts(band, signals):
                best = name
        if best is not None:
            chosen = best
        if not signals and self.downward_bias:
            chosen = "low"
        return chosen


# ── Builtin layer ─────────────────────────────────────────────────────────

BUILTIN_GUIDANCE: dict[Tier, str] = {
    "low": (
        "LOW — localized, single-concern change: one file or a tight cluster, "
        "no design ambiguity, no cross-module contract change, no open "
        "questions. If it fits in a single focused edit, it is LOW."
    ),
    "medium": (
        "MEDIUM — the default working tier: a bounded multi-file change with a "
        "clear approach, a couple of touch points, no unresolved design choice. "
        "Most feature work and bug fixes belong here."
    ),
    "high": (
        "HIGH — reserved for genuinely heavy work: multiple linked issues or "
        "reopened problem, cross-module/architectural change, sustained large "
        "context burn, or repeated human change-requests. Reach for HIGH only "
        "when the evidence below actually fires; an unproven HIGH erodes the "
        "cost/value of the tier."
    ),
}

#: The shipped rubric. Repos tune thresholds/distribution via a repo layer.
BUILTIN_LAYER = ComplexityPolicyLayer(
    scope="builtin",
    bands={
        "low": ComplexityBand(
            min_score=1,
            all_of=[],
            any_of=[],
            guidance=BUILTIN_GUIDANCE["low"],
            envelope=Envelope(),
        ),
        "medium": ComplexityBand(
            min_score=1,
            all_of=[],
            any_of=[],
            guidance=BUILTIN_GUIDANCE["medium"],
            envelope=Envelope(),
        ),
        "high": ComplexityBand(
            min_score=3,
            all_of=[],
            any_of=list(BUILTIN_HIGH_SIGNALS),
            guidance=BUILTIN_GUIDANCE["high"],
            envelope=Envelope(),
        ),
    },
    signal_rules=[
        SignalRule(
            signal=MULTI_ISSUE_LINKAGE,
            family="context",
            field="linked_issues",
            threshold=2.0,
        ),
        SignalRule(
            signal=REOPENED_ISSUE,
            family="context",
            field="reopened",
            threshold=1.0,
        ),
        SignalRule(
            signal=HIGH_TOKEN_BURN,
            family="llm",
            field="input_tokens",
            threshold=150000.0,
        ),
        SignalRule(
            signal=LARGE_SCOPE,
            family="llm",
            field="nb_tool_calls",
            threshold=40.0,
        ),
        SignalRule(
            signal=HUMAN_FRICTION,
            family="human",
            field="changes_requested",
            threshold=2.0,
        ),
    ],
    target_distribution={"low": 0.35, "medium": 0.50, "high": 0.15},
    auto_adjust=True,
    max_auto_delta=1,
)


def build_builtin_policy() -> ComplexityPolicy:
    """Resolve the shipped builtin layer into a standalone policy."""
    return ComplexityPolicy.resolve([BUILTIN_LAYER])
