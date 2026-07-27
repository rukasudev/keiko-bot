# Writing form scenarios

How to test a complete Keiko form flow offline with the behavioral harness
(`tests/behavioral/harness/`). Strategy and layer overview:
`docs/testing-strategy.md`.

## Quick start

```python
import pytest

pytestmark = pytest.mark.behavioral

async def test_block_links_happy_path(scenario_factory):
    scenario = await scenario_factory(locale="pt-br").start("block_links")
    await scenario.confirm()                                # intro -> next step
    scenario.expect_step("permissions")

    await scenario.select_option("general", target="allowed_chats")
    await scenario.confirm()
    await scenario.click("option:Youtube")
    await scenario.confirm()
    await scenario.submit_modal({"Digite minha resposta": "Nada de links!"})

    scenario.expect_step("confirm")
    await scenario.confirm()                                # -> _finish
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

Entry points (the real seams every cog goes through —
`app/services/moderations.py`):

- `await scenario_factory(locale=...).start(command_key)` — first-time
  setup form. `persistence_callback` resolves automatically the way the
  cogs wire it (birthday → `persist_setup_form`; others → generic cog
  storage).
- `await ....start_manager(command_key, cog_data)` — the manage panel for
  an already-configured command. `settings_provider` resolves like the
  cogs do (birthday → `birthday_manager_settings`).

User actions (each mints a fresh interaction, like Discord):

| Method | Notes |
|---|---|
| `click(target)` | see locator semantics below |
| `select_option(values, target=None)` | names/ids resolve against the mock guild; `target` optional when one select is on screen |
| `submit_modal({label: value})` | matches TextInput labels (exact, then contains) |
| `submit_confirmation(word=None)` | submits a pending ConfirmationModal (pause/disable); defaults to the correct action word — pass a wrong `word` to test rejection |
| `submit_file_upload(filename=, content=)` | submits a pending FileUploadModal with a fake attachment (async `read()` runs for real) |
| `pending_modal_fields()` | labels of the pending modal's inputs (useful to build the dict for `submit_modal`) |
| `confirm()` / `cancel()` / `go_back()` | aliases for the generic buttons |
| `finish()` | drains stray tasks; call at the end of card/design flows |

Assertions (all raise with the transcript attached):
`expect_step(key)`, `expect_message(kind=, title_contains=,
description_contains=, content_contains=, ephemeral=, components_v2=,
has_component=)`, `expect_component(label_or_action=, disabled=)`,
`expect_error(text)`, `expect_modal(title_contains=, field_labels=)`,
`expect_configuration_values(*values)` (manager summary),
`expect_persisted(db, collection, filter, subset)` /
`expect_not_persisted(...)` / `get_persisted(...)`.

Introspection: `scenario.outputs` (normalized events), `scenario.transcript`,
`scenario.current_message`, `scenario.responses`, `scenario.active_form`
(descends into composition sub-forms), `scenario.db`.

## Locator semantics — what `click("...")` accepts

Resolution order: explicit `custom_id` → localized button label
(case-insensitive) → semantic alias. Aliases (locale-independent, resolved
through the real i18n keys):

| Target | Resolves to |
|---|---|
| `continue` / `confirm` | the green confirm button |
| `cancel` | cancel button, `card_cancel`, `cancel_design` |
| `back` | back button, `card_back`, `picker_back`, `back_design` |
| `done` | `card_done` (configuration card) |
| `customize:N` / `edit:N` / `reset:N` | `card_customize_N` / `card_edit_N` / `card_reset_N` |
| `option:<label>` | an options-grid button by label |
| `design:<key>` | `design_<key>` |

Unknown targets raise `LocatorError` listing everything clickable on
screen. For multi-selects, `select_option(..., target=...)` also accepts
the YAML select key (e.g. `allowed_chats`).

The dispatcher supports the four callback patterns used in the repo
(attribute-shadowed callbacks, `callback` subclasses, `@discord.ui.button`
decorators, and LayoutViews routed via `interaction_check` +
`custom_id`) — see `tests/behavioral/harness/locators.py`.

## Configuration cards (Components V2)

Cards are LayoutViews; the transition to/from them is delete+resend, which
the transcript shows as `delete` + `followup_send` with `CV2`. Typical
interaction:

```python
await scenario.click("customize:0")        # opens the section picker/modal
await scenario.select_option("general")    # picker select -> back on the card
await scenario.click("customize:1")
await scenario.submit_modal({"Dia": "12"}) # modal-input section
await scenario.click("done")               # validates `required:` and advances
```

A failed YAML validation (e.g. `validate_date`) sends an ephemeral error
and keeps the card as the actionable message; assert it with
`expect_error(...)` and retry by clicking the section again.

## Normalized output

One event per engine API call (never coalesced), shape:

```python
{"seq": 6, "actor": "bot", "kind": "send|edit|defer|modal|followup_send|"
 "followup_edit|delete|edit_original|delete_original|channel_send",
 "message": "M3", "ephemeral": True, "step": "birthday_config",
 "embed": {"title", "description", "fields", "footer", "color", ...},
 "components": [...], "modal": {...}, "components_v2": True}
```

Unstable data never appears: message ids become `M1, M2, ...` aliases,
auto-generated custom_ids are omitted (only ids set by production code
show up as `action`), timestamps are dropped, URLs reduced to basenames.
Two identical runs produce identical `outputs` (enforced by
`test_harness.py`). There is no snapshot file mechanism; assert on the
normalized dicts or with the `expect_*` helpers.

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
the manager view over representative real commands with cog documents in
the **real persisted shape** (the same shape the end-to-end scenarios
assert after `_finish`). When a new command adopts the manager with a
meaningfully new configuration shape, add one `pytest.param` — do not
write a command-specific test file.

## Extending the harness

- New component type on screen → teach `normalizer.normalize_item` and, if
  clickable, `locators` (keep both master-version-specific).
- New semantic click target → add to `_ALIASES` in `locators.py`.
- New engine API call → add to `FakeResponse`/`FakeFollowup` with the same
  strictness (precondition + `HarnessProtocolError` on misuse) and record
  a normalized event.
- Keep the harness generic: no `if command_key == ...` in harness code.

## Troubleshooting

- `HarnessProtocolError` — the engine (or your scenario) used the
  interaction API out of order; this mirrors a real Discord constraint,
  so suspect a real bug before suspecting the harness.
- `LocatorError` — the target is not on screen; the error lists what is.
  Remember card transitions delete and resend messages.
- Scenario hangs on nothing / stray warnings about tasks — call
  `await scenario.finish()` at the end (design-preview pregeneration).
- YAML edits not picked up — `parse_form_yaml_to_dict` is cached per
  process; that is production behavior, not a harness bug.
