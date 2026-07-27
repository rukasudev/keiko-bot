---
name: keiko-architecture-review
description: Use when the user asks for an architecture or conventions review of Keiko Bot code, e.g. "review my branch", "review this PR/diff", "check this file against Keiko conventions", "audit this directory", "does this follow the architecture", or "full architecture review". Supports four scopes chosen by the argument given - no argument or "branch" = current branch vs its base branch; a file path = that file; a directory path = that directory; "project"/"everything" = the entire codebase. Checks compliance with the YAML-first core principle and decision order (CLAUDE.md), reuse of form-engine capabilities and known exceptions (docs/form-configuration.md), planning-contract anti-patterns (.claude/rules/implementation-planning.md), and writing-style/localization/Discord UI rules (keiko-writing-style skill). Review-only - produces a structured findings report with Blocking/Important/Suggestion severities and never edits code.
---

# Keiko Architecture Review

You are performing a read-only architecture and conventions review of Keiko Bot code.
You MUST NOT modify any file: no edits, refactors, commits, YAML changes, copy rewrites,
or test fixes. You produce a report and an implementation direction. Only apply findings
if the user explicitly asks after seeing the report.

The standard you review against: **Python implements reusable capabilities. YAML combines
those capabilities to create commands, forms, and interaction flows.** Judge every finding
against the decision order in `CLAUDE.md`: YAML-only → reuse existing capability → new
generic reusable primitive → declared architecture exception.

## Scope resolution

Identify the scope first and state it explicitly at the top of the report (`Base branch`
only in branch mode):

```markdown
## Review scope
- Mode: current branch | file | directory | entire project
- Target: ...
- Base branch: ...
- Related context inspected: ...
```

Rules for every mode:
- If the user asked for a file or directory review without a path, ask for the path.
- If the target doesn't exist, suggest close matches (`git ls-files | grep -i <name>`) and
  ask; never silently substitute another target.
- Never silently widen the scope (a file review does not become a project audit). Reading
  surrounding code, YAML, callers, and tests for context is expected, but findings must
  stay focused on the selected target. Do not report unrelated legacy issues in branch,
  file, or directory mode unless the reviewed code depends on them.

**Current branch** — determine the base and diff against the merge base, including
committed, staged, unstaged, and untracked work:

```bash
BASE=$(gh pr view --json baseRefName -q .baseRefName 2>/dev/null) \
  || BASE=$(git symbolic-ref --short refs/remotes/origin/HEAD 2>/dev/null | sed 's|^origin/||') \
  || BASE=main
MERGE_BASE=$(git merge-base "origin/$BASE" HEAD)
git diff --name-status "$MERGE_BASE"   # committed + staged + unstaged
git status --porcelain                  # "??" lines = untracked files, read them directly
git diff "$MERGE_BASE" -- <paths>       # full content as needed
```

If the current branch IS the base branch or the diff is empty, say so and ask which scope
the user wants. Focus findings on code introduced or affected by the branch.

**File / directory** — analyze the given path; inspect its imports, callers, related form
YAML, localization keys, and tests as context.

**Entire project** — prioritize high-impact structural findings; group repeated instances
into patterns instead of listing every occurrence; distinguish the current architecture
from historical or isolated code; surface the biggest reuse and standardization
opportunities rather than an unstructured list of minor style issues. You MAY launch
parallel Explore agents, one per area (cogs+services, views+components, languages+data,
tests); give each the four required-reading paths and the finding format below, then
dedupe, classify severity, and assemble the single report yourself.

## Required reading

Read these before producing findings (they are the review criteria; do not restate their
contents in the report beyond what a finding needs):

- `CLAUDE.md` — the core principle and the mandatory decision order.
- `docs/form-configuration.md` — the form-engine map. Read §4 (extension points/registries)
  before claiming a capability does or doesn't exist; read §7 before flagging something as
  a new architecture exception (it may already be a known, documented one); §8 names the
  enforcement test suite.
- `.claude/rules/implementation-planning.md` — decision order details and the anti-patterns
  list findings should cite.
- `.claude/skills/keiko-writing-style/SKILL.md` — MANDATORY read whenever the scope touches
  user-facing text, `app/languages/`, embeds, forms, modals, buttons, notifications,
  errors, or any visual presentation. It is the source of truth for personality, voice,
  Discord UI conventions, form structure, and localization. Read the current file each
  time; never rely on a summary, and never copy its rules into this skill or the report.

## Review process

Follow all six steps before writing findings.

1. **Understand the selected change** — what behavior it provides, how it is triggered,
   which YAML is involved, which components/services it uses, which state and persistence
   mechanisms it depends on, whether it introduces user-facing behavior.
2. **Search for existing equivalents** — similar commands, form steps, components, actions,
   validators, transforms, formatters, service methods, localization keys, buttons and
   state messages, and tests for similar behavior. Never report duplication without
   identifying the specific existing implementation that could be reused.
3. **Evaluate YAML-first compatibility** — is the behavior already fully configurable in
   YAML; composable from existing YAML capabilities; a small extension to an existing
   generic primitive; a new reusable primitive; or genuinely outside the architecture?
4. **Evaluate code reuse** — for every new method, class, handler, service, or helper:
   does an equivalent exist; does it duplicate logic; is it unnecessarily tied to one
   command; does it belong in an existing abstraction; does it have realistic reuse
   potential; does the abstraction make the implementation simpler or only more indirect?
   A new abstraction is not wrong merely for being new — judge it on these criteria.
5. **Evaluate architecture exceptions** — when code falls outside the architecture,
   explain what is outside the structure, why the existing structure does not support it,
   whether a generic extension is possible, whether the exception appears justified, how
   it should be isolated, and what technical debt it introduces. Never classify something
   as an exception without naming the architectural path it bypasses.
6. **Evaluate writing and UI conventions** (when relevant) — no user-facing strings
   hardcoded in Python; both `en-us` and `pt-br` covered; generic text keys reused;
   buttons/components and command-description voice per the writing-style skill; errors,
   success messages, and transitions match Keiko's personality; new forms follow the
   established interaction structure; no competing visual or writing pattern introduced.

## Finding categories

Group findings under these categories when applicable; never create empty sections:
`YAML-driven architecture`, `Existing implementation reuse`, `Generic design`,
`Command-specific logic`, `Architecture exception`, `Configuration consistency`,
`Keiko writing style`, `Discord UI conventions`, `Localization`,
`Testing and regression risk`, `Documentation`.

## Severity levels

- **Blocking** — should not merge as is: breaks existing behavior; unsafe duplication in a
  critical path; bypasses an important shared mechanism; significant architectural
  conflict; violates required localization or user-facing constraints; high regression risk.
- **Important** — works but should change: duplicates reusable logic; unnecessary
  command-specific behavior; misses an existing YAML-driven solution; new primitive too
  specific; avoidable maintenance cost; conflicts with established conventions.
- **Suggestion** — non-blocking improvement: clearer, more reusable, more consistent,
  easier to maintain, better documented or tested.

Do not inflate minor preferences into blocking findings.

## Finding format

Every finding needs evidence and a concrete recommendation, in this structure:

```markdown
### [Important] Command-specific validation duplicates an existing validator
**Location:** `path/to/file.py:120`
**What was found**
Explain the concrete implementation and why it is relevant.
**Existing capability**
Identify the existing file, function, component, YAML property, validator,
transform, formatter, or service that could be reused.
**Why it matters**
Explain the architectural, maintenance, consistency, or regression impact.
**Recommended change**
Describe how to implement the behavior using the existing structure or a
generic extension.
**Classification**
- Current implementation: command-specific logic
- Recommended approach: reuse existing capability
```

Generic recommendations like "Consider making this more reusable" are non-compliant: name
the existing capability, or describe the exact generic primitive that should be created.

## Running tests

Running pytest is non-mutating and allowed; never install dependencies, edit code to make
tests pass, or start services (`make run`, `make docker-up`). Record the exact commands and
their real results in the validation checklist — never claim tests passed without
executing them.

- Every scope: run the architectural contract suite once,
  `.venv/bin/python -m pytest tests/test_reusable_configuration.py -q`. A failure caused
  by the reviewed code is a Blocking finding.
- File/directory: also run the matching tests (`tests/test_<module>.py`, else
  `git grep -l <symbol> tests/`). If none exist, record
  `Relevant tests executed: none found for <target>` and consider a test-coverage finding.
- Branch: run the tests for changed modules; escalate to `make test` when the diff touches
  the form engine or its registries.
- Entire project: `make test` once, after exploration.

## Report format

Produce the final report in exactly this structure, findings ordered Blocking → Important
→ Suggestion. Omit fields that genuinely do not apply, except the architecture
classification. Include positive observations only when supported by evidence (correct
reuse of primitives, no command-specific branching, a genuinely generic new validator,
reused localization keys, writing-style compliance, regression tests for shared logic);
never add praise to balance the report.

```markdown
# Keiko Architecture Review
## Review scope
- Mode:
- Target:
- Base branch:
- Related context inspected:
## Executive summary
Summarize whether the selected scope follows the project's conventions.
## Architecture classification
- Primary approach:
- YAML-first compliance:
- Existing primitives reused:
- New reusable primitives:
- Command-specific logic:
- Architecture exceptions:
- Keiko writing-style impact:
- Overall risk:
## Findings
## Positive observations
## Reuse opportunities
Existing components or abstractions that should be used more broadly.
## Architecture exceptions
Justified and unjustified exceptions, separately.
## Recommended implementation direction
The preferred target structure, without rewriting the entire feature.
## Validation checklist
- Existing tests reviewed:
- Relevant tests executed:
- YAML compatibility checked:
- Localization checked:
- Keiko writing-style skill reviewed:
- Backward compatibility checked:
## Final assessment
Aligned with the current architecture | Aligned with minor improvements recommended |
Requires architectural adjustments | Conflicts with the current architecture
```

## Restrictions

- Do not formalize the YAML system as a complete DSL; do not require enums, Pydantic,
  JSON Schema, a parser, an AST, or a compiler.
- Do not suggest a large refactor when a small reuse change solves the issue.
- Do not recommend YAML-only implementation when the behavior genuinely requires runtime code.
- Do not treat every new method as duplication.
- Do not invent existing capabilities, and do not claim a component can be reused without
  inspecting it.
- Do not report unrelated legacy problems in branch, file, or directory reviews.
- Do not duplicate the Keiko writing-style skill.
- Do not enforce personal style preferences that are not project conventions, and do not
  report formatting already handled by project tooling unless it reveals a larger problem.
- Do not claim tests passed unless they were executed.
- Do not alter the selected scope without making that expansion explicit.
