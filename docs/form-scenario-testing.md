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
(`app/forms/discord/callbacks.py`). The platform decides what
opens from what is saved in the mock Mongo, exactly as in production.

- `await scenario_factory(locale=...).start(command_key)` — nothing saved:
  the setup form.
- `await ....start_manager(command_key, cog_data)` — seeds `cog_data` as the
  guild's document (deep-copied, `guild_id` forced to the scenario's guild)
  and marks the feature enabled in `moderations`, then opens the manager
  panel.
- `await ....start_command(command_key)` — opens the command with whatever
  the scenario seeded before; the platform routes to the setup form or to
  the manager. The golden paths use only this entry point.
- `await ....run_command(command, binding, *args)` — a member runs the app
  command `command` of the cog `binding` as discord.py runs it: the
  interaction carries the command, passes Keiko's tree check, then the
  callback gets `args`; returns the interaction (`/reportar` is reached this
  way).

Every interaction the scenario mints carries `command`, None outside an app
command as on Discord, and holds Keiko's app command tree as
`interaction.client.tree`, the tree the bot syncs, built offline once per run
(`tests/helpers/slash_tree.py`). `scenario_factory` passes it from the
`app_command_tree` fixture; a scenario built by hand takes
`FormScenario(..., tree=)` (read back as `scenario.tree`); a `MockInteraction`
gets `interaction.client = SimpleNamespace(tree=app_command_tree)`.
A test that needs the bot itself behind every interaction sets
`scenario.client` (the report's tests give it the admin server, its reports
channel and the threads on its tickets). A message the harness posts has a `jump_url`, and
`fake_attachment(filename)` (`tests/behavioral/harness/driver.py`) is a file a
user uploaded, described the way Discord describes one.

User actions (each mints a fresh interaction, like Discord):

| Method | Notes |
|---|---|
| `click(target)` | see locator semantics below |
| `select_option(values, target=None)` | names/ids resolve against the mock guild; `target` optional when one select is on screen |
| `submit_modal({label: value}, *, modal=None)` | matches the modal's input labels (exact, then contains); a field inside a `Label` is matched by the Label's text, a select takes an option's label or value, a file upload takes a filename and gets `fake_attachment(filename)`; `modal=` sends a modal opened earlier that a later one replaced as pending; the submit carries the message whose button opened the modal, as Discord sends it, whatever the bot posted since (a modal a command opened carries the message on screen); what `on_submit` raises reaches the modal's own `on_error`, as discord.py routes it, and a modal with discord.py's default `on_error` raises it in the test |
| `dismiss_modal()` | the user closes the modal without submitting — Discord tells the bot nothing, the message that opened it stays on screen |
| `submit_file_upload(filename=, content=, size=, width=, height=, content_type=)` | submits a pending upload modal with a fake attachment (async `read()` runs for real), described the way Discord describes one: a small 800 x 400 PNG by default, any of the four overridden to send a file a validator refuses |
| `pending_modal_fields()` | labels of the pending modal's inputs, selects and uploads included (useful to build the dict for `submit_modal`) |
| `pending_modal_inputs()` | the pending modal's inputs in order, selects and uploads included, to read what the admin is offered (a `max_length`, a default, the options) |
| `dismiss(message=None)` | the admin closes an ephemeral message (Discord's "Dismiss message"), the current one by default: it leaves the screen and the bot is told nothing |
| `confirm()` / `cancel()` / `go_back()` | aliases for the generic buttons |
| `click(target, on=, at=, interaction=)` | `target` is a locator target or a component from `component()`; `on` clicks a message other than the current one, `at` dates the click (`interaction.created_at`), `interaction` delivers one built by `interaction()` (the same click twice, two clicks racing) |
| `component(target, on=None)` | the button or select a target names, as the admin sees it now; clicking it later presses that very component even after the message was drawn again (a stale click) |
| `interaction(on=None, target=None, at=None)` | the interaction Discord would send, not sent: a slash command's with nothing given, a click on `target` of `on` otherwise; hand it to `click(..., interaction=)` or to an entry point the scenario does not open (`open_setup_dashboard`, `run_feature_command`, a records browser) |
| `expire()` | the clock passes every open session's deadline with nobody clicking (`Runtime.expire_stale`) |
| `sweep()` | the expiry loop runs once past every deadline: what nobody finished expires, what ended is forgotten (`Runtime.sweep`) |
| `restart()` | the bot restarts: every session is forgotten and no view survives |
| `click_after_restart(target, on=, interaction=)` | a click that lands when no view holds its message (after `restart()`, or once the view timed out), routed as discord.py routes it: through the dynamic items the bot registers (`registered_items()`, the forms' and every command's `dynamic_items`), rebuilt from the message; returns the interaction |
| `submit_modal_after_restart()` | the pending modal submitted once its form is gone: it reaches `Events.on_interaction`, as on Discord; returns the interaction |
| `click_custom_id(custom_id, on=None)` | a click Discord delivers by its custom id alone to a bot that holds no view for it (a control drawn before a restart, or before a release removed its feature), routed through the dynamic items; `on=None` when there is no message to edit; returns the interaction |
| `expiry_loop_pass()` | one pass of the bot's expiry loop (`Events.sweep_forms`) a session's lifetime from now: what nobody touched since expires, what ended is forgotten, a failing pass is logged and never raised; like `sweep()`, the latest click's token is taken as alive |
| `with later(seconds):` | the clock reads `seconds` later for what runs inside: a session's deadline, the fifteen minutes Discord honours an interaction token, a cooldown, the expiry loop's pass |
| `discord_refuses(api, error=None, times=None)` | Discord answers the next `times` calls of `api` (every one when None) with `error` (a 500 by default) instead of doing them: `response.send_message`, `response.defer`, `response.edit_message`, `response.send_modal`, `followup.send`, `followup.edit_message`, `followup.delete_message`, `edit_original_response`, `delete_original_response`; each refusal is a `refused` event |
| `scenario.latency = seconds` | every first answer minted afterwards takes that long to reach Discord, and `is_done()` turns true only when it arrives; a second first answer sent meanwhile gets 40060 (`discord.HTTPException`), as on Discord; pair it with a short `ViewConstants.DEFER_AFTER_SECONDS` to race the late-answer clock |
| `finish()` | lets any task the flow scheduled settle, the work a feature started in the background (`in_background`, after a save or a yes) included, for a few seconds at most; call at the end of card/design flows, and before asserting on what that work did (yielding to the loop a few times is not enough: the work waits on threads) |
| `open_from(card, source=, ending=, back=)` | every later command opens from a button on `card`, a caller's screen (a /setup row): the card is sent first (`scenario.launcher`), then the feature opens with the `source`, `ending` and `back` that caller passes |
| `run(entry, *args, name=)` | reaches Keiko through an entry point the scenario does not open itself (`open_setup_dashboard`, a greeting's handler): `entry(interaction, *args)` with a fresh interaction of the admin |
| `post(view, embed=None)` | the bot posts `view` outside any command (the greeting on joining a server); returns the message, which is now on screen to click |

Assertions (all raise with the transcript attached):
`expect_step(key)`, `expect_mode(kind, key=None)` (the session on screen runs
as `"setup"`, `"manage"`, `"edit"`, `"add_item"`..., of the feature `key` when
given), `expect_status(status, session=None)` (the session whose id
was read from `scenario.session.id`, or the one on screen, stands as `status`:
`"cancelled"`, `"awaiting"`...), `expect_message(kind=, title_contains=,
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
  defaults included (empty when the current step is not a card);
- `scenario.sessions` — every session the runtime still holds, open or ended;
- `scenario.held_sessions` — the id of every session the bot keeps anything
  for, in the engine's store or in what the adapter holds beside it (a session
  one half forgot and the other kept leaks);
- `scenario.pending_modal` — the modal waiting for a submit, or None;
- `scenario.live_messages` — every message still on screen, oldest first, and
  `scenario.alias(message)` — the name (`M1`, `M2`...) `outputs` gives it;
- `await scenario.fill_tokens(text)` — `text` with its `${...}` tokens filled
  as the form on screen fills its own copy (its answers, the server's name,
  prefix, language and time zone).

Assert on state through those, never by reaching into the store, `RUNTIME` or
discord.py yourself. What a test cannot say with the public actions and reads is
a new action of the driver, added in the same change.

## Flows: the steps tests share (`tests/behavioral/flows/`)

One module per command folder of `app/commands/` (`block_links`, `birthdays`,
`reaction_roles`, `report`, `roles`, `stream_elements`, `twitch`, `welcome`, `youtube`)
plus `common`: the
command's saved documents (`FORM`, `ENABLED`, `PAUSED`), its seeds (`seed`,
`seed_paused`, `seed_config`) and one function per intention that takes more
than a click (`open_card`, `pick_channel`, `submit_streamer`, `type_day`,
`two_panels`, `remove_item`). The golden drivers and every test import these
public names; `module_of(form)` gives the module of a form key. A helper two
test files need that is not a command's flow lives in `tests/helpers/` (the
loop ticks, the Components V2 limits, the activation records, the log
channels, a card's texts and a message's button labels); a test module never
imports another one.

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
| `customize:<section key>` / `reset:<section key>` | the button that opens or resets the card section with that key (`customize:exceptions`, `customize:streamer`); a refused modal step drawn as a one-field card is keyed by its step (`customize:link`). Never a position |
| `option:<label>` | an options-grid button by label |
| `design:<value>` | the design gallery entry whose `value` is `<value>` |
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
  (`tests/behavioral/regressions/test_panel.py`);
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
await scenario.click("customize:exceptions")                 # the section's own screen
await scenario.select_option("general", target="allowed_chats")
await scenario.go_back()                                     # back on the card
await scenario.click("customize:own_links")                  # the list's Add: its modal
await scenario.submit_modal({"Digite o link ou site": "spam.com"})
await scenario.click("done")               # validates the card's `required` and advances
```

A failed validation (e.g. the `date` validator) answers with an error message of
its own below the card; assert it with `expect_error(...)`, which reads the
last error message sent, and retry by clicking the section again: its modal
opens with what was typed. A refused modal step becomes a one-field card with
what was typed; its Edit is `customize:<step key>`. Selections on a card are drafts
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
helpers, or against a golden book (below).

## Golden books

`tests/behavioral/golden/` pins what an admin sees on every canonical path
of every form, in one file per command: `tests/behavioral/golden/<form>.yml`.
The paths are the drivers registered with `@golden_path(form, name, locales)` in
`tests/behavioral/golden/paths/<form>.py`; the same module declares the book
with `golden_book(FORM, rendered=(<panel>, <review>))`. The books are the UX
contract of the platform (`docs/form-platform-architecture-review.md`, II.3):
a change to what an admin sees must be listed first in `docs/ux-changes.md`, which
also keeps the history of every delta the platform introduced (ux-1 to ux-9); a book
that moves only because the tests changed takes no row.

### What a book holds

- `screens`: every distinct screen once, by a name the recorder derives from the
  copy it opens with (`form.panel`, `link_settings`, `exceptions`); a screen close
  to another of its family is stored as `from: <base>` plus `changes` (`set`,
  `add`, `drop` at a path), always computed by the recorder, never written by
  hand, and named after the action that first showed it (`form.panel+pause`).
- `flows`: each path as `<name>.<locale>`, a list of steps, the user's action
  then one line per answer of the bot: `send M1 form.panel`, `delete M1`,
  `send M2 paused (delete_after 5)`.
- Every text is written as the copy key that wrote it, `⟨buttons.edit.label⟩`,
  or with the values the code filled in,
  `⟨commands.responses.paused.description: date=«<t:…:f>»; user=«<@555>»⟩`.
  `tests/behavioral/harness/copy_keys.py` watches the `i18n` reads, `Text.get`
  and the three fills (the token pass, an admin's `{name}` template through
  `tokens.templated`, a `string.Template`) while the path runs. A fill is
  written with every value the code passed, so both locales key alike; a value
  the code lowercased reads as its key, `⟨texts.months.may (lowercase)⟩`; a
  number the code wrote beside a copy word stays beside its key,
  `• ⟨texts.page⟩ 1 / 1`; the whitespace around a copy is layout. Markdown,
  emoji and the admin's own values stay as rendered. A reworded copy moves no
  book.
- The two screens named in `rendered`, the panel and the review (`form.panel`
  and `form.review`; StreamElements calls them `commands.panel` and
  `commands.confirm`), also keep their words in every locale a flow shows them
  in: on the base screen, or, for a locale that never shows the base (en-us
  reaches the panel only at the end of its setup), on the screen of that family
  it shows most. A fill that broke or a bad translation on the main screens
  fails in both languages; `test_every_form_keeps_the_words_of_its_panel_and_its_review`
  refuses a form that names fewer.
- One copy still reads as rendered words, since no seam the trace watches
  splits it: the welcome messages' default, one `;`-joined text the card
  draws as bullets.
- Component ids, `custom_id` and the harness's `step` field never enter a book
  (`INVISIBLE_FIELDS`): they are invisible to the user and the
  `k:<session>:<revision>:<action>` codec changes them on every render. Silent
  defers are dropped, transport is folded (`followup_edit` is an `edit`) and
  component defaults are filled in before recording or comparing, so a book
  describes what the admin sees, not how the adapter got there.

### Re-recording

- `pytest tests/behavioral/golden -q` replays every flow against its book.
- A change that moves screens re-records once, at the end of the change:
  `pytest tests/behavioral/golden --update-golden`. A book is written only when
  every flow of its form ran in that session, so never narrow the run with `-k`
  (the run fails naming the forms it could not write).
- Then read `git diff tests/behavioral/golden/` and confirm only the intended
  text or layout moved: a reworded copy moves only the words kept on the panel
  and the review, once per locale; a new button moves its screen once, plus the
  `changes` of the variants whose paths it shifts.

### Reading a failure

A flow that differs fails with `flow differs from book <path>` and, in order:

1. the flow's lines as a unified diff, book against run: a changed screen keeps
   its line with a `?` (`send M2 form.review?`), and an answer the book does not
   have, or one the run lost, is a lone `+` or `-` line (`+delete M1`);
2. for up to two screens, `screen '<name>' differs` and the JSON diff of the
   expected screen (its base plus its changes) against the run: a key, a style
   or a layout moved;
3. or `screen '<name>' renders other words` and the diff of its words, when the
   keys are the same but the words are not: a fill that broke or a translation
   that changed;
4. the run's transcript.

Adding a path: write the driver with `@golden_path(form, name, locales)` in the
form's module (a new form also calls `golden_book` with its panel and review),
run `pytest tests/behavioral/golden --update-golden` once, read the flow and the
screens it added, and commit the book with the driver. Seed documents through
`seed_document` in `tests/behavioral/flows/common.py`; it deep-copies, so one
path can never leak its edits into the next.

## Regression scenarios

Location: a function in the group file of its command or shared component,
`tests/behavioral/regressions/test_<command or component>.py`
(`.claude/rules/testing.md`). Rules:

- name the test after the **lasting contract**, not the incident
  (`test_manager_form_renders_existing_configuration`,
  not `test_fix_issue_42`);
- the "Broke as" docstring records: what broke, which shared behavior was
  affected, which consumer exposed it, what must remain guaranteed, and its
  entry in `scripts/keiko/testguard/ledger.yml` names the test (`python -m
  scripts.keiko testguard ledger --sync` adds it);
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
  clickable, `locators` (keep both version-specific).
- A `DynamicItem` (a button Keiko routes by its custom id alone) is recorded as the
  component it wraps, its label and colour kept, and a click on it is named by its
  label in the transcript and the books.
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
