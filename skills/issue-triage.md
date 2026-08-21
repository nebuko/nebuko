# Issue Triage Skill

Procedural playbook for iterative issue refinement with humans: question format, round structure, state markers, and @@ctx delegation.

## The Multi-Round Process
1. **Human creates a draft issue** with some basic content
2. **You analyze the draft**, understand what's needed, check for questions to answer, and ask clarifying questions to help refine
3. **If needed**, you call @@ctx to collect context (search the repo, internet, related issues/PRs) — then use that context to rephrase and enhance the issue
4. **Once a round is done**, you post a response and **end the workflow**, waiting for the human to ping you again (`@@issue`) for the next round
5. **When the human is happy**, they ask you to produce the final polished issue

## 1. Round 1

### Step 1: Read the Full Context
- Read the issue body (the "Issue Original Description" section in your context)
- Read the full comment history to understand the conversation so far
- **Check the issue body for state marker**: Look for an HTML comment like `<!-- issue-agent-state: ... -->` — if present, this tells you the current round number and status. If absent, this is Round 1.

### Step 2: Analyze the Draft
- Understand what the human is trying to communicate
- Identify gaps, ambiguities, or missing information
- Note any explicit questions the human asked
- **CRITICAL**: Preserve the human's intent. Your job is to **enhance**, not **rewrite** their draft. Keep their voice and core message.

### Step 3: Rephrase and Ask Clarifying Questions
- **First, rephrase your understanding** of the human's draft in 2-3 sentences to confirm you've understood correctly
- Then post clear, focused questions that will help improve the issue
- Limit to 3-5 questions per round (don't overwhelm)
- **Each question MUST include 1-5 proposed answer options** — format as a lettered list (a, b, c, etc.)
  - Use open-ended "Something else (please specify)" as the last option when appropriate
- **ALL questions MUST be grouped in a single copy-pasteable block** so the user can reply inline
- Prioritize questions that would have the biggest impact on issue quality

Expected question block format:

```
### Questions

**Q1: [Question text]**
- (a) [Option A]
- (b) [Option B]
- (c) [Option C]
- (d) Something else (please specify)

**Q2: [Question text]**
- (a) [Option A]
- (b) [Option B]
```

### Step 4: End the Round
- Post your analysis and questions as a comment
- End your message with: `@@issue round complete. Please respond to my questions or post @@issue again for the next round.`
- **DO NOT wait** — the workflow ends. The human must ping you again to continue.

## 2. Subsequent Rounds (Round N > 1)

### Step 1: Check State Marker
- Look for `<!-- issue-agent-state: round=N,status=... -->` in the issue body
- If found, you know the current round number and what happened last time
- If NOT found, count the number of your own bot comments (`<!-- bot-agent-issue -->`) in the comment history to determine the round number

### Step 2: Read Human's Response
- Review the human's answers to your previous questions
- Assess whether you have enough information to proceed
- Identify any new gaps or follow-up questions

### Step 3: Decide Next Action
Choose ONE of the following paths:

**Path A: Ask More Questions** (if gaps remain)
- Post follow-up questions
- End the round

**Path B: Call @@ctx for Context** (if you need more information)
- When calling @@ctx, use the AGENT_RUN_CONFIG sentinel format:
  ```
<!-- AGENT_RUN_CONFIG {"task": "ctx"} -->
  ```
- Include your specific context request in the message body (e.g., "Please search for: related authentication issues and PRs").
- After ctx returns, it will route back to you via `{"task": "issue"}` based on the bot marker detection.
- Proceed to Path C

**Path C: Rephrase and Enhance** (if you have enough information)
- **Step 1: Rephrase** — Restate your understanding of the human's request in 2-3 sentences
  - Explicitly ask: "Is my understanding correct?"
  - Wait for confirmation before proceeding
- **Step 2: Enhance** — Once confirmed, enhance the issue body with better structure, clarity, and detail
- Use ctx's context if available to add technical depth
- Post the enhanced version as a comment for human review
- Ask for human approval

**Path D: Produce Final Issue** (if human requests it)
- When the human explicitly says they are happy with the discussion, produce the final polished issue
- Post the final issue as a well-structured comment on the issue thread
- The human can copy the final issue to the issue body if desired
- End with: `@@issue final issue created. The polished issue has been posted as a comment.`

## 3. Calling @@ctx

### How to Call @@ctx
Use the `AGENT_RUN_CONFIG` sentinel format:

```
<!-- AGENT_RUN_CONFIG {"task": "ctx"} -->
```

Include your specific search request in the message body (e.g., "Please search for: related authentication issues and PRs").

### What to Ask @@ctx For
- Related issues or PRs in the repository
- Relevant code patterns or architecture documentation
- Best practices for similar issues
- Internet research on relevant technologies or standards

### What NOT to Ask @@ctx For
- Do NOT call @@ctx for simple clarifying questions that you can answer yourself
- Do NOT call @@ctx if the human's draft is already well-structured
- Do NOT call @@ctx more than once per round

### Handling @@ctx Results
- If @@ctx returns useful context: integrate it into your response
- If @@ctx returns empty/insufficient results: tell the human clearly — "I searched for related context but found nothing relevant. Here's what I know so far..."
- If @@ctx fails or times out: tell the human — "I was unable to collect additional context. Let's proceed with what we have."

## 4. State Management (Comment-Based State Markers)

### Why State Markers?
GitHub Actions are stateless. The full comment history can grow very large, consuming token limits. To mitigate this, include state markers in your comments so the agent can track progress across rounds.

### State Marker Format
Include this HTML comment at the **top** of your comment responses:

```html
<!-- issue-agent-state: round=3,status=waiting_for_human -->
```

### State Values
- `status=waiting_for_human` — You've posted questions/results and are waiting for the human
- `status=ctx_collecting` — You've called @@ctx and are waiting for results
- `status=enhancing` — You're actively refining the issue content
- `status=final` — The final issue has been produced

### When to Include State
- Always include the state marker at the top of your comment
- The state marker helps the agent (and humans) quickly understand the current round and status
- When the human pings you again, read the last state marker to determine where you left off

## 5. Output Formats

### Per-Round Response Structure
```
## Round N: [Brief title describing this round's focus]

### Analysis
[Rephrase the human's draft in 2-3 sentences to confirm understanding]

### Questions
**Q1: [Question text]**
- (a) [Option A]
- (b) [Option B]

### Next Steps
[What the human should do next]

<!-- issue-agent-state: round=N,status=waiting_for_human -->
```

### Final Issue Format
When producing the final issue, structure it as:
```
## Summary
[One-paragraph overview of the issue]

## Context / Background
[Relevant background information]

## Problem Statement
[Clear, concise problem description]

## Proposed Solution (if applicable)
[Your suggested approach]

## Acceptance Criteria
- [ ] Criterion 1
- [ ] Criterion 2
- [ ] Criterion 3

## Additional Notes
[Any supplementary information]
```
