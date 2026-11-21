# Test rules

A test pins one guarantee a user or an operator would notice. It is written so that a
refactor that keeps the guarantee never edits it. The layers are in
`docs/testing-strategy.md`; how a scenario drives Keiko is `docs/form-scenario-testing.md`.

## Where a new test goes

1. Search before writing: grep the suite for the guarantee (the `Guaranteed:` line of the
   docstrings, the function under test, the command key). If a test already owns it, add a
   case or a `pytest.param` there. Do not open a file.
2. Pick one layer, the cheapest one that fails when the guarantee breaks:
   - a pure function: a unit test in the file named after its module
     (`tests/core/test_<module>.py` for `app/core`, `tests/test_<module>.py` otherwise);
     never a `*_test.py` beside the code, which cannot run without `tests/conftest.py`;
   - a rule of the form platform (compile, `decide`, a section kind): `tests/forms/`;
   - what an admin walks through one command:
     `tests/behavioral/scenarios/test_<command>_flow.py`, and what every card, panel or
     `/setup` page does: `test_card_flow.py`, `test_panel_flow.py`, `test_setup_flow.py`;
   - a component several commands share: its file in `tests/behavioral/contracts/`;
   - a fixed bug: a new function in the group file of its command or component,
     `tests/behavioral/regressions/test_<command>.py` (`test_birthdays.py`,
     `test_youtube.py`...) or `test_<component>.py` (`test_card.py`, `test_panel.py`,
     `test_language.py`, `test_logging.py`, `test_runtime.py`, `test_cache_and_data.py`,
     `test_ui.py`).
3. One guarantee, one layer. Do not add the same rule as an engine test, a scenario and a
   regression in the same change. When a second layer is truly needed, it asserts a
   different thing (the engine decides, the scenario shows), and its docstring says what.
4. A new test file only for a new command, a new shared component or a new migration.
   A bug, a variant or a new field is a new function in the existing file.
5. A guarantee every form must keep (lifecycle, cancel, back, expiry, limits) is one test
   parametrized over every form, never one test per form.

## How a test drives Keiko

6. Use only the harness's public actions and reads, the tables of
   `docs/form-scenario-testing.md`: `start`, `start_command`, `open_from`, `run`, `post`,
   `click`, `select_option`, `submit_modal`, `submit_file_upload`, `confirm`, `cancel`,
   `go_back`, `dismiss`, `interaction()`, `click_after_restart`, `click_custom_id`,
   `expire`, `sweep`, `expiry_loop_pass`, `later`, `discord_refuses`, `restart`, `finish`,
   the `expect_*` checks, `get_persisted`, `card_state`, `answers`, `outputs`, `sessions`,
   `held_sessions`, `live_messages`, `pending_modal_inputs`, `fill_tokens`, and the flows
   of `tests/behavioral/flows/` and the helpers of `tests/helpers/`.
7. Never touch `scenario._mint`, `scenario.store`, `RUNTIME`, `_modal_inputs`,
   `locators.dispatch_click`, `current_message.deleted = ...` or discord.py internals
   (`__discord_ui_compiled_template__`, `DynamicItem.__subclasses__`). If a test needs one,
   add a public action to the harness first, in the same change, with its self-test in
   `tests/behavioral/test_harness.py` and its row in `docs/form-scenario-testing.md`, and
   use that. One intention is one action: extend the one that exists rather than adding a
   second with another name.
8. Name an intention once. When one user intention takes more than one click (delete the
   feature, open a member's card, type a day), it is a harness or flow action named after
   the intention, so a step added or removed in the UI edits one function.
9. Click a card section by its key (`customize:<section key>`), never by its position
   (`customize:4`).
10. Reach a command or a webhook through the entry point Discord or the outside service
    uses (the scenario, the cog, the webhook handler). Call a service function only from
    its own unit test, and then with keyword arguments.
11. Never import a name that starts with `_`, from `app/` or from another test module. A
    helper two test files need moves to `tests/helpers/` (or the command's flow module)
    with a public name.
12. Import app code from the package that exports it (`app.forms.features`, not a file
    inside it), and never `patch()` a module path string: patch the object the test was
    handed (a fixture, `monkeypatch.setattr(obj, name, ...)`), so a file move inside a
    package edits the package, not the tests.

## What a test asserts

13. Assert the outcome the guarantee names: what the user sees, what Discord receives or
    what is stored. Do not also assert incidental parts of the same screen (a list's
    style, a message's full text, the position of a button) unless the guarantee is about
    them.
14. Read copy by key: compare with `ml(key, locale)`, `copy_text(key, locale, **values)`
    (`tests/behavioral/harness/copy_keys.py`), a button's action or a locator alias. No
    literal sentence, label or emoji, and never split a copy string on its punctuation.
    The exception is a test whose guarantee is the wording itself (language, gender, the
    ticket in English).
15. Seed stored documents through the builders, one per persisted shape, with keyword
    overrides: `tests/generators/` (a saved feature, a member's birthday, an event) and
    `seed_document` with the documents of each flow module for a form. Never retype field
    names or envelopes (`{"value": ..., "_raw_value": ...}`) by hand in a test.
16. Never enumerate an open list in a test (every migration id, every shared copy file,
    every feature). Assert the property: unique, ordered, each one has X.
17. A helper or fixture already defined elsewhere is reused, not copied (`_texts`,
    `_panel`, `_last_screen`, the Mongo `server` fixture).

## Golden books

18. One golden book per command, `tests/behavioral/golden/<form>.yml`: each screen once,
    a variant as its base screen plus changes, every text by its copy key and the values
    the code filled, the panel and the review also in words in both locales. A path that
    only re-walks a shared component is a parametrized contract instead.
19. A change that moves screens re-records once, at the end of the change
    (`pytest tests/behavioral/golden --update-golden`, never narrowed with `-k`), and the
    `git diff tests/behavioral/golden/` is read to confirm only the intended text or
    layout moved. Never fix books one by one during the work.
20. `docs/ux-changes.md` gets a row only when what Keiko shows or does changes for an
    admin (a cosmetic or behavioural change). A book that moves because the tests changed
    (the book format, a harness action, a path re-ordered) or for a change a row already
    covers takes no new row.

## Past bugs and the guard

21. Every "Broke as" docstring is an entry of `scripts/keiko/testguard/ledger.yml`, which
    names the test that pins it now. A test that moves, merges or is renamed takes its
    entry along (its `test:` line points at the new home); an entry is never deleted. A
    new regression is added with `python -m scripts.keiko testguard ledger --sync`, and
    `python -m scripts.keiko testguard ledger` must pass.
22. A test the guard shows lost (a covered line, a mutant kill, a ledger entry) gets its
    guarantee back in the same change. The baseline is never re-saved to hide a loss; a
    kill the baseline recorded falsely (the test failed with no mutant) goes in
    `scripts/keiko/testguard/accepted.yml` with its reason and the command that shows it.

## Running

23. During a round, `make test-fast` (forms, scenarios, goldens, the harness and the
    contracts, no `slow` test). Before a PR, `make test` and `make test-guard`, which
    must say SAFE.

## Size

24. A change adds at most one test file per new command, shared component or migration.
    The PR description gives test lines added per app line changed; above 3 says why.
25. A test that fails after a refactor that kept the behaviour is a defect of the test.
    Rewrite it to rules 6 to 17 in the same change instead of patching the line.
26. A docstring says the guarantee in the words of what the user sees or what is stored.
    Internal names (functions, modules, YAML keys) appear once, in a regression's
    "Shared behaviour" line, never spread across every test of the file.
