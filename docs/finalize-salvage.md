# Finalize reports and salvage

How an agent closes a run with the `finalize` tool, and what the engine does
when the posted report is broken.

## Usage — what the agent must send

The closing `finalize` call's **`report` argument is the canonical channel** for
the deliverable. Turn text is not: the engine harvests the `report` argument
first, and the agent-authored turn text only ever feeds the salvage fallback
described below.

A report is a title line followed by named sections delimited by section
markers:

```
# …headline…

<!-- nebuko:section changes -->
…changes…
<!-- /nebuko:section -->

<!-- nebuko:section verification -->
…verification…
<!-- /nebuko:section -->
```

The marker syntax is exact: an HTML-comment open tag `<!-- nebuko:section NAME -->`
followed, after the section body, by the matching close tag
`<!-- /nebuko:section -->`. The section names and which fields are required
come from the step's declared output contract (see the step's `output`
declaration); a required field whose section is missing or unclosed fails
validation and the call is retried.

## Behaviour — how the engine salvages a broken finalize

When the closing turn's report fails section validation, the engine gives the
agent a retryable error, twice. After the second failure it salvages: it
extracts whatever sections *did* survive from candidate report texts, in
precedence order:

1. the anchor `finalize` call's `report` argument (including from a call the
   SDK rejected, e.g. for a missing `turn_outcome`);
2. the anchor event's `thought` content (a model that wrote the report as turn
   text);
3. the nearest preceding agent-authored message, bounded by the closing turn's
   window (never the engine's own prompt, which is user-sourced, and never an
   earlier `finalize` attempt's acknowledgements).

The first candidate that yields at least one intact section wins; later
candidates are not consulted. Salvage never raises — a candidate with no
intact markers contributes nothing, it does not fail the run. If no candidate
yields a section, the run degrades to the work-loop's captured deliverable
(the `finish` note) instead of posting a broken comment.

## Troubleshooting

**The posted message is title-only (an empty body).** That means no candidate
report text contained a single intact section-marker pair — every open tag was
missing, mangled, or unclosed, so every section was dropped. Check that the
`report` argument (or the turn text) contains the literal
`<!-- nebuko:section NAME -->` … `<!-- /nebuko:section -->` pairs.

**The next run started with an empty prompt.** This is a downstream consequence
of a title-only post: the next step's prompt is built from the previous step's
posted sections, so an empty salvage leaves nothing to inject. Fix the
report-marker syntax in the failing step; hardening the next run against an
empty prompt is tracked separately.
