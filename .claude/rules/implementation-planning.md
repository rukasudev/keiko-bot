# Implementation planning contract

Every implementation plan for this repository MUST follow this structure. A plan that skips
a section, uses vague reuse claims, or introduces command-specific logic without the
justification below is non-compliant and must be redone, not patched.

Foundation: **Python implements reusable capabilities. YAML combines those capabilities to
create commands, forms, and interaction flows.** Investigate the existing implementation
before proposing anything — search for similar commands, YAML forms, actions, component
types, views, validators, transforms, formatters, state mechanisms, service methods,
localization keys, reusable buttons/messages, and tests covering similar behavior. Base the
plan on what exists in the repository, not on assumptions. `docs/form-configuration.md`
maps the engine and its extension points.

## Decision order

Evaluate in this order and pick the first that fits:

1. **YAML-only change** — compose existing actions, component types, validators, transforms,
   formatters, state references, language keys. No Python just to make one command behave
   differently.
2. **Reuse an existing Python capability** — an existing generic service, component,
   handler, helper, or view already supports the behavior, possibly with a small extension
   to the configuration it accepts.
3. **New reusable primitive** — a generic validator, transform, component, form action,
   state resolver, or service operation that other commands could also use. No assumptions
   tied to a single command unless the domain is inherently specific.
4. **Architecture exception** — command-specific logic, only when the behavior genuinely
   cannot be represented generically, and never silently (see section 5 requirements).

## Required opening block

Every plan must begin with exactly this summary, reflecting the detailed sections (no
unsupported claims, and **no implementation-time estimates anywhere in the plan**):

```markdown
## Implementation decision

- Primary approach: YAML-only | reuse existing capability | generic extension | architecture exception
- Existing primitives reused: ...
- New reusable capabilities: ...
- Command-specific logic: none | describe and justify
- User-facing changes: yes | no
- Keiko writing-style skill reviewed: yes | not applicable
- Estimated architecture impact: low | medium | high
```

## Required sections

### 1. Relevant existing architecture
Describe the current execution path for the affected feature using the actual classes,
functions, files, and modules found in the repository (e.g. cog → YAML form → form loader →
action handler → component → response processing → service/persistence). Traced, not assumed.

### 2. Existing capabilities to reuse
For every reused item: file path; class/function/config key/component name; current
responsibility; how it supports the feature. Vague statements like "reuse existing
components" are non-compliant — name what was actually found.

### 3. YAML-first analysis
State which parts are implemented purely through configuration: existing actions, component
types, validators, transforms, formatters, state/response references, and the localization
files that change. Clearly separate configuration changes from Python changes.

### 4. Required Python changes
For each proposed Python change: why YAML alone is insufficient; why no existing
implementation already handles it; whether it extends an existing abstraction or creates a
new one; how the capability stays generic; which other features could reuse it. No Python
change without this justification.

### 5. Architecture impact
Classify as one of: `Within the current architecture` | `Small generic extension` |
`New reusable primitive` | `Architecture exception` — and explain. For any exception,
explicitly state: which part falls outside the architecture; why existing YAML configuration
cannot represent it; which alternatives were investigated; why a generic extension is not
appropriate; how the exception is isolated; whether it creates technical debt; whether a
future generic solution is recommended. Exceptions are never introduced silently.

### 6. Writing-style and Discord UI impact
If the feature touches no user-facing text or UI, write the literal line:
`Keiko writing-style skill impact: none.` Otherwise: confirm
`.claude/skills/keiko-writing-style/SKILL.md` was read (read the file — do not rely on a
summary); identify which skill rules apply; list the localization files changing (always
both `en-us` and `pt-br`); name existing text/button/form/state-message keys being reused;
describe any genuinely new copy or design pattern. Never omit this section.

### 7. Files to change
For each file: why it changes; whether the change is configuration, reusable code,
documentation, or tests; how it fits the existing structure. No speculative files.

### 8. Implementation sequence
Steps in dependency order. Typical shape: update/add generic capability → expose it through
the existing configuration mechanism → configure the feature in YAML → add localized text →
add/update tests → validate existing flows. Adapt to the actual feature.

### 9. Tests and validation
Which existing tests are relevant; which new tests are needed; how the YAML configuration is
validated; how backward compatibility is checked; what mocking (Discord, database, external
services) is required — see `tests/conftest.py` for the existing fixtures. Include
regression tests when modifying shared primitives. Reference exact commands
(`make test` = `python -m pytest tests/ app/ -x -q`). Never claim tests passed without
executing them.

### 10. Rejected alternatives
Briefly list considered-and-rejected approaches and why each is inferior — at minimum the
nearest simpler option and the nearest more generic option (e.g. command-specific handler,
duplicated component, hardcoded text, parallel service, redundant YAML property, oversized
refactor).

### 11. Regression and blast-radius analysis
Required verbatim whenever the plan touches shared code (form engine, manager,
views, components, validators, transforms, formatters, state helpers, YAML
loader, localization helpers, services or copy used by multiple commands):

```markdown
## Regression and blast-radius analysis
- Shared code affected:
- Existing consumers:
- Existing tests:
- New regression tests:
- Related suites to run:
```

Identify every consumer of the shared capability BEFORE implementing; a feature
that modifies shared infrastructure is not complete because its own happy path
works — representative existing consumers must be exercised (see the impact map
in `docs/testing-strategy.md` and the consumer contracts under
`tests/behavioral/contracts/`). When the plan follows a reported bug, the fix
workflow in `.claude/rules/bug-fix-protocol.md` applies: failing test first,
generic fix, related suites run, test kept permanently.

## Anti-patterns (never propose these when a generic path exists)

`if command_name == "...":` branches; copying and renaming an existing handler; duplicating
an existing service; a second state-management mechanism; validation inside a command;
hardcoded component behavior for one form; user-facing strings in Python; near-duplicates of
existing generic components; new YAML properties equivalent to existing ones; new
abstractions with no real second use case. When similar logic exists, reuse or improve it.
