"""Frozen triage manifest for issue #827 (pre-publication issue triage).

Every entry is reviewable data: the disposition is the recommended human/agent
action, and ``labels`` is the additive label set to apply.  ``delete`` and ``scrub``
dispositions are human-owned: the applier never deletes or edits issue content.
Their ``needs-scrub`` label *is* applied (labels are additive and hide nothing),
which makes ``label:needs-scrub`` the single human review queue.

Issue #229 ("test"/"aaa", junk) was already deleted upstream and is intentionally
absent; the repository has no live junk issue.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Disposition = Literal["keep", "label", "migrate", "delete", "scrub"]
Category = Literal["competitive", "research", "strategy", "ideation", "secret", "junk"]

DESTRUCTIVE_DISPOSITIONS: frozenset[str] = frozenset({"delete", "scrub"})


@dataclass(frozen=True)
class LabelSpec:
    """A GitHub label to create (``color`` is 6-hex, no leading ``#``)."""

    name: str
    color: str
    description: str


@dataclass(frozen=True)
class TriageEntry:
    """One flagged issue and the disposition/labels the manifest recommends."""

    issue_number: int
    disposition: Disposition
    labels: tuple[str, ...]
    category: Category
    rationale: str


ARCHIVE_REPO = "nebuko/nebuko-archive"

LABELS: tuple[LabelSpec, ...] = (
    LabelSpec(
        "competitive-research",
        "B60205",
        "Competitor / market comparison; keep off the public surface",
    ),
    LabelSpec(
        "strategy",
        "5319E7",
        "Monetization, pricing, patent or go-public strategy",
    ),
    LabelSpec(
        "ideation",
        "0E8A16",
        "Feature thinking not yet implemented",
    ),
    LabelSpec(
        "needs-scrub",
        "FBCA04",
        "Human review queue: secrets or internal endpoints to remove before going public",
    ),
)

MANIFEST: tuple[TriageEntry, ...] = (
    TriageEntry(
        112,
        "scrub",
        ("needs-scrub",),
        "secret",
        "Internal endpoint/private IP in body or comment; human scrubs before the public flip.",
    ),
    TriageEntry(
        116,
        "scrub",
        ("needs-scrub",),
        "secret",
        "Internal endpoint/private IP in body or comment; human scrubs before the public flip.",
    ),
    TriageEntry(
        126,
        "migrate",
        ("strategy",),
        "strategy",
        "Strategy, patent or go-public operation; company-creation sensitive.",
    ),
    TriageEntry(
        130,
        "keep",
        ("research",),
        "research",
        "Technical research/spike; safe to publish, labelled for traceability.",
    ),
    TriageEntry(
        136,
        "migrate",
        ("research", "strategy"),
        "research",
        "Monetization/pricing/market research; company-creation sensitive.",
    ),
    TriageEntry(
        137,
        "migrate",
        ("strategy",),
        "strategy",
        "Strategy, patent or go-public operation; company-creation sensitive.",
    ),
    TriageEntry(
        139,
        "migrate",
        ("strategy",),
        "strategy",
        "Strategy, patent or go-public operation; company-creation sensitive.",
    ),
    TriageEntry(
        140,
        "migrate",
        ("research", "strategy"),
        "research",
        "Monetization/pricing/market research; company-creation sensitive.",
    ),
    TriageEntry(
        144,
        "migrate",
        ("research", "strategy"),
        "research",
        "Monetization/pricing/market research; company-creation sensitive.",
    ),
    TriageEntry(
        145,
        "migrate",
        ("research", "strategy"),
        "research",
        "Monetization/pricing/market research; company-creation sensitive.",
    ),
    TriageEntry(
        146,
        "migrate",
        ("research", "strategy"),
        "research",
        "Monetization/pricing/market research; company-creation sensitive.",
    ),
    TriageEntry(
        147,
        "keep",
        ("research",),
        "research",
        "Technical research/spike; safe to publish, labelled for traceability.",
    ),
    TriageEntry(
        148,
        "migrate",
        ("research", "strategy"),
        "research",
        "Monetization/pricing/market research; company-creation sensitive.",
    ),
    TriageEntry(
        164,
        "keep",
        ("ideation",),
        "ideation",
        "Feature thinking not yet implemented; roadmap ideation.",
    ),
    TriageEntry(
        170,
        "migrate",
        ("competitive-research",),
        "competitive",
        "Competitor/landscape comparison; keep off the public surface.",
    ),
    TriageEntry(
        185,
        "keep",
        ("research", "needs-scrub"),
        "research",
        "Technical research/spike; safe to publish, labelled for traceability. Also leaks an internal endpoint.",
    ),
    TriageEntry(
        227,
        "migrate",
        ("competitive-research",),
        "competitive",
        "Competitor/landscape comparison; keep off the public surface.",
    ),
    TriageEntry(
        273,
        "keep",
        ("research",),
        "research",
        "Technical research/spike; safe to publish, labelled for traceability.",
    ),
    TriageEntry(
        282,
        "delete",
        ("needs-scrub",),
        "secret",
        "Leaked credential in a comment; human must delete it (dev never deletes).",
    ),
    TriageEntry(
        286,
        "migrate",
        ("competitive-research",),
        "competitive",
        "Competitor/landscape comparison; keep off the public surface.",
    ),
    TriageEntry(
        289,
        "migrate",
        ("competitive-research",),
        "competitive",
        "Competitor/landscape comparison; keep off the public surface.",
    ),
    TriageEntry(
        291,
        "migrate",
        ("competitive-research",),
        "competitive",
        "Competitor/landscape comparison; keep off the public surface.",
    ),
    TriageEntry(
        292,
        "migrate",
        ("competitive-research", "needs-scrub"),
        "competitive",
        "Competitor/landscape comparison; keep off the public surface. Also leaks an internal endpoint.",
    ),
    TriageEntry(
        316,
        "migrate",
        ("competitive-research",),
        "competitive",
        "Competitor/landscape comparison; keep off the public surface.",
    ),
    TriageEntry(
        318,
        "migrate",
        ("competitive-research", "needs-scrub"),
        "competitive",
        "Competitor/landscape comparison; keep off the public surface. Also leaks an internal endpoint.",
    ),
    TriageEntry(
        319,
        "migrate",
        ("competitive-research",),
        "competitive",
        "Competitor/landscape comparison; keep off the public surface.",
    ),
    TriageEntry(
        396,
        "migrate",
        ("research", "strategy"),
        "research",
        "Monetization/pricing/market research; company-creation sensitive.",
    ),
    TriageEntry(
        403,
        "delete",
        ("needs-scrub",),
        "secret",
        "Leaked credential in a comment; human must delete it (dev never deletes).",
    ),
    TriageEntry(
        407,
        "scrub",
        ("needs-scrub",),
        "secret",
        "Internal endpoint/private IP in body or comment; human scrubs before the public flip.",
    ),
    TriageEntry(
        409,
        "keep",
        ("ideation",),
        "ideation",
        "Feature thinking not yet implemented; roadmap ideation.",
    ),
    TriageEntry(
        415,
        "keep",
        ("research",),
        "research",
        "Technical research/spike; safe to publish, labelled for traceability.",
    ),
    TriageEntry(
        419,
        "migrate",
        ("strategy",),
        "strategy",
        "Strategy, patent or go-public operation; company-creation sensitive.",
    ),
    TriageEntry(
        445,
        "keep",
        ("ideation",),
        "ideation",
        "Feature thinking not yet implemented; roadmap ideation.",
    ),
    TriageEntry(
        446,
        "keep",
        ("ideation",),
        "ideation",
        "Feature thinking not yet implemented; roadmap ideation.",
    ),
    TriageEntry(
        453,
        "migrate",
        ("competitive-research",),
        "competitive",
        "Competitor/landscape comparison; keep off the public surface.",
    ),
    TriageEntry(
        464,
        "scrub",
        ("needs-scrub",),
        "secret",
        "Internal endpoint/private IP in body or comment; human scrubs before the public flip.",
    ),
    TriageEntry(
        484,
        "migrate",
        ("competitive-research",),
        "competitive",
        "Competitor/landscape comparison; keep off the public surface.",
    ),
    TriageEntry(
        486,
        "scrub",
        ("needs-scrub",),
        "secret",
        "Internal endpoint/private IP in body or comment; human scrubs before the public flip.",
    ),
    TriageEntry(
        487,
        "migrate",
        ("research", "strategy"),
        "research",
        "Monetization/pricing/market research; company-creation sensitive.",
    ),
    TriageEntry(
        491,
        "migrate",
        ("research", "strategy"),
        "research",
        "Monetization/pricing/market research; company-creation sensitive.",
    ),
    TriageEntry(
        493,
        "migrate",
        ("strategy", "needs-scrub"),
        "strategy",
        "Strategy, patent or go-public operation; company-creation sensitive. Also leaks an internal endpoint.",
    ),
    TriageEntry(
        497,
        "migrate",
        ("strategy", "needs-scrub"),
        "strategy",
        "Strategy, patent or go-public operation; company-creation sensitive. Also leaks an internal endpoint.",
    ),
    TriageEntry(
        499,
        "migrate",
        ("strategy",),
        "strategy",
        "Strategy, patent or go-public operation; company-creation sensitive.",
    ),
    TriageEntry(
        503,
        "migrate",
        ("competitive-research",),
        "competitive",
        "Competitor/landscape comparison; keep off the public surface.",
    ),
    TriageEntry(
        511,
        "scrub",
        ("needs-scrub",),
        "secret",
        "Internal endpoint/private IP in body or comment; human scrubs before the public flip.",
    ),
    TriageEntry(
        519,
        "scrub",
        ("needs-scrub",),
        "secret",
        "Internal endpoint/private IP in body or comment; human scrubs before the public flip.",
    ),
    TriageEntry(
        520,
        "scrub",
        ("needs-scrub",),
        "secret",
        "Internal endpoint/private IP in body or comment; human scrubs before the public flip.",
    ),
    TriageEntry(
        523,
        "migrate",
        ("competitive-research",),
        "competitive",
        "Competitor/landscape comparison; keep off the public surface.",
    ),
    TriageEntry(
        525,
        "migrate",
        ("research", "strategy"),
        "research",
        "Monetization/pricing/market research; company-creation sensitive.",
    ),
    TriageEntry(
        526,
        "migrate",
        ("competitive-research", "needs-scrub"),
        "competitive",
        "Competitor/landscape comparison; keep off the public surface. Also leaks an internal endpoint.",
    ),
    TriageEntry(
        531,
        "migrate",
        ("competitive-research",),
        "competitive",
        "Competitor/landscape comparison; keep off the public surface.",
    ),
    TriageEntry(
        533,
        "migrate",
        ("research", "strategy", "needs-scrub"),
        "research",
        "Monetization/pricing/market research; company-creation sensitive. Also leaks an internal endpoint.",
    ),
    TriageEntry(
        545,
        "migrate",
        ("strategy",),
        "strategy",
        "Strategy, patent or go-public operation; company-creation sensitive.",
    ),
    TriageEntry(
        561,
        "migrate",
        ("competitive-research", "needs-scrub"),
        "competitive",
        "Competitor/landscape comparison; keep off the public surface. Also leaks an internal endpoint.",
    ),
    TriageEntry(
        563,
        "migrate",
        ("competitive-research",),
        "competitive",
        "Competitor/landscape comparison; keep off the public surface.",
    ),
    TriageEntry(
        565,
        "keep",
        ("ideation",),
        "ideation",
        "Feature thinking not yet implemented; roadmap ideation.",
    ),
    TriageEntry(
        566,
        "keep",
        ("ideation",),
        "ideation",
        "Feature thinking not yet implemented; roadmap ideation.",
    ),
    TriageEntry(
        567,
        "keep",
        ("ideation",),
        "ideation",
        "Feature thinking not yet implemented; roadmap ideation.",
    ),
    TriageEntry(
        577,
        "migrate",
        ("competitive-research",),
        "competitive",
        "Competitor/landscape comparison; keep off the public surface.",
    ),
    TriageEntry(
        578,
        "migrate",
        ("competitive-research",),
        "competitive",
        "Competitor/landscape comparison; keep off the public surface.",
    ),
    TriageEntry(
        579,
        "scrub",
        ("needs-scrub",),
        "secret",
        "Internal endpoint/private IP in body or comment; human scrubs before the public flip.",
    ),
    TriageEntry(
        580,
        "migrate",
        ("competitive-research", "needs-scrub"),
        "competitive",
        "Competitor/landscape comparison; keep off the public surface. Also leaks an internal endpoint.",
    ),
    TriageEntry(
        581,
        "migrate",
        ("competitive-research",),
        "competitive",
        "Competitor/landscape comparison; keep off the public surface.",
    ),
    TriageEntry(
        583,
        "migrate",
        ("strategy", "needs-scrub"),
        "strategy",
        "Strategy, patent or go-public operation; company-creation sensitive. Also leaks an internal endpoint.",
    ),
    TriageEntry(
        588,
        "migrate",
        ("strategy",),
        "strategy",
        "Strategy, patent or go-public operation; company-creation sensitive.",
    ),
    TriageEntry(
        591,
        "migrate",
        ("strategy",),
        "strategy",
        "Strategy, patent or go-public operation; company-creation sensitive.",
    ),
    TriageEntry(
        592,
        "migrate",
        ("strategy",),
        "strategy",
        "Strategy, patent or go-public operation; company-creation sensitive.",
    ),
    TriageEntry(
        593,
        "migrate",
        ("strategy",),
        "strategy",
        "Strategy, patent or go-public operation; company-creation sensitive.",
    ),
    TriageEntry(
        594,
        "migrate",
        ("strategy",),
        "strategy",
        "Strategy, patent or go-public operation; company-creation sensitive.",
    ),
    TriageEntry(
        595,
        "migrate",
        ("strategy",),
        "strategy",
        "Strategy, patent or go-public operation; company-creation sensitive.",
    ),
    TriageEntry(
        596,
        "migrate",
        ("strategy",),
        "strategy",
        "Strategy, patent or go-public operation; company-creation sensitive.",
    ),
    TriageEntry(
        597,
        "migrate",
        ("strategy",),
        "strategy",
        "Strategy, patent or go-public operation; company-creation sensitive.",
    ),
    TriageEntry(
        600,
        "scrub",
        ("needs-scrub",),
        "secret",
        "Internal endpoint/private IP in body or comment; human scrubs before the public flip.",
    ),
    TriageEntry(
        616,
        "migrate",
        ("competitive-research",),
        "competitive",
        "Competitor/landscape comparison; keep off the public surface.",
    ),
    TriageEntry(
        626,
        "scrub",
        ("needs-scrub",),
        "secret",
        "Internal endpoint/private IP in body or comment; human scrubs before the public flip.",
    ),
    TriageEntry(
        631,
        "migrate",
        ("competitive-research", "needs-scrub"),
        "competitive",
        "Competitor/landscape comparison; keep off the public surface. Also leaks an internal endpoint.",
    ),
    TriageEntry(
        633,
        "migrate",
        ("competitive-research", "needs-scrub"),
        "competitive",
        "Competitor/landscape comparison; keep off the public surface. Also leaks an internal endpoint.",
    ),
    TriageEntry(
        642,
        "migrate",
        ("competitive-research", "needs-scrub"),
        "competitive",
        "Competitor/landscape comparison; keep off the public surface. Also leaks an internal endpoint.",
    ),
    TriageEntry(
        656,
        "scrub",
        ("needs-scrub",),
        "secret",
        "Internal endpoint/private IP in body or comment; human scrubs before the public flip.",
    ),
    TriageEntry(
        657,
        "delete",
        ("needs-scrub",),
        "secret",
        "Leaked credential in a comment; human must delete it (dev never deletes).",
    ),
    TriageEntry(
        668,
        "scrub",
        ("needs-scrub",),
        "secret",
        "Internal endpoint/private IP in body or comment; human scrubs before the public flip.",
    ),
    TriageEntry(
        689,
        "migrate",
        ("competitive-research", "needs-scrub"),
        "competitive",
        "Competitor/landscape comparison; keep off the public surface. Also leaks an internal endpoint.",
    ),
    TriageEntry(
        690,
        "scrub",
        ("needs-scrub",),
        "secret",
        "Internal endpoint/private IP in body or comment; human scrubs before the public flip.",
    ),
    TriageEntry(
        696,
        "migrate",
        ("competitive-research",),
        "competitive",
        "Competitor/landscape comparison; keep off the public surface.",
    ),
    TriageEntry(
        697,
        "migrate",
        ("competitive-research", "needs-scrub"),
        "competitive",
        "Competitor/landscape comparison; keep off the public surface. Also leaks an internal endpoint.",
    ),
    TriageEntry(
        698,
        "migrate",
        ("competitive-research",),
        "competitive",
        "Competitor/landscape comparison; keep off the public surface.",
    ),
    TriageEntry(
        699,
        "migrate",
        ("competitive-research",),
        "competitive",
        "Competitor/landscape comparison; keep off the public surface.",
    ),
    TriageEntry(
        704,
        "delete",
        ("needs-scrub",),
        "secret",
        "Leaked credential in a comment; human must delete it (dev never deletes).",
    ),
    TriageEntry(
        707,
        "scrub",
        ("needs-scrub",),
        "secret",
        "Internal endpoint/private IP in body or comment; human scrubs before the public flip.",
    ),
    TriageEntry(
        709,
        "migrate",
        ("strategy",),
        "strategy",
        "Strategy, patent or go-public operation; company-creation sensitive.",
    ),
    TriageEntry(
        717,
        "scrub",
        ("needs-scrub",),
        "secret",
        "Internal endpoint/private IP in body or comment; human scrubs before the public flip.",
    ),
    TriageEntry(
        721,
        "delete",
        ("needs-scrub",),
        "secret",
        "Leaked credential in a comment; human must delete it (dev never deletes).",
    ),
    TriageEntry(
        739,
        "delete",
        ("needs-scrub",),
        "secret",
        "Leaked credential in a comment; human must delete it (dev never deletes).",
    ),
    TriageEntry(
        747,
        "migrate",
        ("competitive-research",),
        "competitive",
        "Competitor/landscape comparison; keep off the public surface.",
    ),
    TriageEntry(
        755,
        "keep",
        ("research",),
        "research",
        "Technical research/spike; safe to publish, labelled for traceability.",
    ),
    TriageEntry(
        774,
        "scrub",
        ("needs-scrub",),
        "secret",
        "Internal endpoint/private IP in body or comment; human scrubs before the public flip.",
    ),
    TriageEntry(
        811,
        "scrub",
        ("needs-scrub",),
        "secret",
        "Internal endpoint/private IP in body or comment; human scrubs before the public flip.",
    ),
    TriageEntry(
        813,
        "keep",
        ("ideation",),
        "ideation",
        "Feature thinking not yet implemented; roadmap ideation.",
    ),
    TriageEntry(
        821,
        "keep",
        ("ideation",),
        "ideation",
        "Feature thinking not yet implemented; roadmap ideation.",
    ),
    TriageEntry(
        823,
        "keep",
        ("ideation", "needs-scrub"),
        "ideation",
        "Feature thinking not yet implemented; roadmap ideation. Also leaks an internal endpoint.",
    ),
    TriageEntry(
        824,
        "keep",
        ("ideation",),
        "ideation",
        "Feature thinking not yet implemented; roadmap ideation.",
    ),
    TriageEntry(
        827,
        "keep",
        ("ideation",),
        "ideation",
        "Feature thinking not yet implemented; roadmap ideation.",
    ),
    TriageEntry(
        828,
        "scrub",
        ("needs-scrub",),
        "secret",
        "Internal endpoint/private IP in body or comment; human scrubs before the public flip.",
    ),
)


def entries_by_disposition(disposition: Disposition) -> tuple[TriageEntry, ...]:
    """Return the manifest rows carrying ``disposition`` (reviewer helper)."""
    return tuple(e for e in MANIFEST if e.disposition == disposition)

