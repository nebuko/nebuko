# Every week, another tool. Every week, the same question.

It lands on Monday. Someone posts it — "you have to try this." The demo is
impressive. It generates code, writes tests, explains the stack, does the thing
it was built to do. You feel the familiar pull to add it, wire it in, make it
part of how you ship.

Then comes the quiet moment the demo never shows. *Where does this actually go
in my stack?* What does it replace? Where does it sit? How does it combine with
the four other tools that are already here — the ones nobody fully documented,
because the answer lived in a couple of people's heads and, for one of them, in
a job they've since left?

You run the audit by hand. You answer the question the way you always have:
slowly, personally, and from scratch. And next week, another tool. Another
question. The same work, over and over.

What changed is not that the tools got worse. It's that the bottleneck moved.
**Writing code stopped being the hard part. Integrating the tools and managing
the process around them is.** That shift is where the real cost of AI-assisted
development now lives — and it's the problem git-flow-ai exists to solve.

---

## The problem has a name

Generation is solved. A hundred tools will keep proving that. What none of them
touch is the shape of your team's work.

There is a constant stream of new AI tools arriving, and every single one forces
a manual "where does this go?" audit — a judgment repeated by hand for each
arrival, with the answer stored nowhere anyone can inspect it.

There is a review load that has quietly grown out of proportion. LLMs produce
large pull requests in minutes. Merging them means a human diffing line by line,
and so *review time* — not generation time — has become the limiting factor of
the pipeline. The cost is paid on every merge, by the person who already has too
many open PRs.

There is automation that refuses to bend. CI/CD pipelines are deterministic. When
step three needs something step two didn't produce, the pipeline either fails or
moves forward blind. Nothing about it can say "I'm missing a piece — let me ask
the specialist who knows." It just stops.

And there is process that lives only in people. How a good team actually works —
spec, design review, stub review, implementation, verification — is tribal
knowledge carried by meetings and senior engineers. It is not versioned, not
shareable, not reproducible by the next team. When those people leave, the
process leaves with them.

None of these problems is a code problem. They are process problems. And process
problems need a place to live that the tools alone will never give you.

---

## The process became code

Here is the turn, and it is the whole idea in one line: **a workflow is a YAML
file in your repository, exactly like your CI.**

That single fact does more than any feature list. Because your process is now a
file under version control, it inherits everything you already trust about code.

You set a process once, declaratively. The workflow says which agent does what,
in which order, and where a human must sign off before the machine continues.
No dashboard to configure, no service to stand up — just a file you commit.

You update it the way you update anything else: a plain diff. A colleague reads
the change before it takes effect. They see that the spec gate moved before
implementation, or that a new approval was added, and they reason about your
*process* the way they already reason about a pull request.

You roll back a bad process change the same way you roll back a commit. The
week the new workflow made reviews slower is a single `git revert` away. Your
team's way of working finally has a **version history** — reviewable, diffable,
reversible, and honest about when things changed.

You share it through the repo. A workflow another team adopts is a file they
copy, not a migration they plan.

And when a step hits a gap — a missing detail, an assumption it needs checked —
the agent doesn't fail the pipeline. It reaches out with a simple `@@mention` to
the right specialist: *@@dev, implement this stub*, *@@ctx, what's the context
here*. The result comes back into the same issue thread and the step picks up
where it left off. A missing piece becomes a delegation instead of a broken
build, and you watch the whole exchange happen in the thread you're already
reading. No opaque orchestration layer. The repo is the system.

Set it. Update it. Roll it back. Share it. Measure it. That last one is the part
that turns a clever trick into a discipline — and it's where the story gets
proof.

---

## The day it runs itself

Picture one concrete day, using the flagship workflow we ship first. We call it
**spec-driven**, and it's built from the way we actually work rather than an
imagined use case.

An issue is filed. An agent reads it and drafts the architecture spec. Then the
machine stops and waits. A human spends five minutes making a *design decision*
— approving the shape of the solution — instead of two hours diffing code that
already exists and is therefore harder to change.

The agent proposes method stubs next: where code will be injected, where it will
be called, what the test cases look like. A second short approval. What a human
reviewed here was the *design*, not the implementation. Two gates, both
short, both at the level where human judgment is worth paying.

Then it runs itself. The implementation fills in against the approved stubs.
Tests verify. A pull request opens — small, focused, and shaped by decisions a
human already made. The reviewer's job collapsed from "read every line of a large
AI PR" to "confirm the design held up," which is measurably less time to merge
AI-generated code.

And because the workflow is a unit in version control, you can finally answer
the question no tribal automation can: *did this process actually improve
quality?* You compare the period before and after it landed, or run variants
side by side, on concrete signals rather than gut feeling.

Initial candidate signals (definitions still in progress):

| Signal | What it tells you |
|--------|-------------------|
| Human interactions per issue | How much a human had to intervene |
| Tokens spent per issue | The cost of running the process |
| Defect / rework rate | The quality of what gets merged |
| Merge time | How fast work actually lands |

If the numbers show the workflow earned its keep, shipping it across the team is
just merging the YAML file — a CI change, nothing more. No new deployment
surface. No platform migration. You promote a proven process the way you've
always promoted a good patch.

---

## From your repo to a shared library

Think about what just happened in that day. You didn't just wire a pipeline. You
built a *process someone else can adopt by forking a file.*

Today, that sharing is a git repo. Point at a workflow in a public repository,
copy it in, adapt it to your stack. The project ships a small set of workflows
drawn from real usage, and the spec-driven flow above is the one we're most
confident in — the proof that a human-in-the-loop process beats raw generation
on what matters: review time, quality, and speed to merge.

As those workflows prove their value, the format is ready to carry more. The
open contract is the YAML itself; a curated, discoverable library — and later a
managed marketplace — is simply a distribution layer sitting on top of it. The
engine and the format stay open. Distribution is optional, and it comes later.

A word on how it fits in and who owns what. There is no new infrastructure:
GitHub Issues and Comments are the transport, agents post where you already
watch, and humans steer by commenting. Setup is a **GitHub App installation** —
per-repo, fine-grained, scoped and revocable — with a PAT fallback for local and
contributor development. And the split between open and managed is deliberate:
an open-source core, self-hosted and runnable with your own LLM key under a
copyleft-style license (AGPL v3 is currently the leading candidate; final
decision pending), with a managed SaaS layer — hosted operation, managed LLM
routing, observability, and the marketplace — arriving later as convenience,
not as a fork.

We are shipping it early and in stages, and building it in public: an alpha with
one true agent-to-agent round-trip on a real issue, a beta where the
spec-driven workflow runs end to end and the format freezes, and a 0.1 that is
stable enough to fork. You will meet the project in motion, not at a reveal.

So the shortest way in is also the honest one. Install the app on a repository
you actually work in. Run the spec-driven workflow on a real issue and watch the
two human gates do their job. Then take a workflow, fork it, and make it yours —
because the thing we're really shipping isn't a tool. It's a place where the way
your team works can be written down, measured, improved, and handed to the next
person without losing a single line of it.