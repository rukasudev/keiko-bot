# Keiko form configuration reference

> **Python implements reusable capabilities. YAML combines those capabilities to create
> commands, forms, and interaction flows.**

This document describes how Keiko's YAML-driven form engine actually works today, where its
extension points live, and when a change requires Python. Every claim below is anchored to a
real file. If this document and the code disagree, the code and the passing tests win —
fix this document.

For user-facing copy and Discord UI conventions, read
`.claude/skills/keiko-writing-style/SKILL.md` (mandatory before changing any user-visible
text or component). For the mandatory plan structure, read
`.claude/rules/implementation-planning.md`.

## 1. Overview

A feature command is declared as a YAML file in `app/languages/form/`. The filename is the
command key (a constant in `app/constants.py`, class `Commands`). Current forms:

```text
block_links.yml            default_roles.yml        notifications_twitch.yml
notifications_youtube_video.yml    reminders_birthday.yml
stream_elements_commands.yml       welcome_messages.yml
```

Each file has a single top-level `steps:` list. Unlike the paired locale namespaces
(`app/languages/{buttons,commands,errors,messages,locales}/<ns>.<locale>.yml`, resolved at
runtime via `ml(key, locale)` in `app/services/utils.py`), form files are **one bilingual
file per command**: every human-readable value is an inline locale dict
`{en-us: ..., pt-br: ...}`. Both locales are mandatory for every string.

```yaml
steps:
  - action: form            # intro screen (always first)
    key: form
    title:
      en-us: ":no_entry_sign: Block links"
      pt-br: ":no_entry_sign: Bloquear links"
    description: {en-us: "...", pt-br: "..."}
    footer: {en-us: "• Use the `/report` command...", pt-br: "• Use o comando `/reportar`..."}
  - action: multi_select
    key: permissions
    selects:
      - {type: channels, key: allowed_chats, style: channel, label: {...}, placeholder: {...}}
      - {type: roles, key: allowed_roles, style: role, label: {...}, placeholder: {...}}
  # ... more steps ...
  - action: resume          # summary + confirm (always last)
```

## 2. Load and execution path

```text
cog (app/cogs/...) 
→ service.manager(interaction, guild_id)              # one per command, app/services/<key>.py
→ send_command_form_message / send_command_manager_message  (app/services/moderations.py)
→ Form view (app/views/form.py) driven by the YAML steps
→ child component views (app/components/, app/views/)
→ Form._finish → persistence_callback or generic cog storage (app/services/cogs.py → app/data/)
```

- **Loader**: `parse_form_yaml_to_dict(key)` — `app/services/utils.py`. Reads
  `app/languages/form/<key>.yml` and returns the `steps` list. It is `@functools.cache`d:
  YAML edits require a process restart.
- **First-time setup vs manage**: each service's `manager()` checks whether the guild already
  has config. Unconfigured → `send_command_form_message` (runs the form). Configured →
  `send_command_manager_message` (renders the `Manager` view in `app/views/manager.py` with
  Edit / Pause / Disable / Add / Remove / History, re-using the same YAML for titles and
  value rendering).
- **Step loop**: `Form` (`app/views/form.py`, a `discord.ui.View`) advances through steps via
  the `_update_form_step` decorator (`form.py`). The `action: form` intro step is shown
  once and skipped as a step. A step is skipped when its YAML `condition` is not met:

  ```yaml
  condition:
    key: register_now        # a previous step's key (or a card field key)
    not_in: [false, "false", "False"]
    # matches: "[/?]"        # optional regex: step runs only when the value matches
  ```

  Both rules are evaluated by the single helper `condition_allows`
  (`app/services/utils.py`), shared by the form engine, the manager summary
  and the edit-step filter. `matches` lets a step ask only what cannot be
  assumed from an earlier answer (e.g. block_links only asks domain-vs-exact
  when the typed link goes beyond a bare domain).

- **State**: `FormStateManager` (`app/views/form_state.py`) owns the step index and previous
  answers; its `fill_*` methods re-populate a component when the user navigates back.
  Everything lives on the view instance (timeout 1800s) — there is no cross-request draft
  persistence.
- **Responses**: each answered step is stored in `Form.responses` as
  `{key, title, value, style, hidden?, _raw_value?}`. `_raw_value` keeps the machine value
  when `value` holds a localized label.
- **Finish**: `Form._finish` (`form.py`) builds the cog document via
  `_parse_responses_to_cog()` and either calls the command's `persistence_callback`
  (only `reminders_birthday` uses one today) or does the generic path:
  `update_moderations_by_guild` + `insert_cog_by_guild` + `insert_cog_event`, then shows the
  `commands.command-events.enabled` embed.

## 3. Step action reference

Dispatch is a plain dict in `Form.get_action_by_type` (`app/views/form.py`); action
names are constants in `app/constants.py`, class `FormConstants`. Unknown actions silently
no-op.

| YAML `action` | Handler | Renders |
|---|---|---|
| `form` | — | intro embed only; auto-skipped as a step |
| `modal` | `show_modal` | `CustomModal` (`app/components/modals.py`) |
| `options` | `show_options` | `OptionsView` (`app/views/options.py`) button grid |
| `channels` | `show_channels` | `ChannelSelectView` when `select: true`, else options grid |
| `roles` | `show_roles` | `RoleSelectView` when `select: true`, else options grid |
| `available_roles` | `show_available_roles` | like `roles`, filtered to assignable roles |
| `user_select` | `show_user_select` | `UserSelectView` (`app/components/select_views.py`) |
| `multi_select` | `show_multi_select` | `MultiSelectView` — several selects in one step |
| `design_select` | `show_design_select` | `DesignSelectView` (LayoutView / Components V2) |
| `file_upload` | `show_file_upload` | `FileUploadModal` |
| `button` | `show_buttons` | info screen with `fields` + Continue |
| `composition` | `show_composition` | `FormComposition` (`app/views/composition.py`) — nested sub-form producing a list of items |
| `configuration_card` | `show_summary_card` | `SummaryCardView` (`app/views/summary_card.py`, LayoutView) |
| `resume` | `show_resume` | final summary + Edit/Add/Remove/Confirm |
| `month_select` | `show_month_select` | **dead path** — registered, no YAML uses it |
| `summary_card` | `show_summary_card` | **dead path** — superseded by `configuration_card` |

Step descriptions may echo an earlier answer with `{response:<key>|<fallback>}`
(resolved by `Form._apply_response_tokens` against the running form's responses;
the fallback renders while the key has no answer). Prefer this over canned
examples whenever a step explains a choice about something the user just typed.

Common step keys: `key`, `title`/`description`/`footer` (locale dicts), `emoji`, `style`,
`hidden`, `required`, `unique`, `auto_confirm`, `condition`, `options[]`, `fields[]`,
`selects[]`, `steps[]` (composition only), `parent_key`, `unique_by`, `response_transform`,
`validation`, `max_length`, `lowercase`, `multiline`, `enumerate`, and (cards only)
`header`, `sections[]`, `defaults`, `template-vars`.

## 4. Extension points — where to add capability X

Search these registries before proposing anything new. If a name can be added to one of
these tables/classes, the change is a **small generic extension**, not new architecture.

### Validators — `ModalValidations` (`app/components/modals.py`)
YAML `validation: <name>` resolves to a method on `ModalValidations` via `getattr`. A
validator returns `{"ok": bool, "error_key": str}`; the error key maps to
`errors.<error_key>` in `app/languages/errors/*.yml` and is rendered by
`response_error_embed`. Existing: `validate_streamer_name`, `validate_youtube_channel`,
`validate_date`. The same names are used by card `reset-on-change` (see below).

### Response transforms — `RESPONSE_TRANSFORMS` (`app/services/transforms.py`)
YAML `response_transform: <name>` resolves to a registry entry declaring `part_keys`,
the stored `value_key`, the summary `style`, and a `serialize`/`hydrate` pair.
`Form._transform_step_response` and `Form._save_summary_card_response` serialize parts
into the stored value; `build_summary_card_from_step` hydrates the stored value back into
per-part state keys. Single-input (scalar) steps also apply their transform when
`value_key` equals the step key. Existing: `mm_dd_date_parts` (`day`+`month` ⇄ `date`,
style `mm_dd`) and `normalize_link` (strips scheme/`www.`/trailing slash/fragment,
keeps the query — used by block_links so saved links are clean and comparable).

### Value formatting styles — `format_values_by_style` (`app/services/utils.py`)
YAML `style:` on steps/fields selects how stored values render in summaries and the manager
panel. Existing styles: `channel`, `role`, `user`, `bullet`, `numbered`, `boolean`,
`boolean-mode`, `mm_dd`, plus `composition` (handled separately by
`get_styled_composition_values`).

### Multi-select types — `MultiSelectView` (`app/components/select_views.py`)
`selects[].type` supports `channels` and `roles`/`available_roles`. New Discord select kinds
are added here, not as new one-off views.

### Card section types — `SECTION_TYPES` (`app/views/summary_card.py`)
`configuration_card` steps declare `sections[]` with a `type` resolved by this registry:
`title-content`, `file-upload`, `channel-select`, `value-select`, `button-options`,
`boolean-toggle`, `modal-input`, `multi-select` (multi-value string picker; state holds a
list). Supporting card keys, all YAML-driven:
- `state:` — maps a section to the state keys it edits (`{value: X}` or `{mode, title, content}`…);
- `defaults:` — initial state values; a `{en-us, pt-br}` dict is resolved to the user's
  locale (used for prefilled text like the block_links default answer);
- `required:` — keys that must be set before Done (missing labels are listed in the
  warning; keys owned only by hidden sections are skipped);
- `visible-when:` — `{key, not_in}` on a section: it only renders (and only counts for
  `required:`) while the card state satisfies the rule (e.g. block_links hides the
  popular-websites section in allow_all mode);
- `picker-title:` / `picker-description:` — heading and body copy of the picker screen the
  section opens (`SummaryCardPickerView` / `SummaryCardButtonOptionsView`). The description
  is where a section explains what each option does before the user commits (one line per
  option, led by its emoji, per the writing-style choice layout);
- `options[].style:` — `primary`/`secondary`/`success`/`danger`, resolved by the shared
  `resolve_option_style` (`app/components/buttons.py`), the same helper the `options` steps
  use. The card's button picker paints each option from it (block_links: allow-all green,
  block-all red);
- `template-vars:` — resolved by `_resolve_value` (`from: response|interaction`, `key`/`attr`,
  `format` → `format_values_by_style`) and substituted into previews with `{name}`;
- `reset-on-change:` — `[{key, validation}]` clears a dependent key when the named
  validator rejects the new combination (e.g. changing month invalidates day 31).

Card fields whose sections carry `options` store the localized label as the response
`value` and the raw option value as `_raw_value` (persistence and `condition:` use the
raw; resume/manager show the label). Step-level `condition:` may reference card state
keys, since card fields become responses.

The card and its pickers are Components V2 LayoutViews, so they never go through
`parse_form_dict_to_embed`: the step's `footer:` is rendered by `_add_footer` as `-#`
subtext at the bottom of the container (`SummaryCardConfig.footer`).

### Conditional copy — `description-when:` (`app/views/form.py`)
A step may carry variants of its description, each guarded by the same `condition:` grammar
used to skip steps:

```yaml
description:            # the default wording
  en-us: "..."
description-when:
  - condition: {key: mode, not_in: [block_all]}
    en-us: "..."
    pt-br: "..."
```

The first variant whose condition holds replaces the description, before `{response:...}`
tokens are substituted. The condition value is looked up in this order: answers given in
this run, the saved configuration (`cogs`, so the manager Edit/Add flows still resolve it),
and the context inherited from the parent form. That last step is what lets a composition
sub-step word itself by a choice made before the composition started
(`FormComposition(parent_context=...)`) — block_links uses it to say whether an entry will
always allow or always block the link. Pinned by
`test_description_variants_reference_keys_produced_earlier`.

### Manager panel — `ManagerPanelView` (`app/views/manager_panel.py`)
`build_command_manager_message` returns the manager as a **Components V2 container**, not an
embed: the header, the saved configuration and every button live inside it. The first YAML step
is still parsed into an embed, but only to read the header the container draws (title, intro,
footer) from the source the form already uses.

The rows come from `parse_settings_with_database_values`. Each row carries the `icon` its own
YAML declares, resolved by `resolve_form_settings_icons` (`app/services/utils.py`) in this order,
first hit wins: card `sections[].icon` (applied to every state key the section owns) → card
`fields[].icon` → `selects[].icon` → the step's `emoji:` → 🥏 as fallback. Add `icon:` to a select
or card field to give a setting its own identity in the panel — no Python involved.

Rows are grouped into the panel's **sections** by `resolve_form_settings_groups`, which maps each
setting to the step that owns it (a card owns its `fields`, a multi-select owns its `selects`, a
composition owns itself). Each section sits next to a `SectionEditButton` that jumps straight into
that step of the edit flow, skipping the "which setting?" dropdown. Two rules keep the hierarchy
readable: a section whose title repeats the command's draws no heading, and the emoji belongs to
the heading alone — the settings under it are plain `**Label:** value` lines.

A setting no step owns (a command with a custom `settings_provider`, like birthday) falls into the
group with no key: no heading, no pencil of its own, and the panel keeps the **global Edit button**
— which is then the only way into the edit flow. Pinned by
`tests/behavioral/contracts/test_manager_panel.py`, including Discord's Components V2 limits
(40 components, 4000 characters of text), which the API only enforces at runtime.

Events (pause, unpause, disable, edit, add, remove) answer with a fresh embed message and no
controls: the panel's own buttons hold the configuration as it was *before* the change. That is
`Manager._announce_event` / `_event_embed`, pinned by
`tests/behavioral/regressions/test_manager_event_embeds.py`.

Buttons that answer with a message of their own (Help, Preview, Stats, the blocked-links list,
role sync) carry an `ActionCooldown` (`app/components/buttons.py`): a click inside the window
gets a self-deleting notice (`buttons.cooldown.*`) instead of a second copy, and the button never
leaves the screen — never remove or clear items from a view whose message stays visible.
Navigation buttons (Add, Edit, Remove, lifecycle) have no cooldown: double-clicking them is
protected behavior. Pinned by `tests/behavioral/contracts/test_view_action_cooldown.py` and
`tests/behavioral/regressions/test_view_lifecycle_regressions.py`.

> **Components V2 flags are fixed at send time.** A message sent with a container can never be
> edited back into an embed message. Every screen reached from the panel therefore goes through
> `app/views/panel_transitions.py` (`transition_to_embed`, `close_panel`), which replaces the
> message instead of editing it. The form engine uses the same helper
> (`Form._transition_from_layout_view`). Pinned by
> `tests/behavioral/contracts/test_panel_transitions.py`.

### State refill — `FormStateManager.fill_*` (`app/views/form_state.py`)
One `fill_<kind>` method per component family restores the previous answer on back
navigation; `Form._send_view` falls back to hydrating from saved DB config (`cogs`).

### Embed rendering — `parse_form_dict_to_embed` (`app/components/embed.py`)
Turns a step dict into the step embed (title + `emoji`, description, `footer`, thumbnail via
`KeikoIcons.ACTION_IMAGE`).

### Command wiring
- `ExecuteCommandButton.COMMAND_SERVICES` (`app/components/buttons.py`) — the
  command-key → service-module registry, also used by `/setup` (`app/views/setup.py`).
- `send_command_form_message(..., persistence_callback=...)` — custom save logic.
- `send_command_manager_message(..., settings_provider=..., lifecycle_callbacks=...)` —
  custom summary rendering and per-lifecycle hooks (`Commands.LIFECYCLE_EDIT`, `_PAUSE`,
  `_UNPAUSE`, `_DISABLE`, `_ADD_ITEM`, `_REMOVE_ITEM` in `app/constants.py`). Prefer these
  hooks over `if command_key == ...` branches.
- `send_command_manager_message(..., additional_buttons=[...], additional_info="...")` —
  buttons and a closing paragraph that belong to one command only (birthday stats;
  block_links blocked list, stats and the app-command tip). `AdditionalButton` answers the
  interaction for the callback, unless it is built with `own_response=True`, which a callback
  that opens its own view (a `PaginationView`, for instance) requires. The behavioral harness
  resolves both through `_ADDITIONAL_BUTTONS` / `_MANAGER_INFO` / `_MANAGER_INFO_TITLE`
  (`tests/behavioral/harness/driver.py`), so scenarios see the panel the user sees; register
  a command there when it starts passing them. `additional_info_title="..."` turns that
  paragraph into a titled section instead of a trailing note.
- `build_command_manager_message(...) -> (embed, view)` is the same assembly without sending,
  for a button that needs to redraw the panel in place (`interaction.response.edit_message`).
- Mongo indexes and retention live in `app/data/indexes.py`, applied once by `create_app`.

### Reusable localized copy (never hardcode strings in Python)
- Button labels: `buttons.*` in `app/languages/buttons/*.yml` (confirm, cancel, edit, back,
  add, remove, continue, pause, unpause, preview, history, select…).
- Errors: `errors.*` — `{title, message}` pairs.
- Command state changes: `commands.command-events.{enabled,paused,unpaused,disabled,edited,added,removed}`.
Access via `ml(key, locale)`; always add both `en-us` and `pt-br`.

## 5. Building a new YAML-driven command from existing primitives

Use `reminders_birthday` as the reference implementation — it is the only command exercising
the full set (`configuration_card`, `composition`, `persistence_callback`,
`settings_provider`, `lifecycle_callbacks`). Checklist:

1. Add the command key constant to `Commands` (`app/constants.py`) and, if it appears on the
   `/setup` dashboard, to `SETUP_FEATURES` / `FEATURE_COMMANDS`.
2. Create `app/languages/form/<command_key>.yml` — open with `action: form`, close with
   `action: resume`, compose the middle from the actions in §3. Follow the writing-style
   skill for all copy.
3. Create the service module `app/services/<command_key>.py` exposing
   `async manager(interaction, guild_id)` that routes to form vs manager (copy the shape of
   an existing service, e.g. `app/services/block_links.py`).
4. Register it in `ExecuteCommandButton.COMMAND_SERVICES`.
5. Create the cog under `app/cogs/...` using `locale_str` names (`app/translator.py`) and add
   the slash-command copy to `app/languages/commands/*.yml` (both locales; descriptions in
   third person per the skill).
6. Persistence: prefer the generic cog storage (no code). Only pass a
   `persistence_callback` when the data genuinely doesn't fit the cog document model.
7. Tests: follow `tests/` patterns (`conftest.py` loads real i18n and mocks Mongo/Redis/
   Discord); assert against real YAML via `parse_form_yaml_to_dict`.

## 6. When Python is required

YAML alone cannot add: a new step action, a new validator, a new response transform, a new
formatting style, a new multi-select type, a new card section type, or new persistence
behavior. Each of those is a **small, generic** addition to one of the registries in §4 —
named generically, containing no command-specific assumptions, and reusable by other forms.
Everything else — new steps, copy, options, conditions, defaults, required keys, section
composition, reordering — is configuration.

## 7. Existing architecture exceptions (documented, not to be imitated)

Known command-specific or convention-breaking code. Do not extend these; new work must use
the generic mechanisms above. If you must touch one, follow the architecture-exception rules
in `.claude/rules/implementation-planning.md`.

- `app/views/manager.py` (`disable_callback` / `remove_item_callback`) — twitch/youtube unsubscribe `if command_key`
  branches (marked `# TODO`), duplicated in disable and remove-item; the
  `lifecycle_callbacks` registry is the intended replacement.
- `app/views/form.py` — `pre_finish_step` stream_elements branch with hardcoded
  English `"Channel ID"` title; `_start_preview_pregeneration` hardwired to
  `welcome_messages`.
- `app/views/form.py` (`parse_cogs_to_modal` / `_parse_cogs_to_select`) — birthday-specific
  `MM-DD` split when hydrating modal/select from saved config.
- User-facing strings in Python: `to_summary_composition` titles (`app/data/birthdays.py`),
  `_format_boolean_value` "Sim"/"Não" (`app/services/utils.py`).
- Duplication: the "unwrap `{value|values}`" helper exists ~5 times (`app/services/compositions.py`,
  `app/services/reminders_birthdays.py`, `app/views/{manager,edit,remove}.py`); four
  near-identical select view classes in `app/components/select_views.py`; `bullet` and
  `numbered` bodies are swapped between `format_single_value` and `format_list_values`.
- i18n parity gap: `commands.command-events.*.short-description-with-cog` exists only in
  `en-us` (7 keys).
- Dead paths kept for compatibility: `month_select` / `summary_card` actions and the
  `validation_context_keys` branch in `Form.show_modal`.

## 8. Enforcement and future recommendations

`tests/test_reusable_configuration.py` is the architectural contract suite: it asserts the
birthday feature is built only from generic primitives (shared `configuration_card`
sections declared in YAML, generic `boolean-mode`/`mm_dd` styles, a single shared
discard-confirmation path for every cancel button, real localized copy loaded through the
real i18n stack). Run it plus the full suite:

```bash
.venv/bin/python -m pytest tests/test_reusable_configuration.py -q
make test    # = python -m pytest tests/ app/ -x -q
```

For an on-demand audit of a branch, file, directory, or the whole project against these
conventions, use the `keiko-architecture-review` skill
(`.claude/skills/keiko-architecture-review/SKILL.md`).

The offline behavioral suite (`tests/behavioral/` — YAML contract tests,
full-flow scenarios, shared-component consumer contracts and permanent
regression scenarios) enforces this architecture end to end; see
`docs/testing-strategy.md` and `docs/form-scenario-testing.md`.

Future recommendations (out of scope for routine changes; do not do silently):
unify the unwrap helpers;
fix the bullet/numbered swap (behavior change — needs its own test); migrate the
`manager.py` twitch/youtube branches to `lifecycle_callbacks`; localize the hardcoded
strings listed in §7; add the missing pt-br `short-description-with-cog` keys; parameterize
the duplicated select views; remove the dead paths; stop using localized text as button
`custom_id` (`PauseButton`/`UnpauseButton`/`DisableButton` in `app/components/buttons.py`).
