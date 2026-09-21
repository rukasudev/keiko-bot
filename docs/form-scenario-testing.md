# Writing form scenarios

How to test a complete Keiko form flow offline with the behavioral harness
(`tests/behavioral/harness/`). Strategy and layer overview:
`docs/testing-strategy.md`. What the harness drives is the form platform
described in `docs/form-configuration.md`.

## Quick start

```python
import pytest

pytestmark = pytest.mark.behavioral

async def test_block_links_happy_path(scenario_factory):
    scenario = await scenario_factory(locale="pt-br").start("block_links")
    await scenario.confirm()                                # intro -> the card
    await scenario.click("done")                            # accept the defaults
    scenario.expect_step("add_custom")
    await scenario.click("Depois")
    scenario.expect_step("permissions")
    await scenario.confirm()

    scenario.expect_step("confirm")
    await scenario.confirm()                                # review -> commit
    scenario.expect_persisted("guild", "block_links",
                              {"guild_id": str(scenario.guild.id)},
                              {"enabled": True})
```

On failure, the assertion message carries the full transcript:

```text
[01] USER start "block_links" values=pt-br
[02] BOT  send M1 ephemeral | embed ":no_entry_sign: Bloquear links" ...
       components: [button "Confirmar" success] [button "Cancelar" danger]
[03] USER click M1 "continue"
[04] BOT  defer
[05] BOT  followup_edit M1 ephemeral | step=permissions ...
```

## The driver (`FormScenario`)

Every scenario enters through the real seam every cog goes through:
`RUNTIME.open_feature(interaction, key)`
(`app/settings/discord/callbacks.py`). The platform decides what
opens from what is saved in the mock Mongo, exactly as in production.

- `await scenario_factory(locale=...).start(command_key)` — nothing saved:
  the setup form.
- `await ....start_manager(command_key, cog_data)` — seeds `cog_data` as the
  guild's document (deep-copied, `guild_id` forced to the scenario's guild)
  and marks the feature enabled in `moderations`, then opens the manager
  panel.
- `await ....start_command(command_key)` — opens the command with whatever
  the scenario seeded before; the platform routes to the setup form or to
  the manager. The golden transcripts use only this entry point.

User actions (each mints a fresh interaction, like Discord):

| Method | Notes |
|---|---|
| `click(target)` | see locator semantics below |
| `select_option(values, target=None)` | names/ids resolve against the mock guild; `target` optional when one select is on screen |
| `submit_modal({label: value})` | matches the modal's input labels (exact, then contains) |
| `dismiss_modal()` | the user closes the modal without submitting — Discord tells the bot nothing, the message that opened it stays on screen |
| `submit_confirmation(word=None)` | submits the pending lifecycle confirmation (pause/unpause/disable); defaults to the correct action word — pass a wrong `word` to test rejection |
| `submit_file_upload(filename=, content=)` | submits a pending upload modal with a fake attachment (async `read()` runs for real) |
| `pending_modal_fields()` | labels of the pending modal's inputs (useful to build the dict for `submit_modal`) |
| `confirm()` / `cancel()` / `go_back()` | aliases for the generic buttons |
| `expire()` | the clock passes every open session's deadline with nobody clicking (`Runtime.expire_stale`) |
| `finish()` | lets any task the flow scheduled settle; call at the end of card/design flows |

Assertions (all raise with the transcript attached):
`expect_step(key)`, `expect_message(kind=, title_contains=,
description_contains=, content_contains=, ephemeral=, components_v2=,
has_component=)`, `expect_component(label_or_action=, disabled=)`,
`expect_error(text)`, `expect_modal(title_contains=, field_labels=)`,
`expect_configuration_values(*values)` (manager summary),
`expect_persisted(db, collection, filter, subset)` /
`expect_not_persisted(...)` / `get_persisted(...)`.

Introspection: `scenario.outputs` (normalized events), `scenario.transcript`,
`scenario.current_message`, `scenario.db`, and three views of the engine's
state, read from the session store through the ids on screen:

- `scenario.session` — the `FormSession` in front of the admin: the one
  behind the current message, or the child it opened when that child shows
  a modal;
- `scenario.answers` — the raw answers of that session, by key;
- `scenario.card_state` — the state the card on screen is drawn from,
  defaults included (empty when the current step is not a card).

Assert on state through those, never by reaching into the store yourself.

## Locator semantics — what `click("...")` accepts

Resolution order: explicit `custom_id` → localized button label
(case-insensitive) → semantic alias. Every component the platform draws
carries a `k:<session>:<revision>:<action>[:<arg>]` id, and aliases resolve
to the `action[:arg]` part, so a target is locale-independent:

| Target | Resolves to |
|---|---|
| `continue` / `confirm` | the green confirm button (`confirm`) |
| `cancel` | `cancel` |
| `back` | `back`, `picker_back` |
| `done` | the card's Done (`done`) |
| `edit` / `add` / `remove` | the manager panel's Edit, Add, Remove |
| `pause` / `unpause` / `disable` | `lifecycle:<action>` |
| `help` / `preview` / `history` | `aside:<action>` |
| `customize:N` / `edit:N` / `reset:N` | the N-th card section's customize, edit or reset button |
| `option:<label>` | an options-grid button by label |
| `design:<key>` | the design gallery entry `<key>` |
| `section:<step_key>` | the manager panel section that edits `<step_key>` (they all share the "Edit" label) |

Unknown targets raise `LocatorError` listing everything clickable on
screen. For multi-selects, `select_option(..., target=...)` also accepts
the YAML select key (e.g. `allowed_chats`).

## Message lifecycle is modeled like discord.py's ViewStore

What a message shows is the snapshot taken when it was sent or edited
(`FakeMessage.registered_items`), not a view object's current children.
Three real behaviors fall out of that model:

- a click on a component whose message was replaced fails the scenario
  with the real `View interaction referencing unknown view for item ...`
  warning — the silent dead-button bug this catches offline
  (`tests/behavioral/regressions/test_view_lifecycle_regressions.py`);
- on edits, an explicit `view=None` / `embed=None` REMOVES that field
  (`MISSING` semantics), while not passing it keeps what was there;
- a message sent as a Components V2 container refuses `content` and
  `embed` on edit with the real `50035` (`discord.HTTPException`), and
  keeps what it showed — the flag is fixed at send time, so the executor's
  send-then-delete replacement is exercised here exactly as on Discord.

Dispatch calls the clicked component's own `callback`, the way discord.py's
`ViewStore` does; every component the renderer draws routes that callback
to `Runtime.handle` with its `custom_id`, so the harness and production
share one path and the fakes never call an engine method directly.

## Configuration cards (Components V2)

Cards are LayoutViews; the transition to/from them is send + delete, which
the transcript shows as `followup_send` with `CV2` followed by `delete`.
Typical interaction:

```python
await scenario.click("customize:0")        # opens the section picker/modal
await scenario.select_option("general")    # picker select -> back on the card
await scenario.click("customize:1")
await scenario.submit_modal({"Dia": "12"}) # modal-input section
await scenario.click("done")               # validates `required_keys` and advances
```

A failed validation (e.g. `validate_date`) sends an ephemeral error and
keeps the card as the actionable message; assert it with `expect_error(...)`
and retry by clicking the section again. Selections on a card are drafts
until Done, so `scenario.card_state` shows them right after the pick.

## Normalized output

One event per Discord API call (never coalesced), shape:

```python
{"seq": 6, "actor": "bot", "kind": "send|edit|defer|modal|followup_send|"
 "followup_edit|delete|edit_original|delete_original|channel_send",
 "message": "M3", "ephemeral": True, "step": "birthday_config",
 "embed": {"title", "description", "fields", "footer", "color", ...},
 "components": [...], "modal": {...}, "components_v2": True}
```

Unstable data never appears: message ids become `M1, M2, ...` aliases,
session-coded custom_ids are omitted, timestamps are dropped, URLs reduced
to basenames. Two identical runs produce identical `outputs` (enforced by
`test_harness.py`). Assert on the normalized dicts, with the `expect_*`
helpers, or against a golden transcript (below).

## Golden transcripts

`tests/behavioral/golden/` pins what an admin sees on every canonical path
of every form: `tests/behavioral/golden/<form>/<scenario>.<locale>.json`
is the normalized output of the driver registered under that name in
`tests/behavioral/golden/paths/<form>.py`. They are the UX contract of the
platform (`docs/form-platform-architecture-review.md`, II.3): a change that
moves one must be listed first in `docs/ux-changes.md`, which also keeps
the history of every delta the platform introduced (ux-1 to ux-9).

- `pytest tests/behavioral/golden -q` compares every path with its file
  and fails with the first differing event in transcript form plus a JSON
  diff (`tests/behavioral/harness/golden.py`);
- `pytest tests/behavioral/golden -q --update-golden` rewrites the files
  from the platform — only after the changelog entry exists;
- component ids, `custom_id` and the harness's `step` field never enter a
  golden (`INVISIBLE_FIELDS`): they are invisible to the user and the
  `k:<session>:<revision>:<action>` codec changes them on every render;
- silent defers are dropped and component defaults are filled in before
  comparing, so a golden describes what the admin sees, not how the
  adapter got there.

Adding a path: write the driver with `@golden_path(form, name, locales)`
in the form's module, run once with `--update-golden`, read the recorded
transcript, commit the JSON with the driver. Seed documents through
`seed_document` in `paths/common.py`; it deep-copies, so one path can
never leak its edits into the next.

## Regression scenarios

Location: `tests/behavioral/regressions/`. Rules:

- name the test after the **lasting contract**, not the incident
  (`test_manager_form_keeps_rendering_existing_config_for_all_consumers`,
  not `test_fix_issue_42`);
- the docstring records: what broke, which shared behavior was affected,
  which consumer exposed it, what must remain guaranteed;
- mark with `@pytest.mark.regression` plus the relevant
  `@pytest.mark.shared_contract("<component>")`;
- never delete a passing regression test.

Full bug-fix workflow: `.claude/rules/bug-fix-protocol.md`.

## Shared-component consumer contracts

`tests/behavioral/contracts/test_manager_form_consumers.py` parametrizes
the manager panel over representative real commands with documents in the
**real persisted shape** (the same shape the end-to-end scenarios assert
after the commit). When a new command adopts the manager with a
meaningfully new configuration shape, add one `pytest.param` — do not
write a command-specific test file.

## Extending the harness

- New component type on screen → teach `normalizer.normalize_item` and, if
  clickable, `locators` (keep both master-version-specific).
- New semantic click target → add to `_ALIASES` and `_CODEC_TARGETS` in
  `locators.py`.
- New Discord API call → add to `FakeResponse`/`FakeFollowup` with the same
  strictness (precondition + `HarnessProtocolError` on misuse) and record
  a normalized event.
- Keep the harness generic: no `if command_key == ...` in harness code.

## Troubleshooting

- `HarnessProtocolError` — the adapter (or your scenario) used the
  interaction API out of order; this mirrors a real Discord constraint,
  so suspect a real bug before suspecting the harness.
- `LocatorError` — the target is not on screen; the error lists what is.
  Remember card transitions send and delete messages.
- Stray warnings about tasks — call `await scenario.finish()` at the end
  (the welcome previews render in the background).
- YAML edits not picked up — the registry compiles each form once per
  process; that is production behavior, not a harness bug.
- The runtime is reset between tests by an autouse fixture
  (`RUNTIME.reset()`), so a session from one test never survives into the
  next.
