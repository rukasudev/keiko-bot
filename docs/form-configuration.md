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
| The engine | `app/settings/form/` | the frozen session and its store (`form_state.py`), events (`events.py`), effects (`effects.py`), the `when` evaluator (`conditions.py`), the screen model (`components.py`), the form: navigation and what each click does in a form, decided by `decide` (`form.py`), and the manager: the panel over a saved document, decided by its own `decide` (`manager.py`), which opens the form as a child session and which the form never imports |
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
   saved document, or nothing. Nothing saved opens a `Setup` session, which
   the form engine decides; a document opens a `Manage` session over the
   manager panel, which the manager decides (`Runtime._decide` routes a
   session by its mode, and nothing else does).
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
   or a card section; `lookups.py` stands apart from `validations.py` because it
   reads the steps from `form.py` and `form_yaml.py`, and both import
   `validations`) defers the interaction and runs the feature's `prefetch`,
   then calls `decide(definition, session, event, context)`, or
   `manager.decide(definition, session, event, context, panel)` for a manage
   session. `Context` (`app/settings/form/actions/action.py`) is the one
   context of both: the adapter fills what it prefetched, the engine fills the
   definition, the steps in play, the locale and the scope.
5. `decide` is pure: it returns a `Decision` with the next session, the
   effects to run in order, the rules it evaluated and the analytics events
   to emit. The adapter stores the session (`put` bumps the revision;
   `remember` records a rejected event without bumping it), emits the
   analytics, logs one line, and the `Executor` runs the effects on Discord.
6. `Commit` hands the feature module the payload of the session (`commit(kind,
   payload, context)`); the outcome comes back to `decide` as
   `CommitSucceeded` or `CommitFailed`, and `Finalize(kind)` closes the
   message with the matching `commands.command-events.<kind>` copy. A
   change saved from the manager panel (an item added, edited or removed, a
   card or a step edited) closes nothing: the adapter reads the feature again
   (`Runtime._reopen`) and the manager draws the panel over what is saved now,
   on the message the admin was using (`Manager.commit_and_return`); the page
   turned to is kept, the screen of an item a search found goes back to the
   panel, and a heading's own screen the admin came from is drawn again. Only
   the setup, Pause, Unpause, Disable and a commit that fails end with
   `Finalize`. Every setup ends with `Finalize("enabled")` and then
   `OpenPanel`: once the summary has become the activation embed, the
   adapter reads the feature again and opens
   a manage session over what was saved, with a message of its own sent right
   below the embed (`Runtime._open_panel`, a new `Surface`), started by
   `FromSetup`, which draws the panel the command opens without reporting an
   opening and counts the setup's save as the session's first change; when the
   feature cannot be read again the activation embed is all the admin gets
   (`tests/behavioral/contracts/test_a_setup_ends_with_the_activation_and_its_panel.py`).

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
| `title`, `description`, `footer` | `Text` | copy in `en-us` and `pt-br`; `description` accepts `{response:key:formatter|fallback}` tokens resolved from earlier answers (inside a card section, from the card's own draft first), where the formatter is `host` or any value style of this table (`{response:default_roles:role|Empty}` reads `<@&1>, <@&2>`), `{plural:key\|one\|several}` tokens, which read the second text only while the answer holds more than one value and the first otherwise, none included, counted from the card's own draft first like `{response:...}` (`{plural:default_roles\|Role to give these members\|Roles to give these members}`), and `{context:name}` tokens filled by the adapter: `server_name`, `prefix` (the chat prefix), `command` (the feature's slash command in the admin's language, without the `/`) and every value the feature's `open` returned in `Opened.values` (`members_count`, `bots_count` for default roles); trailing whitespace is dropped, so a YAML block text adds no blank line |
| `emoji` | `emoji` | the step's icon on summaries; on the manager panel it leads each line the step owns (a card section's or a select's `icon` wins for its keys, the frisbee stands in when nothing is declared) |
| `required` | `required` | the step must hold a value before Confirm; on a composition, the feature needs its list: Remove shows from two items and a removal never takes the last one (`kept_items`), except with `remove_each`, where each item keeps its own Remover and removing the last one turns the feature off after the `only_item_warning` (section 3.4) |
| `hidden` | `hidden` | the answer is a gate: never listed on the review or the panel |
| `in_setup` | `in_setup` | `false` keeps the step out of the setup (and its dry run): the setup walks past it, its intro does not list it, and its review neither lists it nor offers Add for it; the step stays in the definition, so the panel lists and edits it and the saved document keeps its key (the birthday list is filled from the panel after saving) |
| `when` (legacy `condition`) | `when` | the step is shown only while the rule holds (section 4) |
| `description-when` | `description_when` | variants of the description, each with its own `when` |
| `title-when` | `title_when` | variants of the title, each with its own `when`, checked against the answers saved so far (this step's included): the title on the step's screen, its panel and review rows, the heading of a group it does not share, and the edit and remove pickers ("#1") follow the variant in force |
| `style` | `style` | how summaries format the value: `channel`, `role`, `user`, `bullet`, `code-block`, `numbered`, `code`, `boolean`, `boolean-mode`, `mm_dd`, `composition`; several values read as Markdown bullets (`- item`, `` - `item` `` for `code`, `1. item` for `numbered`), except `code-block`, the style of message texts (the Twitch, YouTube and welcome messages), which reads one or several values as `• ` lines inside one code block; a saved value reads in the style the definition declares for its key (a card field, a select, an item's step), the style stored beside it being the fallback, so a document saved under an older style reads as the form says today |

### 3.2 Step kinds

| `action:` in YAML | Kind | Model | What it renders |
|---|---|---|---|
| `form` | `intro` | `IntroStep` | the first screen: what the feature does, then Confirm; `manager_description` is the manager panel's intro, written as what the feature is doing (falls back to `description`) |
| `modal` | `text` | `TextStep` | a modal with one text input (`label`, `placeholder`, `max_length`, `multiline`, `lowercase`, `normalize` (a registered input normalizer such as `handle`: no spaces around, no leading @, lowercase), `validation`, `transform`) or several named `fields`; `lookup_answers` keeps named values of the lookup its modal ran (`answer_key: service.field`) as hidden answers a later description reads with `{response:answer_key\|fallback}`, never listed and never saved; `media_key` names one of them whose picture the review draws under this step's line once the lookup found one (the StreamElements streamer's avatar), the way a card section's `media_key` does, and the compiler refuses a key the step does not look up |
| `file_upload` | `text` with `input: file` | `TextStep` | a modal with a file input |
| `options` | `single_choice` | `SingleChoiceStep` | one button per `option` (`label`, `value`, `style`); `unique`, `styled_values`, `auto_confirm` |
| `design_select` | `single_choice` with `designs` | `SingleChoiceStep` | a gallery, one `Design` (`key`, `label`, `description`) per entry, with the feature's previews |
| `channels` | `channel_pick` | `ChannelPickStep` | a native channel select (`unique`) |
| `roles`, `available_roles` | `role_pick` | `RolePickStep` | a native role select; `available_roles` limits it to roles the bot can assign |
| `user_select` | `user_pick` | `UserPickStep` | a native member select |
| `multi_select` | `multi_pick` | `MultiPickStep` | several native selects on one screen (`selects`: `type` in `channels`/`roles`/`available_roles`, `key`, `style`, `icon`, `label`, `placeholder`) |
| `configuration_card` | `card` | `CardStep` | a Components V2 card whose sections each edit part of the answer (3.3) |
| `composition` | `composition` | `CompositionStep` | a list of items, each built by a child session over `steps` (3.4) |
| `resume` | `review` | `ReviewStep` | the last screen: a card with the manager panel's blocks, each with its Edit (returning to the review), and Preview, Edit, Add, Remove, Confirm and Cancel below; its title is the feature's emoji and name, like the intro, and its description names the command it turns on with `{context:command}`; a list the card draws carries its own Add, Remove and per-item Preview (3.4), so Add and Remove stay below only for a list the card does not draw, and Preview below only while no item previews itself; `preview: true` adds the Preview aside; Confirm, once the setup is saved, replaces the summary by the closed "🎉 Funcionalidade ativada com sucesso!" embed, which stays in the chat as the record of the activation, and sends the feature's manager panel as a new message right below it (the panel the slash command opens), with the same events, story and audit record as a setup that sent no panel (`tests/behavioral/contracts/test_a_setup_ends_with_the_activation_and_its_panel.py`); `lines` (each `emoji`, `label`, `value-key`, an optional `value-format` of 3.1's styles) adds one block of lines read from answers the document never keeps, such as a lookup's counts, drawn after the settings and skipped while their value is empty; `info` (both locales, tokens resolved like `description`) is a note that closes the card, drawn after every block and right above the footer, as loose text with no heading: the panel's closing paragraph (`Panel.info`, the one default roles fills with the roles it can no longer assign), for what the admin should read last, after the settings (the birthdays: where the list is filled once the setup is saved); the card's own picture is always Keiko's, and a step's picture goes under its line (`media_key` of a text step) |

The alias table is `KIND_BY_ACTION` in the compiler. `condition:` still
compiles into `when` with a deprecation warning; new forms write `when`.

### 3.3 Cards

A `CardStep` declares `sections`, each with `key`, `icon`, `label`, a
`state` (the keys the section owns: `value`, `mode`, `title`, `content`,
`url`), an optional `customize_label`, `style`, `picker_title`,
`picker_description`, `description` (a few lines drawn under the section's
heading, in the same text as the heading and the section's lines, with `{response:...}` and `{context:...}` resolved:
what the admin needs while editing that part, such as the variables a messages
section accepts), `media_key` (a state key or a `lookup_answers` key of the
card whose picture the section draws under its lines once it holds one, such as
the streamer's avatar; the compiler refuses a key the card never holds), a
`visible-when` rule, and `setup-only: true` for a section drawn only while the
feature has nothing saved (the setup and the edits from its review), never on
the manager's edits. `required_keys` names the
state keys Done insists on, `required_any` names keys of which Done insists on
at least one (refused with `fill-one-field` under the header), `defaults` seeds them, `header` draws the title
block (`title`, `title_emoji`, `thumbnail`, one of Keiko's icons and never an
answer, `lines` resolved from earlier answers), `fields` declares how summaries show each persisted key (a field's `icon` leads its line where no section owns the key, such as the date a transform writes), `context`
declares template variables (`answer` + `format`, or `context: server_name`)
for `{name}` in previews, read from the card's own draft first, and `transform` names a registered transform.
`preview: true` puts a Preview button first under the card, before Done,
Back and Cancel, as it leads the review's buttons. `preview_needs` (`keys`,
`error`) names the state keys the preview is drawn from: while one of them is
empty (and not hidden), Preview draws nothing and answers with the sentence
of `errors.<error>.message` on a message of its own (`ShowError`, plain, like
Done's "I still need"), and the card stays as it was; the Twitch and YouTube
cards need the streamer and the youtuber
(`tests/behavioral/regressions/test_a_preview_waits_for_the_answer_it_draws.py`).
The compiler refuses it on a card without `preview: true` and a key the card
does not hold. A list item's Preview (`aside:preview:<key>$<index>`) runs over
an item Done already accepted, so it never asks.
Done reads Avançar / Next while another screen follows it (the setup and its
summary, an edit from the summary) and Confirmar / Confirm when it saves a
saved feature with nothing after it (an edit or an item from the panel); the
form tells the card through `Context.done_saves`.
Every section that picks draws its control on the card itself, under its
lines, in place of a button that would open a screen of its own (the section
kind's `controls`): a channel select, a member select, a select of the declared
options (each option's `description` under its label), a select of several
options, the native selects of a `selects` section, a select of the designs,
and two buttons Sim / Não for a yes or no, the current one primary and the other
secondary (Discord's radio group only lives inside a modal). A section's picture is
drawn under its lines, or under its select when the section draws no lines
(the member's picture under the member select). A pick on the card
is a `Drafted` change of that section's key, the way a select reports it, and a
button reports its value as `set:<key>=<value>`; the card is drawn again with
the new draft. A section typed in a modal (`title-content`, `file-upload`,
`modal-input`) keeps the button that opens it, a `list` keeps its Add and
Remove, and a `group` keeps the button that opens its screen. The screen a section's button opened (`press`) stays only for an
`edit_by_field` part edit, which opens that one section from the panel or the
review: the channel select, the role selects, the design gallery with every
banner drawn.

| `type` | Section model | What the admin does |
|---|---|---|
| `title-content` | `TitleContentSection` | keeps the `default` title and body or types both in the `modal` |
| `file-upload` | `FileUploadSection` | keeps the default image or uploads one; without a `mode` in its `state` it only holds the uploaded image, and counts as set once it has one |
| `channel-select` | `ChannelSelectSection` | picks one channel on a native select drawn on the card |
| `user-select` | `UserSelectSection` | picks one member on a native member select drawn on the card, with no line of its own (the select shows the member); `picture: true` draws the member's picture under the select once one is picked, read from the server the session runs in (`Context.member_pictures`, which the adapter fills from the guild's members); a list whose `unique_by` is such a section is keyed by a member, like one whose `unique_by` is a `user_select` step (the birthday's member) |
| `value-select` | `ValueSelectSection` | picks one of the declared `options` on a select drawn on the card (its placeholder is the `customize_label`); `reset_on_change` clears a dependent key when the named validator rejects the new pair |
| `button-options` | `ButtonOptionsSection` | picks one of the declared `options` on a select drawn on the card, each option's `description` under its label (the block links modes); its own screen, only for a part edit, is a row of buttons, and when every option declares a `description` that screen draws a divider under its title and one block per option instead (its label in bold over its description, with a Choose button beside it, primary on the current value, the option's `style` otherwise) and the picker's own description is left out |
| `boolean-toggle` | `BooleanToggleSection` | chooses yes or no on two buttons drawn on the card, Sim / Não, the current one primary and the other secondary |
| `modal-input` | `ModalInputSection` | types in the `modal`: a field with a `key` writes that key, fields without one are joined with `;` into the section's `value` (the shape services split, through `split_messages` in `app/services/utils.py`, which reads a saved list the same way); each field may `normalize`; the modal reopens with the saved values; `validation` runs on submit with the other items and any lookup its validator needs (the birthday's day: `validate_date` refuses 31 in February, and a day typed before any month passes when some month has it); `fills: {into, tokens}` writes the accepted value into another state key of the card: each `{token}` of that text becomes its template with `{value}` replaced (`stream_link: "https://twitch.tv/{value}"`), and when the value changes (in the setup or an item edit) the old renderings are replaced by the new ones, longest first, as whole words and whatever their case; the compiler refuses an `into` the card does not own |
| `design-select` | `DesignSection` | picks one of the declared `designs` on a select drawn on the card (each design's description under its name), the chosen design's preview drawn under its line; the gallery of every design, with the feature's previews, opens only for a part edit |
| `multi-select` | `MultiSelectSection` | picks several of the declared `options` on a select drawn on the card, which can be emptied |
| `selects` | `SelectsSection` | picks channels or roles on one or more native `selects` (the model refuses none; each a `Select` of 3.2's `multi_select`: `type`, `key`, `style`, `icon`, `label`, `placeholder`), every select on one screen; the section owns each select's `key` (no `state` needed), its lines are one `icon **label:** value` per select (the value alone when it holds one), and every select is drawn on the card under them; a select nothing was picked on holds an empty list, and one id is stored as itself, several as a list, like `multi_select`; on the screen of an `edit_by_field` part edit the first pick saves; a select whose key is not one of the card's `required` keys takes no value, so an optional select can be emptied again |
| `list` | `ListSection` | draws a list of the form on the card (`list` names the composition): its heading is the list's own title (with its `title-when`), its items the bullets of their names, with "➕ Adicionar" (off once full) and the list's "🗑️ Remover" beside them (3.4); Add opens the composition's `AddItem` child and the card shows again when it finishes, its draft kept; the setup never walks a list a card draws (`listed_on_cards(steps)` in `form_yaml.py`, the one derivation the setup walk and the intro both read; the compiler refuses a `list` naming no composition), Back to the card keeps its items, and the list's child reads the card's draft through `parent.` (the card's state, defaults included); the review and the panel keep drawing the list as their own block. Declare it `setup-only: true` when an edit of the card must not add to the list |
| `group` | `GroupSection` | names other sections of the same card in `sections`; on the card it draws its heading, its `description`, one line per section it holds that the state shows (`icon **label:** value`, one per select of a `selects` section, a `list` by its title and its items' names, "Em branco" when empty) and its `customize_label` as a button; the button opens a screen of its own on the same message, in place of the card, headed by the `picker_title` (the section's `icon` before it) over the `picker_description`, or the `description` when none is declared, with those sections drawn exactly as on the card (their selects, buttons and lists) and one Back (`picker_back`) below, no Done and no Cancel; every pick there is a draft of the card, saved only by the card's Done, and the screen stays after a pick, after an item added from a list it holds and after a removal (`FormSession.opened_group`, which `opening` sets and `at` keeps only on the same step), and Back draws the card again with the new lines; a visible group takes its sections off the card, so a section held only by groups their rules hide is drawn on the card itself (block links: `exceptions` holds the channels and roles, the popular websites and the custom links in "Bloquear todos", `channel_exceptions` only the channels and roles in "Bloquear alguns", where the blocked links stay on the card); the compiler refuses a key that names no section of the card and a group inside a group; a group owns no state key, so the review and the panel list what its sections hold as before |

A hidden section (`visible-when` false) does not count as required. Card
selections are drafts until Done: the card keeps its values on Back and on
a failed validation. A refused value is told on an ephemeral message of its
own, below the card (`ShowError`), never as a line inside it, so a phone shows
it without scrolling up: a section's validator answers with
`errors.<key>.message` and its modal opens again with what the admin typed;
Done with required fields empty answers with the "I still need" sentence. What
was typed stays until the next change of the session (`FormSession.refused`).
A modal holds at most five inputs.

`edit_by_field: true` on a card or a multi-select gives each of its lines its
own Edit on the panel and the review: the click opens only that section or
select (`Edit.part`), saves the edit as soon as a value is chosen (from the
manager) or returns to the review (in the setup), cancels on Back, and shows
the whole card when the choice leaves it incomplete. It is refused inside a
composition.

### 3.4 Compositions

A `CompositionStep` declares `parent_key` (the document key that holds the
list), `items` and `steps`, the sub-form each item runs as a child session.
`items` holds `max`, `unique_by` (the item key two items may not share, which
is also what an admin knows an item by: `item_name` in `summary.py` reads it,
and an item with no such value reads as its title and number), and
`unique_locked: true` keeps the item an
item is by: in an edit of a saved item, the card section that owns `unique_by`
draws its value as a line (its picture kept) with no select or button, and a
change that still reaches it is ignored, while an addition keeps the select
(the birthday's member; Twitch and YouTube leave it off, so an edit may rename
a streamer or a channel and move its subscription). The compiler refuses a
`unique_by` that no item step produces. A child session sees its parent's
answers through the `parent.` scope (section 4).

A list is drawn inside the card, on the panel and on the review alike
(`list_adder`, `list_bullets` and `list_remover` in `summary.py`, which the card's
`list` section and the panel's `list_head` both use): its title with a grey
"➕ Adicionar" beside it (off once the list holds `items.max`), its items as
bullets of their names, and
"🗑️ Remover" under them while the list holds more than it must keep (one item
for a `required` list, none otherwise). The bottom Add and Remove only stay for
a list the card does not draw. Remove opens a select of the items' names that
takes several at once (up to what the list may lose), a confirmation names
them (`removal_screen`; the chosen positions wait in `FormSession.chosen`),
and Remove there takes them all in one commit. A list keyed by a member
(`unique_by` is picked on a member select: a `user_select` step or a card's
`user-select` section, `picks_a_member` in `form_yaml.py`) asks with a member
select instead, which also takes several. A duplicate by `unique_by` writes nothing: the screen the
admin added from is drawn again and `errors.item-already-registered` is told
once, on a message of its own, so the admin can add another item from there.

`edit_by_item: true` draws one block per item with Edit, and Preview beside
it when the item's card declares `preview: true` (the aside runs over that one
item, `aside:preview:<key>$<index>`), between the list's title and its
Remove; the compiler refuses it on a list that may hold more than
`ViewConstants.EDIT_BY_ITEM_MAX` items. `search: true` on a list keyed by a
member puts "🔎 Pesquisar" on the panel's last row: it asks for a member on a
select that answers as soon as one is picked (no Confirm), and is left out
while the list holds no item, the way Edit is over an empty list; it shows that
member's item on a screen of its own: its `found_title` (`$item` the item's
name, "🎉 Configurações de aniversário de <@777>"; the list's emoji and the name
when none is declared) with no picture beside it, a separator, the item's lines without the one of its
name, which the title already says, then Edit, Remove and Back (Remove
asks on the confirmation of the per-item Remove below, `remove_this`, and its
Back returns to the member); a feature
adds to that screen through `PanelExtras.found`, a `FoundItem(lines)` per
item keyed by its `unique_by` value: the lines drawn under the item's own (the
birthday messages); the member's picture is drawn big under them, read like
every member picture from `Context.member_pictures` (the adapter's
`MemberPictures`, the server's own member cache), never by the feature. Such
a list keeps no Remove below the panel. The compiler refuses `search` on any
other list.

`remove_each: true` on `items`, drawn only on the manager panel (the setup's
review keeps Edit, Preview and the list's own Remove) and refused by the
compiler without `edit_by_item`, puts a red "🗑️ Remover" beside each item
(`remove_this:<key>$<index>`, `ItemRemovalRequested`) and drops the list's
Remove picker: the confirmation names the item (`confirm_removal`, the
removal screen of the picker). On the only item of a `required` list it asks
to wait first ("⚠️ Ei, espere! Leia abaixo primeiro", `buttons.remove.last`)
and reads the composition's `only_item_warning`, its `$item` replaced by the
item's block as the panel draws it (`item_heading` and its lines) and its
`$feature` by the intro's title without its emoji; the compiler refuses a
`required` list with `remove_each` and no `only_item_warning`. Confirming that
one runs `disable` naming the `items` the panel showed, which drops nothing
when the saved list differs. `title_count` on a composition (a text with
`$count` and `$max`) is what the list's title line reads after its title on
the panel and on the review, in place of the empty value,
"👥 **Notificações:** 2 de 3 configurados": the items it holds of its `max`. An item's bookkeeping keys (`enabled` among them, which a per-item
pause in dev once wrote) are never a line or an answer of the item, the way a
document's are not.

On the manager panel every visible step is a block with an Edit beside what it
opens (`panel_rows` and `panel_groups` in `app/settings/form/responses/summary.py`,
which the review draws too): a card lists its fields, a
multi-select its selects, a list its items, and any other step its own answer.
A block of one line shows no heading, and a block titled like the panel shows
none either. A feature that builds its own rows (`PanelExtras.rows`) names each
row's `key`; a keyed row joins the block of the step that produces that key,
and the global Edit only stays while some row names no step. Rows about a list
saved with no item keep no Edit, since there is nothing it could open. A
setting that holds several values (a multi-select) reads as its bullets under
its title, with its Edit beside the title. The panel's intro is the intro
step's `manager_description`.

`panel_group` on a step or a card field puts its rows under a heading shared
with other steps: `key`, `title` (with `title_when` and `description` /
`description_when` variants), `emoji`, `order`, and `own_screen: true` for a
heading whose rows open on a screen of their own behind one Edit, where a
multi-select's options turn on and off as buttons, a list draws one block per
item (`item_groups` in `summary.py`, the panel's own, with no heading per item
and the first `ViewConstants.GROUP_ITEMS_SHOWN` items), and the rows that open
the same step share one Edit.
`entry: footer` with a `label` takes such a heading off the panel and opens
it from a button of that label on the panel's last row (the compiler refuses a
footer entry without `own_screen` or `label`). A feature may also hand the
panel `PanelExtras.pages`, each a `PanelPage(title, lines, description)`: the
panel draws the first one under its blocks, its title with the description
under it and, when the form has a list, the list's "➕ Adicionar" beside the
title (off once full), so the panel's bottom row keeps no Add; a page with no
lines still shows its title, its description and its Add (the birthday list
with no birthday). It turns through them with ◀ and ▶ (`page:<n>`), keeping the
page on a redraw.

## 4. The `when` grammar

One grammar serves `when` on a step, `visible-when` on a card section, and
`description-when` and `title-when` on a variant. Of a list of variants the
first whose rule holds wins, the default text otherwise (`chosen_text`, beside
`evaluate`), for a step's title and description and a panel group's alike. Evaluation lives in
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
| Card section types | `SECTION_KINDS` in `app/settings/form/actions/configuration_card.py`, one `SectionKind(press, change, preview, reset, custom, controls)` per YAML `type` (`controls` are the selects or buttons the section draws on the card; a kind without them draws the button whose `press` opens its modal); what a section declares (`choices`, `holds_several`, `adds_to`, `opened_modal`, `validators`, `offered_designs`, `drawn_over`, `looked_up`, `filled`, `picks`, `controls_drawn`, `pictured`, `picks_members`, `holds`) is asked of the section model, never by `isinstance` | the thirteen types of 3.3 |
| Validators | `VALIDATORS` in `app/settings/form/responses/validations.py`; each declares `needs` (`answers`, `items`, `external:<service>`) so the adapter prefetches before `decide` | `validate_streamer_name`, `validate_youtube_channel`, `validate_link_or_domain`, `validate_date` |
| Transforms | `TRANSFORMS` in `app/settings/form/responses/transforms.py`; a `Transform` names its `part_keys`, `value_key`, `style`, `serialize` and `hydrate` | `mm_dd_date_parts`, `normalize_link` |
| Formatters | `STYLES` and `format_value` in `app/settings/form/responses/styles.py` | the styles of 3.1 |
| Copy tokens | `TOKEN_FORMATTERS` in `app/settings/form/actions/action.py`, then every value style; `PLURAL_TOKEN` there picks one of its two texts by `count_of` (`conditions.py`, the count the `when` grammar's `count` leaf reads); the `{context:name}` values are fields of `Context` and its `values`, filled in `Runtime._context` (`callbacks.py`) from `Opened.values` | `host` and the styles, `plural`; context `server_name`, `prefix`, `command` and the feature's own values |
| Copy resolution | `text(key, locale)` and `normalize_locale` in `app/settings/form/copy.py` | reads the language files through `ml` |
| Definitions | `DefinitionSource` (`load`, `list`) in `app/settings/form/form_yaml.py` | `DiskSource` over `app/languages/form/` |
| Features | `Feature` in `app/constants.py`, one `FeatureSpec` each, whose `module` names the feature's module under `app/settings/features/` or is None; `feature_for(key)` in `app/settings/features/__init__.py` imports the module the spec names | the seven feature modules; a spec whose `module` is None gets `GenericCogFeature`, and a key no spec declares is refused |

An extension may: register a step kind, a pure validator with its `needs`,
a transform, a formatter, a card section type; provide a `FeatureModule`.
An extension may not: change the session, call Discord, read another
feature's document, or receive a command key. A validator returns the
error key or `None`; the step turns it into a `Refusal` that names its
`target` (the step key, or `section:<n>` of a card), the `inputs` typed and,
for a card confirmed incomplete, the `missing` keys. `Form.refuse` draws the
screen that asked again and then answers with `errors.<key>.message` in the
admin's locale on an ephemeral message of its own (`ShowError`), the last
thing the admin sees: a card section's modal reopens with what was typed, and
a modal step (Discord takes no second modal after a modal submit) becomes a
one-field card with the typed value and an Edit that reopens the modal
prefilled (`modal.refused_screen`). A refusal without a target (a picker
confirmed empty) only sends the message.
`setup.required_missing` is emitted only for missing fields, every other
refusal emits `setup.validation_failed`.

## 6. Feature modules

`FeatureModule` (`app/settings/features/feature.py`) is what a feature
contributes, and nothing more:

| Member | Role |
|---|---|
| `key` | the form key |
| `open(context) -> Opened` | the saved document and its `PanelExtras` (`rows`, `pages`, `found`, `info`, `info_title`, `info_action` (one button drawn beside the info block, inside the card, such as StreamElements' command list), `extra_buttons` (the panel's last row), `enabled`), `previews` or `pending_previews`, or an empty `Opened` for setup; `refusal` names an error key when the feature cannot open; `values` are the `{context:name}` tokens its copy reads (default roles: the server's members and bots, which the setup's own step asking whether to give the roles to who is already there shows; a `hidden` options step after the card, so the summary and the panel never list it) |
| `prefetch(lookup, context)` | the external data a submitted modal asks for: `lookup.services` are the `external:<service>` needs of its validator (a text step's or a card section's modal) and `lookup.value` is the typed value after `lowercase` and `normalize`; the adapter defers the interaction before running it |
| `to_document(answers, locale)` / `from_document(document)` | answers to the persisted document and back, any schema version |
| `commit(kind, payload, context) -> CommitResult` | writes what `kind` asks (`setup`, `edit`, `toggle`, `edit_item`, `add_item`, `remove_item`, `pause`, `unpause`, `disable`), records the audit event, and reports `written` and `external` so a failure midway can say what happened. The list and choice commits apply each change to the settings as they are saved when the admin confirms (`saved_document`, `saved_list`, read through the feature's `normalized`): `toggle` sets one option to the state its button asked for (`turned_on`), so the same click from two screens gives the same choice, and the item commits find the edited or removed items by the content the panel showed (a removal names `indexes` and the `items` they held, and takes every one of them or none, never leaving a `required` list empty), refuse a `unique_by` value already listed (`DuplicateItem`), and write only while nobody changed the value meanwhile (`update_cog_if_unchanged_async`); otherwise `ListChanged` saves nothing. `pause`, `unpause` and `edit` first check that the document is still saved (`saved_document`). A refusal drops the cached document, the adapter reads the feature again (`Runtime._reopen`, which keeps the document it had and logs the error when that read fails) and the manager answers: the out-of-date notice and the screen redrawn from the saved list, the duplicate copy once over the screen drawn again, or, once the feature is gone, `Finalize("closed")`, which takes the rows of buttons off the message and writes the closed notice inside it. A refusal keeps the session open (a duplicate included), or closes it after another panel's Disable, and emits no event: only a commit that fails the session emits `feature.commit_failed`. What a change needs outside runs before the write (`before_item_added`, `before_item_replaced`) and is let go when the write fails (`save_or_let_go`); what it leaves outside runs after it (`after_item_replaced`, `after_item_removed`, `after_disable`), best-effort and logged when it fails (`best_effort`); what may take long (default roles giving the saved roles to every member already in the server) starts after the commit through `in_background`, best-effort too, and the commit never waits for it, so the answer to the click stays in time. `CommitContext.guild` is the guild the session runs in. Disable deletes the document and lets go of what it listed before it records the flag; a `disable` that names `items` (the removal of a list's only item) first checks the list is saved as them |
| `asides()` | read-only side actions by button action name (`AsideAction(handler, defer, own_response, cooldown, confirm)`); a button may name one item of the list (`aside:<name>:<key>$<index>`), and that item cools down on its own |
| `responses_for_preview(answers, locale)` | the answers in the shape the preview functions read |
| `responses_for_aside(answers, locale, index)` | the same rows with the list replaced by the item at `index` (the first when none is named), from the answers on a form and from the saved document on the panel |

A feature never sees the session, never renders, and never reaches another
feature's document. `GenericCogFeature` (`app/settings/features/feature.py`)
covers the common case: one document per guild in the feature's collection,
lifecycle through `insert_cog_event`. Write a module only when persistence
is not that (birthdays write members, twitch and youtube subscribe, welcome
messages render banners).

Documents keep the persisted shape `{style, values}` per key that every
reader outside the forms depends on; `app/settings/form/responses/responses.py` is the
one place that shape is built and read. A runtime reader reads it through
the read model there: `document_values` for the settings and `list_values`
(each item through `item_values`) for a list give machine values, the
`_raw_value` beside a label and never the label; saved items keep their
labels in `value`, because v0.9.0 shows and matches them.

## 7. The Discord adapter

- **Codec and stale clicks.** Every component id says which session, seen
  at which revision, doing what. A duplicate `event_id` is applied once; a
  click from an older revision gets the current screen drawn again and no
  notice (`Engine.redraw`); a click on an unknown or expired session takes
  the controls off that message and says the form expired, in the admin's
  locale.
- **One lock per session, one first answer per interaction.** Two clicks on
  the same session run one after the other. Every first answer of an
  interaction (a defer, an edit, a message, a modal) goes through its
  `Answering` (`transitions.py`), whose lock the late-answer clock shares:
  discord.py reports an interaction answered only once the round trip
  returns, so the clock never sends a second first answer (40060). A modal is
  always the first answer of its interaction; an aside that answers by itself
  (`own_response`, the history) stops the clock first.
- **Held while loading.** Where the adapter knowingly waits before it can
  draw (a lookup a modal needs, the previews a screen shows, a design drawn
  again), the first answer disables every control of the message the click
  came from (`transitions.hold`); the next screen replaces it, and when
  nothing is drawn over it the controls come back (`release`). A click from
  another message is deferred.
- **Effects.** `Render` edits the message in place, or sends the new screen
  and then deletes the old one when the flavour changes (a Components V2 card
  and an embed cannot share a message) or when a child opens from the
  manager panel. `OpenModal` opens the modal; `ShowError` (a picker
  confirmed empty, a commit that failed) and `Notice` are ephemeral
  follow-ups; `Confirm` and `Dismiss` handle the discard
  confirmation on a message of its own; `OpenChild`, `ResumeChild` and
  `ResumeParent` move between a parent and its child session; `Commit`
  calls the feature; `Finalize(kind)` closes the message with
  `commands.command-events.<kind>`; `OpenPanel` sends the manager panel of a
  saved setup as a new message below its activation embed; `RunAside` runs a help, history,
  preview or feature aside; `Ack` acknowledges and changes nothing.
- **Limits.** The compiler refuses a card that would exceed Discord's forty
  components (`CV2_MAX_COMPONENTS`), counted by `card_components`: the frame
  (the header with each of its `lines`), each section's one text (its heading,
  its description and its lines, joined) and divider, its controls with their rows
  (`controls_drawn`: a select and its row, two buttons and their row for a yes or no, each select of a
  `selects` section, Edit and Reset for a section that keeps a default), one
  more for a picture (`pictured`), and the buttons below with their rows
  (Preview when declared, Back only on a card some step other than the intro
  comes before, `cards_with_back`). A `group` counts as itself or as the
  sections it holds, whichever is more, since they are drawn on the card while
  its rule hides it, and its own screen is counted apart (`group_components`:
  the frame, its sections and Back). The largest card today is the birthday
  member's card at 35; block links with twenty-five links and every exception
  picked takes 22 in "Bloquear todos" and 27 in "Bloquear alguns", its
  exceptions screen 22 and 13
  (`test_every_card_with_its_controls_at_its_largest_fits_discord`,
  `test_the_block_links_card_and_its_exceptions_screen_fit_in_each_mode`). The renderer
  truncates select option text at `DiscordLimits.SELECT_OPTION_TEXT`.
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

A new feature is one declaration and three files, all under existing
conventions:

0. **The declaration**, one member of `Feature` in `app/constants.py`, the
   one table every feature list derives from, except `COMMANDS_LIST`, which
   wave 3 derives: a `FeatureSpec` with the key, the slash command's group
   and namespace, the /setup button and emoji, the permissions it needs,
   whether it assigns roles, whether it answers the chat prefix, and the
   module that saves it (or None for the generic one). The key constant,
   `Commands.SETUP_FEATURES`, `Commands.FEATURE_COMMANDS`, the moderation
   defaults, `feature_keys` and the module `feature_for` imports come from
   it. It is one table because those lists had drifted apart: the
   moderation defaults had forgotten StreamElements, and group and
   namespace were written twice for every command.
   `tests/forms/features/test_features.py` refuses a declaration whose
   form, module, command or copy disagrees with it.
1. **The definition**, `app/languages/form/<key>.yml`: `steps:` composed
   from 3.2, opening with `action: form` and closing with `action: resume`,
   every string in both locales. `pytest tests/forms/form -q`
   compiles every file on disk; a bad rule or a missing locale fails there.
2. **The cog**, in `app/cogs/`: the slash command's body calls
   `open_feature(interaction, key)` (through the feature service's
   `manager`, the convention every feature follows), in a subgroup with the
   namespace and under the group the declaration names.
3. **The copy**, in `app/languages/commands/commands.<locale>.yml` (the
   command's name and description) and in `errors.<locale>.yml` for any new
   validator key. Reuse `buttons.*` labels and
   `commands.command-events.*` state messages before adding keys.

Persistence is generic (`GenericCogFeature`, the spec's `module=None`) unless
the feature needs more; then a module under `app/settings/features/`, named
after the key like the form's file, fulfils section 6 with a `FEATURE`, and
the spec names it. A behavioral scenario in `tests/behavioral/scenarios/`
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
  comment longer than one line; and the services layer never redefines a
  public name the `responses` package owns (names only, not bodies).
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
  `tests/forms/`, plus `ruff check --select F401,F821` over the whole of
  `app/` (unused imports and undefined names), which is the only static check
  that reaches `app/cogs`, `app/webhooks` and `app/api`; the style rules are
  `.claude/rules/code-style.md`, rules 13 onwards.
- `pytest tests/behavioral/golden -q`: the goldens are the UX contract; a
  diff needs an entry in `docs/ux-changes.md` before re-recording.
