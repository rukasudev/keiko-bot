# Bug-fix protocol

When the user reports a bug or a broken scenario, follow this workflow. A bug
is not fixed until a permanent automated test would fail if the same behavior
were reintroduced.

1. **Reproduce with a failing test.** Translate the reported behavior into an
   automated test at the most realistic appropriate layer, as a new function in the
   file that owns the command or the shared component: a flow-level bug goes in
   its group file, `tests/behavioral/regressions/test_<command>.py` or
   `test_<component>.py` (`test_card.py`, `test_panel.py`, `test_language.py`...),
   and any other in the layer file of the table in `docs/testing-strategy.md`. A
   new file only for a new command or component (`.claude/rules/testing.md`):
   - unit test — isolated pure logic;
   - command test (`tests/test_<command>.py`, which `make command` scaffolds)
     — one command's own rule in its `service.py`;
   - definition test (`tests/forms/form/test_form_yaml.py`) — a form that must or
     must not compile, a rule the compiler must check;
   - engine test (`tests/forms/form/test_form.py`, `test_manager.py`) — one
     event on one session, asserted on the decision's session and effects;
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
   `if command_key == "...":` around a shared defect: what one command needs
   from a shared layer is a field or hook of its `Command`
   (`app/core/commands.py`), and the command's own rule stays in its folder.

5. **Run the related suites.** The new test plus the contract suites of the
   touched shared components (`pytest -m "shared_contract"`, the impact map in
   `docs/testing-strategy.md`) — not only the newly added test.

6. **Preserve the test.** It stays in that group file permanently, named after
   the lasting contract, with a "Broke as" docstring recording what broke, the
   shared behavior affected, the consumer that exposed it, and what must remain
   guaranteed. It drives the harness's public actions and asserts user-visible
   behaviour or stored state, so it survives refactors; if the same guarantee
   already has a test at another layer, extend that test instead of adding a
   second one. Record it in the ledger (`python -m scripts.keiko testguard ledger
   --sync` adds its entry to `scripts/keiko/testguard/ledger.yml`, and
   `python -m scripts.keiko testguard ledger` must pass); when it later moves or
   is renamed, its entry moves with it. Never delete one because it passes.

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
