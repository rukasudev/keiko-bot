# Keiko form configuration reference

The map of the form platform (`app/settings/`): what a form declaration may say,
how the engine turns it into screens, where each extension point lives, and
what it takes to add a form. Read it before touching a YAML form, a step
kind, a feature module or the Discord adapter. The contract it documents is
`docs/form-platform-architecture-review.md` (Part II prevails); the UX the
engine must reproduce is pinned by the golden transcripts
(`docs/form-scenario-testing.md`).

Core principle, unchanged:

> **Python implements reusable capabilities. YAML combines those capabilities
> to create commands, forms, and interaction flows.**

## 1. Overview

A form is a short, guided session of one admin. The platform splits it in
four layers with a fixed dependency direction (features depend on the
platform; the adapter depends on the platform and the features; the
platform depends on nothing internal but itself and `app/constants.py`):

| Layer | Package | Owns |
|---|---|---|
| What a form declares | `app/settings/form/form_yaml.py` | the typed schema, the YAML compiler with its legacy aliases, the registry and the source of definitions |
| The engine | `app/settings/form/` | the frozen session and its store (`form_state.py`), events (`events.py`), effects (`effects.py`), the `when` evaluator (`conditions.py`), the screen model (`components.py`), what each click does (`form.py`), the manager (`manager.py`) |
| Actions and responses | `app/settings/form/actions/`, `app/settings/form/responses/` | one render and parse pair per YAML action; a response saved, validated, transformed, styled and summarised |
| Features | `app/settings/features/` | one module per feature: answers to document and back, the commit, side actions |
| Discord | `app/settings/discord/` | where sessions open and every click lands (`callbacks.py`), the description turned into Discord (`views.py`), the message replaced (`transitions.py`), click to event and the `custom_id` codec (`interactions.py`), observability (`observability.py`) |

Only the adapter imports `discord`. Nothing in `app/settings/` names a command
key, blocks the event loop, or reaches another feature's document; those
lines are enforced by `tests/forms/test_boundary.py`.

## 2. Load and execution path

1. A cog's slash command calls the feature service's `manager(interaction,
   guild_id)`, which calls `open_feature(interaction, key)`
   (`app/settings/discord/callbacks.py`). The `/setup` buttons reach
   the same function through `run_feature_command`
   (`app/components/buttons.py`).
2. `Runtime.open_feature` asks the feature module (`feature_for(key)`,
   `app/settings/features/__init__.py`) to `open` the guild: it answers with the
   saved document, or nothing. Nothing saved opens a `Setup` session; a
   document opens a `Manage` session over the manager panel.
3. The definition comes from the registry (`registry.get(key)`), which
   compiles `app/languages/form/<key>.yml` once per process through
   `compile_form` (`app/settings/form/form_yaml.py`). A definition that
   does not compile raises `CompileError` naming the form, the step, the
   field and the reason; it never reaches an admin.
4. Every interaction on a form message carries a `custom_id` of the shape
   `k:<session>:<revision>:<action>[:<arg>]` (`interactions.py`). The adapter decodes
   it, takes the session's `asyncio.Lock`, turns the interaction into an
   event (`interactions.to_event`), and when a submitted modal's validator
   declares an `external:<service>` need (`lookups.lookup_for`, for a text step
   or a card section) defers the interaction and runs the feature's `prefetch`,
   then calls `decide(definition, session, event, context)`.
5. `decide` is pure: it returns a `Decision` with the next session, the
   effects to run in order, the rules it evaluated and the analytics events
   to emit. The adapter stores the session (`put` bumps the revision;
   `remember` records a rejected event without bumping it), emits the
   analytics, logs one line, and the `Executor` runs the effects on Discord.
6. `Commit` hands the feature module the payload of the session (`commit(kind,
   payload, context)`); the outcome comes back to `decide` as
   `CommitSucceeded` or `CommitFailed`, and `Finalize(kind)` closes the
   message with the matching `commands.command-events.<kind>` copy.

## 3. The definition schema

`app/settings/form/form_yaml.py` is the whole vocabulary: pydantic models
that reject unknown fields, carry both locales for every piece of copy, and
are frozen once built. The compiler accepts the YAML the seven forms are
written in today and translates it into these models, so the YAML reference
below names the YAML spelling first and the model second.

### 3.1 Common to every step

| YAML | Model field | Meaning |
|---|---|---|
| `key` | `key` | the answer key this step produces (a text step with named `fields` produces those keys instead) |
| `action` | `kind` | the step kind, through the alias table in 3.2 |
| `title`, `description`, `footer` | `Text` | copy in `en-us` and `pt-br`; `description` accepts `{response:key:formatter|fallback}` tokens (formatter: `host`) resolved from earlier answers |
| `emoji` | `emoji` | the step's icon on summaries; on the manager panel it leads each line the step owns (a card section's or a select's `icon` wins for its keys, the frisbee stands in when nothing is declared) |
| `required` | `required` | the step must hold a value before Confirm |
| `hidden` | `hidden` | the answer is a gate: never listed on the review or the panel |
| `when` (legacy `condition`) | `when` | the step is shown only while the rule holds (section 4) |
| `description-when` | `description_when` | variants of the description, each with its own `when` |
| `style` | `style` | how summaries format the value: `channel`, `role`, `user`, `bullet`, `numbered`, `code`, `boolean`, `boolean-mode`, `mm_dd`, `composition` |

### 3.2 Step kinds

| `action:` in YAML | Kind | Model | What it renders |
|---|---|---|---|
| `form` | `intro` | `IntroStep` | the first screen: what the feature does, then Confirm; `manager_description` is the manager panel's intro, written as what the feature is doing (falls back to `description`) |
| `modal` | `text` | `TextStep` | a modal with one text input (`label`, `placeholder`, `max_length`, `multiline`, `lowercase`, `normalize` (a registered input normalizer such as `handle`: no spaces around, no leading @, lowercase), `validation`, `transform`) or several named `fields`; `lookup_answers` keeps named values of the lookup its modal ran (`answer_key: service.field`) as hidden answers a later description reads with `{response:answer_key\|fallback}`, never listed and never saved |
| `file_upload` | `text` with `input: file` | `TextStep` | a modal with a file input |
| `options` | `single_choice` | `SingleChoiceStep` | one button per `option` (`label`, `value`, `style`); `unique`, `styled_values`, `auto_confirm` |
| `design_select` | `single_choice` with `designs` | `SingleChoiceStep` | a gallery, one `Design` (`key`, `label`, `description`) per entry, with the feature's previews |
| `channels` | `channel_pick` | `ChannelPickStep` | a native channel select (`unique`) |
| `roles`, `available_roles` | `role_pick` | `RolePickStep` | a native role select; `available_roles` limits it to roles the bot can assign |
| `user_select` | `user_pick` | `UserPickStep` | a native member select |
| `multi_select` | `multi_pick` | `MultiPickStep` | several native selects on one screen (`selects`: `type` in `channels`/`roles`/`available_roles`, `key`, `style`, `icon`, `label`, `placeholder`) |
| `configuration_card` | `card` | `CardStep` | a Components V2 card whose sections each edit part of the answer (3.3) |
| `composition` | `composition` | `CompositionStep` | a list of items, each built by a child session over `steps` (3.4) |
| `button` | `info` | `InfoStep` | a read-only screen with titled paragraphs (`fields`, per locale) and Confirm |
| `resume` | `review` | `ReviewStep` | the last screen: a card with the manager panel's blocks, each with its Edit (returning to the review), and Add, Remove, Preview, Confirm and Cancel below; a list shows its first `COMPOSITION_PREVIEW_LIMIT` items and a "+N more" line; `preview: true` adds the Preview aside |

The alias table is `KIND_BY_ACTION` in the compiler. `condition:` still
compiles into `when` with a deprecation warning; new forms write `when`.

### 3.3 Cards

A `CardStep` declares `sections`, each with `key`, `icon`, `label`, a
`state` (the keys the section owns: `value`, `mode`, `title`, `content`,
`url`), an optional `customize_label`, `style`, `picker_title`,
`picker_description`, and a `visible-when` rule. `required_keys` names the
state keys Done insists on, `defaults` seeds them, `header` draws the title
block (`title`, `title_emoji`, `thumbnail`, `lines` resolved from earlier
answers), `fields` declares how summaries show each persisted key, `context`
declares template variables (`answer` + `format`, or `context: server_name`)
for `{name}` in previews, and `transform` names a registered transform.

| `type` | Section model | What the admin does |
|---|---|---|
| `title-content` | `TitleContentSection` | keeps the `default` title and body or types both in the `modal` |
| `file-upload` | `FileUploadSection` | keeps the default image or uploads one; without a `mode` in its `state` it only holds the uploaded image, and counts as set once it has one |
| `channel-select` | `ChannelSelectSection` | picks one channel on a native select |
| `value-select` | `ValueSelectSection` | picks one of the declared `options`; `reset_on_change` clears a dependent key when the named validator rejects the new pair |
| `button-options` | `ButtonOptionsSection` | picks one of the declared `options` on a row of buttons |
| `boolean-toggle` | `BooleanToggleSection` | flips a yes or no |
| `modal-input` | `ModalInputSection` | types in the `modal`: a field with a `key` writes that key, fields without one are joined with `;` into the section's `value` (the shape services split); each field may `normalize`; the modal reopens with the saved values; `validation` runs on submit with the other items and any lookup its validator needs |
| `design-select` | `DesignSection` | picks one of the declared `designs` on the design gallery, with the feature's previews |
| `multi-select` | `MultiSelectSection` | picks several of the declared `options` |

A hidden section (`visible-when` false) does not count as required. Card
selections are drafts until Done: the card keeps its values on Back and on
a failed validation. A modal holds at most five inputs.

`edit_by_field: true` on a card or a multi-select gives each of its lines its
own Edit on the panel and the review: the click opens only that section or
select (`Edit.part`), saves the edit as soon as a value is chosen (from the
manager) or returns to the review (in the setup), cancels on Back, and shows
the whole card when the choice leaves it incomplete. It is refused inside a
composition.

### 3.4 Compositions

A `CompositionStep` declares `parent_key` (the document key that holds the
list), `items` (`max`, and `unique_by`: the item key two items may not
share) and `steps`, the sub-form each item runs as a child session. A child
session sees its parent's answers through the `parent.` scope (section 4).
The manager panel offers Add while the list is under `items.max`, Edit and
Remove per item; a duplicate by `unique_by` finalizes with the `duplicate`
copy instead of writing. `edit_by_item: true` draws one block per item with
an Edit that opens that item directly; the compiler refuses it on a list that
may hold more than `ViewConstants.EDIT_BY_ITEM_MAX` items.

On the manager panel every visible step is a block with an Edit beside what it
opens (`manage.groups`, `panel_groups`): a card lists its fields, a
multi-select its selects, a list its items, and any other step its own answer.
A block of one line shows no heading, and a block titled like the panel shows
none either. A feature that builds its own rows (`Opened.rows`) names each
row's `key`; a keyed row joins the block of the step that produces that key,
and the global Edit only stays while some row names no step. The panel's
intro is the intro step's `manager_description`.

## 4. The `when` grammar

One grammar serves `when` on a step, `visible-when` on a card section, and
`description-when` on a description variant. Evaluation lives in
`app/settings/form/conditions.py`; the compiler checks every rule before the form
exists.

Leaves test one key: `is`, `in`, `not_in`, `matches` (a regular expression),
`present`, `absent`, `count: {min, max}`. Combinators: `all`, `any`, `not`.
Scopes: a bare `key` is an answer of this session; `parent.key` is an
answer of the session that opened this one (repeatable); `item.key` is a
value of the item being edited; `items.count` is how many items the
composition holds.

```yaml
when:
  all:
    - key: mode
      is: block_all
    - not:
        key: allowed_links
        absent: true
```

The compiler refuses a rule whose key is not produced earlier in the same
or an outer scope, a value of `is`/`in`/`not_in` that is not among the
declared options of that key, and a card section that names an unknown
state key. Booleans compare by their lower-cased spelling, so `is: true`
matches an answer stored as `True` or `"true"`.

## 5. Extension points and registries

Everything a definition may name by string is registered in one place;
nothing is looked up by reflection.

| Capability | Registry | Entries today |
|---|---|---|
| Step kinds | `registry()` in `app/settings/form/actions/__init__.py`, one `Kind(render, parse)` per name | the eleven kinds of 3.2 |
| Validators | `VALIDATORS` in `app/settings/form/responses/validations.py`; each declares `needs` (`answers`, `items`, `external:<service>`) so the adapter prefetches before `decide` | `validate_streamer_name`, `validate_youtube_channel`, `validate_link_or_domain`, `validate_date` |
| Transforms | `TRANSFORMS` in `app/settings/form/responses/transforms.py`; a `Transform` names its `part_keys`, `value_key`, `style`, `serialize` and `hydrate` | `mm_dd_date_parts`, `normalize_link` |
| Formatters | `STYLES` and `format_value` in `app/settings/form/responses/styles.py` | the styles of 3.1 |
| Copy tokens | `TOKEN_FORMATTERS` in `app/settings/form/actions/action.py` | `host` |
| Copy resolution | `text(key, locale)` and `normalize_locale` in `app/settings/form/copy.py` | reads the language files through `ml` |
| Definitions | `DefinitionSource` (`load`, `list`) in `app/settings/form/form_yaml.py` | `DiskSource` over `app/languages/form/` |
| Features | `feature_for(key)` in `app/settings/features/__init__.py` | the seven feature modules; any other compiled form gets `GenericCogFeature` |

An extension may: register a step kind, a pure validator with its `needs`,
a transform, a formatter, a card section type; provide a `FeatureModule`.
An extension may not: change the session, call Discord, read another
feature's document, or receive a command key. A validator returns the
error key or `None`; the adapter shows `errors.<key>.message` in the
admin's locale.

## 6. Feature modules

`FeatureModule` (`app/settings/features/protocol.py`) is what a feature
contributes, and nothing more:

| Member | Role |
|---|---|
| `key` | the form key |
| `open(context) -> Opened` | the saved document and panel extras (`rows`, `info`, `extra_buttons`, `enabled`, `previews` or `pending_previews`), or an empty `Opened` for setup; `refusal` names an error key when the feature cannot open |
| `prefetch(lookup, context)` | the external data a submitted modal asks for: `lookup.services` are the `external:<service>` needs of its validator (a text step's or a card section's modal) and `lookup.value` is the typed value after `lowercase` and `normalize`; the adapter defers the interaction before running it |
| `to_document(answers, locale)` / `from_document(document)` | answers to the persisted document and back, any schema version |
| `commit(kind, payload, context) -> CommitResult` | writes what `kind` asks (`setup`, `edit`, `edit_item`, `add_item`, `remove_item`, `pause`, `unpause`, `disable`), records the audit event, and reports `written` and `external` so a failure midway can say what happened |
| `asides()` | read-only side actions by button action name (`AsideAction(handler, defer, own_response, cooldown, confirm)`) |
| `responses_for_preview(answers, locale)` | the answers in the shape the preview functions read |

A feature never sees the session, never renders, and never reaches another
feature's document. `GenericCogFeature` (`app/settings/features/generic.py`)
covers the common case: one document per guild in the feature's collection,
lifecycle through `insert_cog_event`. Write a module only when persistence
is not that (birthdays write members, twitch and youtube subscribe, welcome
messages render banners).

Documents keep the persisted shape `{style, values}` per key that every
reader outside the forms depends on; `app/settings/form/responses/responses.py` is the
one place that shape is built and read.

## 7. The Discord adapter

- **Codec and stale clicks.** Every component id says which session, seen
  at which revision, doing what. A duplicate `event_id` is applied once; a
  click from an older revision gets a self-deleting notice and the current
  screen; a click on an unknown or expired session takes the controls off
  that message and says the form expired, in the admin's locale.
- **One lock per session.** Two clicks on the same session run one after
  the other; an interaction is answered exactly once, and a modal is always
  the first answer of its interaction.
- **Effects.** `Render` edits the message in place, or sends the new screen
  and then deletes the old one when the flavour changes (a Components V2 card
  and an embed cannot share a message) or when a child opens from the
  manager panel. `OpenModal` opens the modal; `ShowError` and `Notice` are
  ephemeral follow-ups; `Confirm` and `Dismiss` handle the discard
  confirmation on a message of its own; `OpenChild`, `ResumeChild` and
  `ResumeParent` move between a parent and its child session; `Commit`
  calls the feature; `Finalize(kind)` closes the message with
  `commands.command-events.<kind>`; `RunAside` runs a help, history,
  preview or feature aside; `Ack` acknowledges and changes nothing.
- **Limits.** The compiler refuses a card that would exceed Discord's forty
  components (`CV2_MAX_COMPONENTS`), and the renderer truncates select
  option text at `DiscordLimits.SELECT_OPTION_TEXT`.
- **Expiry.** The events cog runs `Runtime.sweep` every
  `ViewConstants.FORM_SWEEP_SECONDS`. A session is due when it sat idle for
  `ViewConstants.LONG_TIMEOUT_SECONDS` since its last accepted event and no
  child of it is still in use; it reaches `decide` as `Expired` and its journey
  closes. The message loses its buttons only while the latest interaction token
  is valid (`DiscordLimits.INTERACTION_TOKEN_SECONDS`); after that, the next
  click on the message closes it. Closed sessions past their deadline are
  forgotten.
- **Layout.** `layout.py` holds the Components V2 frame, header, rows and
  footer; the renderer and the `/setup` card (`app/views/setup.py`) both draw
  through it. A screen's own buttons sit below the card, outside its frame;
  a button that belongs to one part of the card (a section, a group's Edit, a
  design, a picker's options) stays inside.
- **Observability.** `observability.emit` is the only place a form's
  product events are emitted, from `Decision.analytics` (see
  `docs/analytics.md`); `log_decision` writes one line per decision and
  `log_effects` one per effect; `error_context` carries the session id,
  revision and definition version; the journey message follows the session.
  Form event traces are quiet: the journey is the only log message a session
  posts.

## 8. Adding a form

A new form is three files, all under existing conventions:

1. **The definition**, `app/languages/form/<key>.yml`: `steps:` composed
   from 3.2, opening with `action: form` and closing with `action: resume`,
   every string in both locales. `pytest tests/forms/form -q`
   compiles every file on disk; a bad rule or a missing locale fails there.
2. **The cog**, in `app/cogs/`: the slash command's body calls
   `open_feature(interaction, key)` (through the feature service's
   `manager`, the convention every feature follows). Register the key in
   `Commands` (`app/constants.py`).
3. **The copy**, in `app/languages/commands/commands.<locale>.yml` (the
   command's name and description) and in `errors.<locale>.yml` for any new
   validator key. Reuse `buttons.*` labels and
   `commands.command-events.*` state messages before adding keys.

Persistence is generic (`GenericCogFeature`) unless the feature needs more;
then a module in `app/settings/features/` fulfils section 6 and `feature_for`
maps the key to it. A behavioral scenario in `tests/behavioral/scenarios/`
and a golden path in `tests/behavioral/golden/paths/` pin what the admin
sees (`docs/form-scenario-testing.md`).

## 9. Decision order for a change

1. **YAML only**: compose the kinds, sections, rules, validators, transforms
   and styles above.
2. **Reuse a capability**: a step kind, validator or feature module already
   supports it, possibly with one more declared field.
3. **A new reusable primitive**: a kind, validator, transform, formatter or
   section type in its registry, generic by construction (no command key,
   no Discord, no session).
4. **An architecture exception**, declared and justified in the plan, never
   silent. The platform has none today; `if key == ...` in `app/settings/` is
   refused by the boundary test.

## 10. Enforcement

- `tests/forms/test_boundary.py`: `discord` only under the adapter; the
  platform imports nothing internal but itself and `app/constants.py`; no
  command key, no blocking call, no reflection outside the adapter, no
  comment longer than one line.
- `tests/forms/test_invariants.py`: I1 to I9 of the review (one session one
  id and a strictly increasing revision; only `decide` changes a session; a
  closed session rejects change; the same event id applies once; an older
  expected revision is rejected; render is a pure function; a feature never
  receives the session; an invalid definition never compiles; every session
  names its definition version; the locale is normalized once).
- `tests/forms/form`, `tests/forms/form`, `tests/forms/features`,
  `tests/forms/discord`: the compiler, `decide`, the rules, the session, the
  feature modules and the adapter choreography.
- `make lint`: `ruff` and `mypy --strict` over `app/settings/` and
  `tests/forms/`; the style rules are `.claude/rules/code-style.md`, rules
  13 onwards.
- `pytest tests/behavioral/golden -q`: the goldens are the UX contract; a
  diff needs an entry in `docs/ux-changes.md` before re-recording.
