# Bug-fix protocol

When the user reports a bug or a broken scenario, follow this workflow. A bug
is not fixed until a permanent automated test would fail if the same behavior
were reintroduced.

1. **Reproduce with a failing test.** Translate the reported behavior into an
   automated test at the most realistic appropriate layer:
   - unit test — isolated pure logic;
   - YAML contract test (`tests/behavioral/test_form_yaml_contracts.py`) —
     configuration resolution;
   - behavioral scenario (`tests/behavioral/scenarios/`) — user interaction
     flows (see `docs/form-scenario-testing.md`);
   - shared-component contract (`tests/behavioral/contracts/`) — reusable
     infrastructure consumed by several commands;
   - live test guild — only when offline simulation cannot represent it
     (see limitations in `docs/testing-strategy.md`).

2. **Confirm the failure before fixing.** Run the test and verify it fails
   because of the reported bug, not an unrelated setup problem. Include the
   observed failure output in the implementation summary.

3. **Identify the regression surface.** Determine whether the bug affects only
   the current command, multiple YAML forms, a generic component, shared
   state, localization, persistence, or several interaction paths — and list
   the consumers of the affected shared code.

4. **Fix generically.** When the failure originates in shared behavior, fix
   the shared implementation or the configuration contract. Never
   `if command_key == "...":` around a shared defect.

5. **Run the related suites.** The new test plus the contract suites of the
   touched shared components (`pytest -m "shared_contract"`, the impact map in
   `docs/testing-strategy.md`) — not only the newly added test.

6. **Preserve the test.** Regression tests live permanently in the suite
   (`tests/behavioral/regressions/` for flow-level ones), named after the
   lasting contract, with a docstring recording what broke, the shared
   behavior affected, the consumer that exposed it, and what must remain
   guaranteed. Never delete one because it passes.

Regression tests must assert user-visible behavior or state — a mock being
called, an exception not raised, or an object existing is not protection.

## Required report section

Every final response for a bug fix or a change to shared infrastructure must
include (never omit):

```markdown
## Regression protection
- New regression tests:
- Shared components affected:
- Existing consumers exercised:
- Related suites executed:
- Previously reported scenarios now protected:
```
