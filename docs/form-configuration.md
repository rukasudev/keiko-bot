# Keiko form configuration reference

The map of the form engine (`app/forms/`): what a form declaration may say,
how the engine turns it into screens, where each extension point lives, and
what it takes to add a form. Read it before touching a YAML form, a step
type, a feature module or the Discord adapter. The contract it documents is
`docs/form-platform-architecture-review.md` (Part II prevails); the UX the
engine must reproduce is pinned by the golden transcripts
(`docs/form-scenario-testing.md`).

Core principle, unchanged:

> **Python implements reusable capabilities. YAML combines those capabilities
> to create commands, forms, and interaction flows.**

## 1. Overview

A form is a short, guided session of one admin. The forms themselves live
with their commands: each command with a form keeps it as the `form.yml` in its
folder (`app/commands/<command>/`), beside its copy in `copy.yml`, with its `FEATURE`
at the end of its `service.py` when it needs a feature module of its own, declared
in its `Command` (`app/core/commands.py`); the registry finds the `form.yml` beside
the package (`form_file(key)`), and the engine reaches both only through it. The two
files are the two halves of the document a command made from data will store
(`{copy, form}`), so the two share one format and one validator.
The engine splits a session in layers with a fixed dependency direction
(features depend on the engine; the adapter depends on the engine and the
features; the engine depends on nothing internal but itself, `app/constants.py`,
the pure calendar `app/core/calendar.py`, the pure registry `app/core/commands.py`,
the `when` grammar and its evaluator `app/core/conditions.py`, the facts and the
context `app/core/facts.py`, and the copy loader `app/core/i18n.py`, through which it
reads a shared block's overrides (`block_texts` in `app/forms/form/copy.py`; the
removal question)):

| Layer | Package | Owns |
|---|---|---|
| What a form declares | `app/forms/form/form_yaml.py`, `app/forms/vocabulary.yml` | the typed schema the YAML is written in, the compiler, the registry and the source of definitions; the shared vocabulary every form reads its fallbacks and its `${ref.<name>}` texts from (3.5) |
| The engine | `app/forms/form/` | the frozen session and its store (`form_state.py`), events (`events.py`), effects (`effects.py`), the screen model (`components.py`), the form: navigation and what each click does in a form, decided by `decide` (`form.py`), and the manager: the panel over a saved document, decided by its own `decide` (`manager.py`), which opens the form as a child session and which the form never imports |
| Actions and responses | `app/forms/form/actions/`, `app/forms/form/responses/` | one render and parse pair per step `type`; a response saved, validated, transformed, styled and summarised |
| Features | `app/forms/features/`, and the `FEATURE` at the end of a command's `service.py` | the `FeatureModule` contract, `GenericCogFeature`, `feature_for` and the subscription base Twitch and YouTube share; a command's own module turns answers into its document and back, commits, and adds side actions |
| Discord | `app/forms/discord/` | where sessions open and every click lands (`callbacks.py`), the description turned into Discord (`views.py`), the message replaced (`transitions.py`), click to event and the `custom_id` codec (`interactions.py`), observability (`observability.py`) |

Only the adapter imports `discord`. Nothing in `app/forms/` names a command
key, blocks the event loop, or reaches another feature's document; those
lines are enforced by `tests/forms/test_boundary.py` and, for the key literals,
`tests/test_structure.py`.

## 2. Load and execution path

1. A command's slash command (`app/commands/<command>/cog.py`) calls
   `open_feature(interaction, key)`
   (`app/forms/discord/callbacks.py`). The `/setup` buttons reach
   the same function through `run_feature_command`, beside it
   (`app.forms`), each caller passing its source.
2. `Runtime.open_feature` asks the feature module (`feature_for(key)`,
   `app/forms/features/__init__.py`, which reads the `feature` of
   `command_for(key)` in the registry) to `open` the guild: it answers with the
   saved document, or nothing. Nothing saved opens a `Setup` session, which
   the form engine decides; a document opens a `Manage` session over the
   manager panel, which the manager decides (`Runtime._decide` routes a
   session by its mode, and nothing else does).
3. The definition comes from the definition registry (`registry.get(key)`),
   which compiles the command's `app/commands/<command>/form.yml` once per
   process through
   `compile_form` (`app/forms/form/form_yaml.py`). A definition that
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
   session. `Context` (`app/forms/form/actions/action.py`) is the one
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
   message with the matching `commands.responses.<kind>` copy. A
   change saved from the manager panel (an item added, edited or removed, a
   card or a step edited) closes nothing: the adapter reads the feature again
   (`Runtime._reopen`) and the manager draws the panel over what is saved now,
   on the message the admin was using (`Manager.commit_and_return`); the page
   turned to is kept, the screen of an item a search found goes back to the
   panel, and a heading's own screen the admin came from is drawn again.
   Pause and Unpause save the same way, in one click with no question: the
   panel is drawn again with the feature's new state (its title's "(Pausado)",
   its Despausar), and `Announce(kind)` sends `commands.responses.paused` or
   `unpaused` beside it on an ephemeral message that removes itself after
   `ViewConstants.ACTION_NOTICE_SECONDS`. The panel's grey Desativar, beside
   Pause, asks first on a red question (`delete_screen` in `form.py`: the
   command by name, that everything set up is deleted, the Pause it could take
   instead while the feature is on; Desativar e apagar,
   `confirm_lifecycle:disable`, then Voltar, which draws the panel again). Only
   the setup, that delete and a commit that fails end with `Finalize`. Every setup ends with `Finalize("enabled")` and then
   `OpenPanel`: once the summary has become the activation embed, the
   adapter reads the feature again and opens
   a manage session over what was saved, with a message of its own sent right
   below the embed (`Runtime._open_panel`, a new `Surface`), started by
   `FromSetup`, which draws the panel the command opens without reporting an
   opening and counts the setup's save as the session's first change; when the
   feature cannot be read again the activation embed is all the admin gets
   (`tests/behavioral/contracts/test_a_setup_ends_with_the_activation_and_its_panel.py`).
7. The caller of `open_feature(interaction, key, source=None, ending=None)`
   (also `run_feature_command(interaction, command_key, source, ending=)`)
   may hand an `ending` with the open, and the platform never learns who it
   is: no source string, command or screen is named under `app/forms/`
   (`test_the_platform_never_names_the_screen_that_opened_it`).
   `ending: Ending` (`Callable[[], Awaitable[discord.ui.LayoutView]]`,
   `app/forms/discord/transitions.py`) is kept on the adapter `Session`.
   A setup opened with one starts on the message the click came from (its
   `Surface` is that message, so the first screen edits it or replaces it
   when the flavour changes), and once saved the `Executor` drops the
   `Finalize` that comes with `OpenPanel` and draws the view the ending
   returns on the setup's message instead (`Executor.hand_back`): no
   activation embed, no panel below. Every event, the story and the audit
   record are the ones a setup without an ending writes. A manager opened
   with an ending ignores it. `/configurar` passes one from its feature
   buttons, to end on the category page the feature was opened from
   (`tests/behavioral/contracts/test_an_opener_can_end_a_setup.py`). Pause,
   unpause and disable run only from the panel.
8. The same caller may hand a `back` (`open_feature(..., back=None)`, also
   `run_feature_command(..., back=)`), of the same `Ending` type, kept on the
   adapter `Session` beside the ending and ignored by a manager. A setup
   opened with one also starts on the message the click came from, and the
   adapter tells the engine so (`Context.back_to_opener`); the engine then
   draws the first screen of the setup (`Form.returns_to_opener`: the first
   allowed step) with a grey Back where its Cancel was (`Form.framed`, on
   every screen `show` and `rerender` draw for that step), and that Back,
   which has no previous step to land on, closes the session as a discard:
   `setup.discarded`, `Status.CANCELLED`, the discarded story, no keep or
   discard question, and `ReturnToOpener` in place of `Finalize("discarded")`,
   which the `Executor` runs by drawing the view `back` returns on the
   setup's message (`Executor.return_to_opener`, through `hand_back`). Every
   later screen keeps its own Back and Cancel, and so does a child session.
   `/configurar` passes one from its feature buttons, back to the category
   page and the page number the admin came from
   (`tests/behavioral/contracts/test_an_opener_can_end_a_setup.py`).

## 3. The definition schema

`app/forms/form/form_yaml.py` is the whole vocabulary, and the YAML is
written in it: pydantic models that refuse an unknown field at every level
(`extra="forbid"`), carry both locales for every piece of copy, and are frozen
once built. What an author writes is the model's field name, in `snake_case`:
there is no alias layer, no legacy spelling and no node rebuilt from the keys
it knows. The compiler writes out the shared texts and fallbacks (3.5) and
validates the result, so the names below are both the YAML and the model.
Every field carries a `description` (what it makes the bot do), and a field
only one form writes says which case it serves; a field lives only on the
step types and section types that read it. A mapping whose keys the author
chooses (a card's `defaults`, a preview's `tokens`, `lookup_answers`, a
validator's `params`, a fill's `tokens`, the root `groups`, the vocabulary's
`texts`) is typed `Open[T]` (`app/core/commands.py`), `T` being what each
child must be; in a file it comes after the closed fields of its block,
preceded by one `# open: <keys> -> <child>` line, the text the field declares
in its schema (`json_schema_extra["open"]`). Every key of a `form.yml` and of the
vocabulary, with its type, default, description and an example from the real
files, is in the generated reference `docs/yaml-reference.html` (`make docs`
after a change; code-style rule 27).

The root of a form.yml is closed: `version` (the definition version a
session is pinned to, `1` today), `steps`, and `groups` (3.4). The form's
key is its command's folder, never a field.

### 3.1 Common to every step

| Field | Meaning |
|---|---|
| `type` | the step type (3.2), the discriminator of the step: each type declares only the fields it reads |
| `key` | the answer key this step produces |
| `title`, `description`, `footer` | copy in `en-us` and `pt-br`, or a `${ref.<name>}` of the shared vocabulary (3.5); a step without `footer` reads the bug-report line and `footer: null` draws none, and a review without `title` or `description` and a top-level card without `title` read theirs from the fallbacks (3.5); `description` accepts `${answer.<key>:<format>\|<fallback>}` tokens (the format and the fallback each optional) resolved from the answers of an earlier step or of the step itself (inside a card section, from the card's own draft first; the compiler refuses a key no such step produces, the way it refuses a `when` on one, and a format it does not know), where the format is `host` or any value format of this section, by its `snake_case` name (`${answer.default_roles:role\|Empty}` reads `<@&1>, <@&2>`, `${answer.date:month_day}` reads `May 12`), `${plural.<key>\|<one>\|<many>}` tokens, which read the second text only while the answer holds more than one value and the first otherwise, none included, counted from the card's own draft first like `${answer...}` (`${plural.default_roles\|Role to give these members\|Roles to give these members}`), and `${context.<name>}` tokens, each an entry the `context:` of `app/core/global.yml` declares (the compiler refuses any other, naming the form, the step, the text and the closest entry): `${context.commands.<key>}` (a command's slash command, `/setup` or `/configurar`) and `${context.groups.<group>}` (a parent group's, `/moderations`) are written in at load, in each locale, and the adapter fills the rest: `server_name`, `prefix` (the server's chat prefix), `server_language` (`pt-br` or `en-us`), `server_timezone` (an IANA zone), all three read once per opened feature from the server's `settings`, `command` (the feature's slash command in the server's language, without the `/`) and every value the feature's `open` returned in `Opened.values` (`members_count`, `bots_count` for default roles); trailing whitespace is dropped, so a YAML block text adds no blank line |
| `emoji` | the step's emoji: it leads the step's title, and on summaries and the manager panel each line the step owns (a card section's or a select's `emoji` wins for its keys, the frisbee stands in when nothing is declared); on a card it also leads the header's title and the card's group |

Every step between the intro and the review (`ContentStep`: text,
single_choice, card, list) also declares:

| Field | Meaning |
|---|---|
| `hidden` | the answer is a gate: never listed on the review or the panel |
| `appears_in` | `everywhere` (the default) or `panel`: `panel` keeps the step out of the setup (and its dry run): the setup walks past it, its intro does not list it, and its review neither lists it nor offers Add for it; the step stays in the definition, so the panel lists and edits it and the saved document keeps its key (the birthday list is filled from the panel after saving); `setup` is refused at compile, since the panel edits every step |
| `when` | the step is shown only while the rule holds (section 4) |
| `overrides` | texts that replace the step's own while their rule holds, each `{when, title, description}` writing one or both (an item that writes neither is refused); the first item whose rule holds wins and replaces only the texts it writes, the step's own stay for the rest (`overridden_text` in `form_yaml.py`, over `winner` in `app/core/conditions.py`), the same rule a copy block's `overrides` follow. A description is chosen before the step is answered, so the compiler checks the rule of an item that writes one, and of every item before it, against the answers before the step; an item after the last one that writes a description is checked against the answers saved so far (this step's included): the title on the step's screen, its panel and review rows, the heading of a group it does not share, and the edit and remove pickers ("#1") follow the item in force |

A value format (a text or single-choice step's `format`, a card field's, a
modal_input section's, a preview token's `format`) is one of `channel`,
`role`, `user`, `bullet`, `code_block`, `numbered`, `code`, `boolean`,
`customized`, `month_day`, `time_span` (a stored span such as `P5M` read as
"5 meses" or "1 week", `stored_span_label` in `app/core/calendar.py`); several
values read as Markdown bullets (`- item`,
`` - `item` `` for `code`, `1. item` for `numbered`), except `code_block`, the
format of message texts (the Twitch, YouTube and welcome messages), which reads
one or several values as `• ` lines inside one code block; a list reads in the
`composition` style its type gives it. A saved document keeps the style
spellings released versions wrote in its `{style, values}` envelopes:
`code_block` is saved as `code-block`, `month_day` as `mm_dd` and `customized`
as `boolean-mode` (`stored_style` in `app/forms/form/responses/styles.py`), and
a list as `composition`. A saved value reads in the format the definition
declares for its key (a card field, a select, an item's step), the style stored
beside it being the fallback, so a document saved under an older style reads as
the form says today.

### 3.2 Step types

| `type` | Model | What it renders |
|---|---|---|
| `intro` | `IntroStep` | the first screen: what the feature does, then Confirm; `panel: {description}` is the manager panel's intro, written as what the feature is doing (falls back to `description`) |
| `text` | `TextStep` | a modal with one text input, always required (`label`, the title when left out, `placeholder`, `max_length`, `normalize: {name}`: a registered input normalizer such as `handle`, no spaces around, no leading @, lower case, or `lowercase`; `validation`, `transform`, `format`); `lookup_answers` keeps named values of the lookup its modal ran (`answer_key: service.field`) as hidden answers a later description reads with `${answer.answer_key\|fallback}`, never listed and never saved; `image` names one of them whose picture the review draws under this step's line once the lookup found one (the StreamElements streamer's avatar), the way a card section's `image` does, and the compiler refuses a key the step does not look up |
| `single_choice` | `SingleChoiceStep` | one button per `options` entry (`label`, `value`, `style` (the button's colour), `description`); `required` refuses Confirm with nothing chosen, `auto_confirm` answers at once; a press answers the option's value, typed as the option declares it; summaries show the chosen option's label unless the step declares a `format` |
| `card` | `CardStep` | a Components V2 card whose sections each edit part of the answer (3.3) |
| `list` | `ListStep` | a list of items, each built by a child session over `steps` (3.4) |
| `review` | `ReviewStep` | the last screen: a card with the manager panel's blocks, each with its Edit (returning to the review), and Preview, Edit, Add, Remove, Confirm and Cancel below; its `title` is the intro's (the feature's emoji and name) and its `description` the summary that names the command it turns on with `${context.command}` (`fallbacks.review_description`), both optional and filled by the defaults of 3.5, so a review writes them only to say something else (StreamElements explains its prefix); a list the card draws carries its own Add, Remove and per-item Preview (3.4), so Add and Remove stay below only for a list the card does not draw, and Preview below only while no item previews itself; `preview: true` adds the Preview aside; Confirm, once the setup is saved, replaces the summary by the closed "🎉 Funcionalidade ativada com sucesso!" embed, which stays in the chat as the record of the activation, and sends the feature's manager panel as a new message right below it (the panel the slash command opens), with the same events, story and audit record as a setup that sent no panel (`tests/behavioral/contracts/test_a_setup_ends_with_the_activation_and_its_panel.py`); `lines` (each `emoji`, `label`, `answer`) adds one block of lines read from answers the document never keeps, such as a lookup's counts, drawn after the settings and skipped while their value is empty; `info` (both locales, tokens resolved like `description`) is a note that closes the card, drawn after every block and right above the footer, as loose text with no heading: the panel's closing paragraph (`Panel.info`, the one default roles fills with the roles it can no longer assign), for what the admin should read last, after the settings (the birthdays: where the list is filled once the setup is saved); the card's own picture is always Keiko's, and a step's picture goes under its line (`image` of a text step); `required: {all, any}` is the card's own rule read across the form's steps when Confirm is pressed (`parse` in `app/forms/form/actions/resume.py`, over `unfilled` of the card): while a key of `all` is empty, or every key of `any`, Confirm saves nothing and answers with the card's own refusals, `required_fields` naming the labels or `fill_one_field`, on a message of its own, and the review stays; a key of a step or a card section its rule hides, or of a step only the panel shows, never counts, and the compiler refuses a key no earlier step produces; it is checked at the setup's review, and the manager holds it on the panel too: an Edit, or a Remove of list items, that would leave it unmet saves nothing, draws the panel again and answers on a message of its own, the warning embed `commands.responses.keep_one_field` ("⚠️ Deixei como estava", the labels of `any`, a `Refusal` with `warning` drawn as `Warn`) or the error `required_fields` (`kept` in `resume.py`, `Manager.refused_by_rule`; default roles: `any: [default_roles, default_roles_bot, role_ladder]`, so a rung stands in for the join roles; `tests/behavioral/scenarios/test_roles_flow.py`) |

Native pick steps, file and design steps of their own are gone: a card's
`channel_select`, `user_select`, `selects`, `file_upload` and `design_select`
sections (3.3) are where those picks live.

### 3.3 Cards

A `CardStep` declares `sections`, each with its `type` first (the
discriminator: each type declares only the fields it reads), `key`, `emoji`,
`label`, `description` (a few lines drawn under the section's heading, in the
same text as the heading and the section's lines, with `${answer...}` and
`${context...}` resolved: what the admin needs while editing that part, such as
the variables a messages section accepts), `keys` (the state keys the section
owns: `value`, `mode`, `title`, `description`, `image`), `image` (a state key or
a `lookup_answers` key of the card whose picture the section draws under its
lines once it holds one, such as the streamer's avatar; the compiler refuses a
key the card never holds), a `when` rule, and `appears_in`: `everywhere` (the
default), `setup` for a section drawn only while the feature has nothing saved
(the setup and the edits from its review), never on the manager's edits, or
`panel` for one drawn only once something is saved. Then by type:
`button: {label}`, the button that opens a `title_content`, `file_upload`,
`modal_input` or `group` section (Edit when left out);
`screen: {title, description}`, the title and the intro of the section's own
screen, for every type that has one (`channel_select`, `user_select`, `value_select`,
`button_options`, `multi_select`, `selects`, `group`; its label and no intro
when left out), drawn on that screen whatever the type; `placeholder`, the hint
of the select a `value_select`, `button_options`, `multi_select` or
`design_select` section draws on the card (its screen title or its label when
left out). A section's value format is its type's (a channel, a member, a yes or
no, a bulleted list); a `modal_input` declares a `format` of its own, and a
`multi_select` may read its values in code (`format: code`).

`required: {all, any}` names the state keys Done insists on: every key of `all`,
and at least one key of `any` (refused with the `fill_one_field` error);
`defaults` seeds the state (the compiler refuses a key the card does not hold),
`header` draws the title block (`title`, the card's title when left out, after
the card's `emoji`; `thumbnail`, one of Keiko's pictures by name, `default`,
`nerd` or `cake` (`KeikoIcons.BY_NAME`), never an answer, and the compiler
refuses any other), `fields` declares how summaries show each persisted key (its
`label` and `format` are the ones of the section, select or input that owns the
key when left out, and a field no section owns, such as the date a transform
writes, writes its own `label` and an `emoji` to lead its line), and `transform`
names a registered transform. A card among the form's own steps may leave its
`title` out and read the intro's (3.5); a card inside a list writes its own.
`preview` puts a Preview button first under the card, before Done, Back and
Cancel, as it leads the review's buttons: `preview: true` on a card whose
preview waits for nothing and fills no token, and the object
`{required, error, tokens}` only when it waits on keys or fills tokens
(`preview: {}` is refused). Its `required`, the same
`{all, any}` as the card's own, names the state keys the preview is drawn from:
while one key of `all` is empty, or every key of `any` (a hidden one never
counts), Preview draws nothing and answers with the message of its `error`
block (`<key>.ui.errors.<name>`) on a message of its own (`ShowError`, plain, like
Done's "I still need"), and the card stays as it was; the Twitch and YouTube
cards require the streamer and the youtuber
(`tests/behavioral/regressions/test_card.py`).
The compiler refuses a `required` without an `error`, an `error` without a
`required`, and a key the card does not hold. Its `tokens` declares the
`{name}` placeholders of the card's previews (`{answer, format}`, or
`{context: server_name}`), read from the card's own draft first; an answer the
card's `transform` derives (the birthday's `date`, from its month and day) is
read as the transform will save it from the draft, so the preview shows it
before Done and follows a new month at once
(`tests/behavioral/regressions/test_birthdays.py`). A list item's
Preview (`aside:preview:<key>$<index>`) runs over an item Done already
accepted, so it never asks.
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
the new draft. A section typed in a modal (`title_content`, `file_upload`,
`modal_input`) keeps the button that opens it, a `list` keeps its Add and
Remove, and a `group` keeps the button that opens its screen. The screen a section's button opened (`press`) stays only for an
`edit: field` part edit, which opens that one section from the panel or the
review: the channel select, the role selects, the design gallery with every
banner drawn.

| `type` | Section model | What the admin does |
|---|---|---|
| `title_content` | `TitleContentSection` | keeps the default title and body or types both in the `modal` (`labels: {title, description}` names its two inputs, and its `normalize: {name}` runs over both as they are saved); `default` names the copy block they are read from, the `title` and `description` the command posts while nobody customized them (`reminders_birthday.ui.announcement.defaults`), so the card, its modal and what the members get say one text |
| `file_upload` | `FileUploadSection` | keeps the default image or uploads one; without a `mode` in its `keys` it only holds the uploaded image, and counts as set once it has one; `validation: {name: image}` checks the file Discord describes (its size, its pixels and its type, against the banner's own bounds in `DBConfigs`) before anything downloads it: a file it refuses is never re-uploaded to the dump channel, the card stays as it was and `errors.image_not_accepted` is told on a message of its own (the welcome banner and the birthday member's picture; `tests/behavioral/scenarios/test_birthdays_flow.py` and `tests/behavioral/scenarios/test_welcome_flow.py`) |
| `channel_select` | `ChannelSelectSection` | picks one channel on a native select drawn on the card |
| `user_select` | `UserSelectSection` | picks one member on a native member select drawn on the card, with no line of its own (the select shows the member); `avatar: true` draws the member's picture under the select once one is picked, read from the server the session runs in (`Context.member_pictures`, which the adapter fills from the guild's members); a list whose `unique.key` is such a section's is keyed by a member (the birthday's member) |
| `value_select` | `ValueSelectSection` | picks one of the declared `options` on a select drawn on the card (its `placeholder`), keeping the option's declared value (a number stays a number); each of its `dependents` (`{key, validation}`) clears that key when its validator rejects it with the new value (a day the new month does not have) |
| `button_options` | `ButtonOptionsSection` | picks one of the declared `options` on a select drawn on the card, each option's `description` under its label (the block links modes); its own screen, only for a part edit, is a row of buttons, and when every option declares a `description` that screen draws a divider under its title and one block per option instead (its label in bold over its description, with a Choose button beside it, primary on the current value, the option's `style` otherwise) and the picker's own description is left out |
| `boolean_toggle` | `BooleanToggleSection` | chooses yes or no on two buttons drawn on the card, Sim / Não, the current one primary and the other secondary |
| `modal_input` | `ModalInputSection` | types in the `modal`, among its `inputs`: an input with a `key` writes that key, inputs without one are joined with `;` into the section's `keys.value` (the shape services split, through `split_messages` in `app/core/utils.py`, which reads a saved list the same way); each input may `normalize: {name}`; the modal's `select` (`{key, label, placeholder, options}`, from 1 to `DiscordLimits.SELECT_OPTIONS` options, the compiler refusing more) adds a select of declared options under the inputs, drawn by the adapter as a `discord.ui.Select` inside a `discord.ui.Label`, required, on the saved option when the modal opens (and on the chosen one when a refused submit opens it again), its option's value written to its own `key` (the submit reports it as `chosen: {key: value}`; the time role's unit, the number typed above it); a section whose `keys.value` is the card's `transform` value (`time_span`: `amount` and `unit` into `time`) keeps that value in step with what the modal wrote (`_derived` in `configuration_card.py`), so its line reads the time as soon as the modal closes; the modal's `upload` (`{validation}`, a validator of uploads the compiler requires) adds an optional image under the inputs, its url written to the section's `keys.image` and a submit with no file keeping the saved one (`Imagem (opcional)`, `texts.file_upload_optional`; reaction roles' message content, its title, text and picture in one modal); the modal reopens with the saved values; the modal's `validation` runs on submit with the other items, the card's state with what the modal wrote, and any lookup its validator needs (the birthday's day: `date` refuses 31 in February, and a day typed before any month passes when some month has it; the time role's time: `time_span` refuses a number outside `FormConstants.TIME_SPAN_AMOUNT_MIN` to `TIME_SPAN_AMOUNT_MAX` and a time longer than `TIME_SPAN_YEARS_MAX` years, with `errors.invalid_time_span`, whose `$span_amount_min`, `$span_amount_max` and `$span_years_max` the validator's `facts` fill from those constants); `fills: {into, tokens}` writes the accepted value into another state key of the card: each `{token}` of that text becomes its template with `{value}` replaced (`stream_link: "https://twitch.tv/{value}"`), and when the value changes (in the setup or an item edit) the old renderings are replaced by the new ones, longest first, as whole words and whatever their case; the compiler refuses an `into` the card does not own |
| `design_select` | `DesignSection` | picks one of the declared `options` (each a design: `value`, `label`, `description`) on a select drawn on the card (each design's description under its name), the chosen design's preview drawn under its line, over the answer `background` names (the previews are drawn again when it changes); the gallery of every design, with the feature's previews, opens only for a part edit |
| `multi_select` | `MultiSelectSection` | picks several of the declared `options` on a select drawn on the card, which can be emptied; `format: code` reads each picked value in code, like a link the admin typed (the block links popular websites), `bullet` (the default) as it is |
| `selects` | `SelectsSection` | picks channels or roles on one or more native `selects` (the model refuses none; each a `Select`: `type` in `channels`, `roles`, `assignable_roles` (the roles Keiko can assign: of the roles picked, the card keeps the ones Keiko can give, below its top role, not managed and not @everyone, as `givable_role_ids` in `app/core/utils.py` reads them by id, a pick of only roles it cannot give draws the screen it came from again and changes nothing, and `commands.responses.roles_not_assignable` names every refused role on a warning embed of its own, one `- ` line each by why the adapter refused it (`why_not_givable` in `app/core/utils.py`: its `lines.above`, `lines.managed` or `lines.everyone`), closed, when a role sits above Keiko's, by how to drag Keiko's role above it (its override on `global.facts.above_keiko`) (the `Warn` effect, `tone: warning`); the role select the adapter draws splits the pick, `RolePicker` with `Picker.available_only`, and the card decides, `_on_drafted` in `form.py` over `Drafted.refused`; `tests/behavioral/regressions/test_card.py`), `key`, `emoji`, `label`, `placeholder`, `max` (the most values the select takes at once, drawn as Discord's `max_values` through `Picker.max_values`; 25, Discord's limit, when left out, and the compiler refuses one above 25 or below 1); `powerful_roles_warning` (only on `assignable_roles`, the compiler refuses it elsewhere: a picked role that can moderate the server, any of `FeaturePermissions.MODERATES_THE_SERVER` in `app/constants.py` (Administrator, Manage Server, Manage Roles, Ban Members), is kept, and this text, with `$roles` naming them, is told on a warning embed of its own beside the card, under the title of `commands.responses.powerful_roles`; the role select reports them as `Picked.warned`, the card tells them through the `Warn` effect; reaction roles' menu roles declare it, `tests/behavioral/scenarios/test_reaction_roles_flow.py`); a channel reads as a channel and a role as a role), every select on one screen; the section owns each select's `key` (no `keys` needed), its lines are one `emoji **label:** value` per select (the value alone when it holds one), and every select is drawn on the card under them; a select nothing was picked on holds an empty list, and one id is stored as itself, several as a list; on the screen of an `edit: field` part edit the first pick saves; a select whose key is not in the card's `required.all` takes no value, so an optional select can be emptied again |
| `list` | `ListSection` | draws a list of the form on the card (`list` names the list step): its heading is the list's own title (with its `overrides`), its items the bullets of their names, with "➕ Adicionar" (off once full) and the list's "🗑️ Remover" beside them (3.4); Add opens the list step's `AddItem` child and the card shows again when it finishes, its draft kept; the setup never walks a list a card draws (`listed_on_cards(steps)` in `form_yaml.py`, the one derivation the setup walk and the intro both read; the compiler refuses a `list` naming no list step), Back to the card keeps its items, and the list's child reads the card's draft through `parent.` (the card's state, defaults included); the review and the panel keep drawing the list as their own block. Declare it `appears_in: setup` when an edit of the card must not add to the list |
| `group` | `GroupSection` | names other sections of the same card in `sections`; on the card it draws its heading, its `description`, one line per section it holds that the state shows (`emoji **label:** value`, one per select of a `selects` section, a `list` by its title and its items' names, "Em branco" when empty) and its `button` as a button; the button opens a screen of its own on the same message, in place of the card, headed by its `screen.title` (the section's `emoji` before it) over its `screen.description`, or the `description` when none is declared, with those sections drawn exactly as on the card (their selects, buttons and lists) and one Back (`picker_back`) below, no Done and no Cancel; every pick there is a draft of the card, saved only by the card's Done, and the screen stays after a pick, after an item added from a list it holds and after a removal (`FormSession.opened_group`, which `opening` sets and `at` keeps only on the same step), and Back draws the card again with the new lines; `collapsed: true` draws the group on the card as only one line per section it holds (`emoji **label:** value`, a `list` by its title over one bullet per item, its name, or the empty value) beside its button, with no heading and no description (auto roles: "⏳ **Cargos por tempo no servidor:**" over "- <@&id> → 5 meses", one per time role, and **Configurar**, `tests/behavioral/scenarios/test_roles_flow.py`), and its button opens the panel's screen of a collapsed group in place of those sections (3.4); a visible group takes its sections off the card, so a section held only by groups their rules hide is drawn on the card itself (block links: `exceptions` holds the channels and roles, the popular websites and the custom links in "Bloquear todos", `channel_exceptions` only the channels and roles in "Bloquear alguns", where the blocked links stay on the card); the compiler refuses a key that names no section of the card and a group inside a group; a group owns no state key, so the review and the panel list what its sections hold as before |

A hidden section (`when` false) does not count as required, and its keys are no
line of the panel or the review: a card's rows through `section_gates`, a list item's
through `titled_items`, each over the answers of its own card (the roles a Twitch or
YouTube notice mentions are listed only while its `mention` is `roles`). Card
selections are drafts until Done: the card keeps its values on Back and on
a failed validation. A refused value is told on an ephemeral message of its
own, below the card (`ShowError`), never as a line inside it, so a phone shows
it without scrolling up: a section's validator answers with the message of the
error block it names (`<key>.ui.errors.<name>`, or a shared `errors.<name>`) and its
modal opens again with what the admin typed;
Done with required fields empty answers with the "I still need" sentence. What
was typed stays until the next change of the session (`FormSession.refused`).
A modal holds at most five components: its inputs, its select and its image.

`edit: field` on a card (the default, `edit: card`, opens the whole card) gives
each of its lines its own Edit on the
panel and the review: the click opens only that section (`Edit.part`), saves the edit as soon as a value is chosen (from the
manager) or returns to the review (in the setup), cancels on Back, and shows
the whole card when the choice leaves it incomplete. It is refused inside a
list.

### 3.4 Lists

A `ListStep` declares `items` and `steps`, the sub-form each item runs
as a child session; the document keeps the list under the step's `key`.
`items` holds `max`, `min` (`1` for a list the feature needs: 3.4's Remove
below) and `unique`: its `key` is the item key two items may not share, which
is also what an admin knows an item by (`item_name` in `summary.py` reads it,
and an item with no such value reads as its title and number), and
`immutable: true` keeps the item an
item is by: in an edit of a saved item, the card section that owns `unique.key`
draws its value as a line (its picture kept) with no select or button, and a
change that still reaches it is ignored, while an addition keeps the select
(the birthday's member; Twitch and YouTube leave it off, so an edit may rename
a streamer or a channel and move its subscription). The compiler refuses a
`unique.key` that no item step produces. `items.name` (both locales) is what an
item reads as where mentions show (the panel's and the review's bullets, the
card's list line, the removal's confirmation), written from the item's own
answers with `${answer.<key>}` (and its `:<format>`) and `${plural.<key>|...}`,
each read from the item's machine value: the roles by time of default roles read
"<@&id> → 5 meses", a real role mention, from `${answer.role:role} →
${answer.time:time_span}`; a select of items (Remove, Edit) keeps the `unique.key`
value, since a select draws no mention, in the words of its style when the style
reads as words (`WORDED` in `styles.py`: `month_day`, `time_span`, so a time role
reads "5 meses" there, never `P5M`), and the compiler refuses a token naming a
key no item step produces. `unique.normalize` (a registered normalizer) compares
the key's values through it, so two values it reads alike are one item: the
setup merges them, and the panel refuses the second as `item_already_registered`
(`same_item` of `ItemsSpec`, the one comparison `merge_item`, the manager's
`item_finished` and the feature's `refuse_duplicate` make); the roles by time
declare `calendar_span`, so 4 weeks is 28 days and 1 year is 12 months, while 1
month and 30 days, which end on different days, may both stay. A child session
sees its parent's answers through the `parent.` scope (section 4).

A list is drawn inside the card, on the panel and on the review alike
(`list_adder`, `list_bullets` and `list_remover` in `summary.py`, which the card's
`list` section and the panel's `list_head` both use): its title with a grey
"➕ Adicionar" beside it (off once the list holds `items.max`), its items as
bullets of their names, and
"🗑️ Remover" under them while the list holds more than it must keep (one item
for a list with `items.min: 1`, none otherwise). The bottom Add and Remove only stay for
a list the card does not draw. Remove opens a select of the items' names that
takes several at once (up to what the list may lose), a confirmation names
them (`removal_screen`; the chosen positions wait in `FormSession.chosen`),
and Remove there takes them all in one commit. A list keyed by a member
(`unique.key` is picked on a card's `user_select` section, `picks_a_member` in
`form_yaml.py`) asks with a member
select instead, which also takes several. A duplicate by `unique.key` writes nothing: the screen the
admin added from is drawn again and `errors.item_already_registered` is told
once, on a message of its own, so the admin can add another item from there.

`edit: item` (the default, `edit: list`, opens the whole list) draws one block
per item with Edit, and Preview beside
it when the item's card declares a `preview` (the aside runs over that one
item, `aside:preview:<key>$<index>`), between the list's title and its
Remove; the compiler refuses it on a list that may hold more than
`ViewConstants.EDIT_BY_ITEM_MAX` items. `search` on a list keyed by a
member (`search: {}`, or `search: {screen: {title}}`) puts "🔎 Pesquisar" on the panel's
last row: it asks for a member on a
select that answers as soon as one is picked (no Confirm), and is left out
while the list holds no item, the way Edit is over an empty list; it shows that
member's item on a screen of its own: the search's `screen.title` (`$item` the item's
name, "🎉 Configurações de aniversário de <@777>"; the list's emoji and the name
when none is declared, and a `screen.description` is refused) with no picture beside it, a separator, the item's lines without the one of its
name, which the title already says, then Edit, Remove and Back (Remove
asks on the confirmation of the per-item Remove below, `remove_this`, and its
Back returns to the member); a feature
adds to that screen through `PanelExtras.found`, a `FoundItem(lines)` per
item keyed by its `unique.key` value: the lines drawn under the item's own (the
birthday messages); the member's picture is drawn big under them, read like
every member picture from `Context.member_pictures` (the adapter's
`MemberPictures`, the server's own member cache), never by the feature. Such
a list keeps no Remove below the panel. The compiler refuses `search` on any
other list.

`remove: item` on the list (the default, `remove: list`, keeps the list's own
Remove picker), drawn only on the manager panel (the setup's
review keeps Edit, Preview and the list's own Remove) and refused by the
compiler without `edit: item`, puts a red "🗑️ Remover" beside each item
(`remove_this:<key>$<index>`, `ItemRemovalRequested`) and drops the list's
Remove picker: the confirmation names the item (`confirm_removal`, the
removal screen of the picker). On the only item of a list with `items.min: 1` it asks
to wait first ("⚠️ Ei, espere! Leia abaixo primeiro", the item of `commands.responses.remove` whose rule is `global.facts.removing` `is: last`)
and reads the list's `last_item_warning`, its `$item` replaced by the
item's block as the panel draws it (`item_heading` and its lines) and its
`$feature` by the intro's title without its emoji; the compiler refuses a
list with `items.min: 1`, `remove: item` and no `last_item_warning`. `$item`,
`$feature`, `$count` and `$max` are facts of `app/core/global.yml`, filled in one
pass by `filled` (`app/core/facts.py`), and the compiler refuses a `$name` of a
form text that the global catalog does not declare (`$itme is no fact of
app/core/global.yml; did you mean $item?`). Confirming that
one runs `disable` naming the `items` the panel showed, which drops nothing
when the saved list differs. `counter` on a list (a text with
`$count` and `$max`) is what the list's title line reads after its title on
the panel and on the review, in place of the empty value,
"👥 **Notificações:** 2 de 3 configurados": the items it holds of its `max`;
a list with no item keeps reading the empty value ("Em branco"), never "0 de 25"
(the block links custom links, "📌 **Links personalizados permitidos:** 2 de 25"). An item's bookkeeping keys (`enabled` among them, which a per-item
pause in dev once wrote) are never a line or an answer of the item, the way a
document's are not.

On the manager panel every visible step is a block with an Edit beside what it
opens (`panel_rows` and `panel_groups` in `app/forms/form/responses/summary.py`,
which the review draws too): a card lists its fields, a list its items, and
any other step its own answer.
A block of one line shows no heading, and a block titled like the panel shows
none either. A feature that builds its own rows (`PanelExtras.rows`) names each
row's `key`; a keyed row joins the block of the step that produces that key,
and the global Edit only stays while some row names no step. Rows about a list
saved with no item keep no Edit, since there is nothing it could open. A
setting that holds several values (a multi-select) reads as its bullets under
its title, with its Edit beside the title. The panel's intro is the intro
step's `panel.description`.

A heading several steps share is declared once, in the root `groups`
(group id -> `title`, `emoji`, `screen: {description}` (the intro of the
group's own screen, which the group's `title` titles, so a `screen.title` is
refused), `overrides` (each `{when, title, description}`, replacing `title` and
`screen.description` while its rule holds, the first that holds winning), `opens`, `button`, `order`), and a
card, a list or a card field joins it with `group: <id>` (the compiler refuses
an id the root does not declare, a group no step names, and a `group` inside a
list's items, whose rows are no panel rows). `opens: inline`, the default,
lists the group's rows on the panel; `opens: screen` is for a heading whose rows
open on a screen of their own behind one Edit, where a
multi-select's options turn on and off as buttons, a list draws one block per
item (`item_groups` in `summary.py`, the panel's own, with no heading per item
and the first `ViewConstants.GROUP_ITEMS_SHOWN` items), and the rows that open
the same step share one Edit.
`collapsed: true`, only with `opens: screen` (the model refuses it otherwise), draws a list of the group on the panel as its title and its `counter` beside the group's Edit, its Add, its items and its Remove only on the group's screen, which draws every item (it is their only way in) and, under a screen title the list's own title repeats, reads the list's head as its count alone, and the panel's last row keeps no Add or Remove for it (`_draws_its_list` and `_group_blocks` in `manager.py`; the roles by time of auto roles; a card's `group` screen likewise drops the heading of a section that repeats its title); since every item is on that screen, each item block ends with its own Edit and red Remove (`remove_this`), as a panel list with `remove: item` does, while the list holds more than its `items.min` (`group_list` in `summary.py`, `item_groups(removes_each=...)`), and the list's own Remove picker goes. A collapsed card `group` screen is that same screen: the setup draws its own title and intro over the panel's blocks and Back (`group_screen_of` in `actions/action.py`, `_collapsed_blocks` in `configuration_card.py`), so a list it holds reads its count beside Add (`add:<list>`), then each item with its Edit (`edit:<list>$<index>`) and red Remove, and any other section it holds is its line beside an Edit that opens that section; Add and Edit come back to that screen, and Remove asks, then takes the item out of the setup's answers, nothing saved yet, refused while the list holds no more than its `items.min` (the form's `ItemRemovalRequested`); the compiler counts the panel's blocks, each item at the list's `items.max` (`group_components`). The review keeps drawing the list inline, as it does for every group.
`opens: button` with `button: {label}` takes such a heading off the panel and
opens it from a button of that label on the panel's last row, always on a
screen of its own; the compiler refuses a `button` without `opens: button` and
the other way round. A feature may also hand the
panel `PanelExtras.pages`, each a `PanelPage(title, lines, description)`: the
panel draws the first one under its blocks, its title with the description
under it and, when the form has a list, the list's "➕ Adicionar" beside the
title (off once full), so the panel's bottom row keeps no Add; a page with no
lines still shows its title, its description and its Add (the birthday list
with no birthday). It turns through them with ◀ and ▶ (`page:<n>`), keeping the
page on a redraw.

### 3.5 The shared vocabulary: fallbacks and refs

`app/forms/vocabulary.yml` is the copy every form shares, each text in both
locales, read once into a `Vocabulary` (`shared_vocabulary()` in
`form_yaml.py`; `compile_form(key, raw, vocabulary)` takes another one, for a
test). A step writes only what differs from it:

- `fallbacks` is what a step reads in place of a field it leaves out (named
  apart from a card's `defaults`, which seed its state). A step without
  `footer`, at any depth (a list's item steps too), reads `fallbacks.footer`,
  the bug-report line; `footer: null` keeps a step without one (the
  StreamElements streamer step, the only one today). A review without
  `description` reads `fallbacks.review_description`, the sentence that names
  the command it turns on. A card or the review among the form's own steps,
  without `title`, reads the intro's title (every review, and the welcome
  messages card), so the schema leaves those fields optional; a list's item
  card writes its own, and the compiler refuses one that does not. A step that
  writes the field keeps what it wrote.
- `texts` are the texts a form names with `${ref.<name>}` wherever a text in
  both locales is allowed (a title, a label, a modal field's label, an option,
  a card's default): `label: ${ref.channel}`, a field written only as the token
  taking the text in both locales, and a token inside a longer text the text of
  that locale. A ref name is `snake_case` like every key. The compiler refuses a
  name the vocabulary does not hold, naming the form, the step, the field and
  the closest name (`welcome_messages.welcome_config: sections[0].label:
  ${ref.chanel} is no text of app/forms/vocabulary.yml; did you mean
  ${ref.channel}?`), and the old `{ref: name}` node with its new spelling.

Both are written out before the models validate anything: `written_out(key,
raw)` is the raw document with every ref replaced by its text and every
fallback in place, which `compile_form` validates, so the schema of 3.1 to 3.4
stays the one an author reads. The readers outside the engine that walk a form
(`step_titles` in `app/core/utils.py`, under the journey and the admin reports,
and `configurable_fields` in `app/core/analytics_reports.py`) read the compiled
definition from the registry, so a label left to its owner reads as the label
it defaults to. The vocabulary is read through `strict_yaml`, as strictly as a
form. A text that three fields of the forms would write out belongs in
`texts` or in a default; one written twice may stay
(`test_no_text_is_written_out_three_times_across_the_forms`).

No `copy.yml`, no `form.yml` and not the vocabulary use YAML anchors or aliases
(`&name`, `*name`, `<<:`): a database document cannot hold them, and the defaults
and the refs solve repetition the same way in a file and in a document
(`test_no_command_and_not_the_shared_vocabulary_writes_a_yaml_anchor_or_alias`;
`strict_yaml` refuses one at load too, and a key written twice, in either file).

## 4. The `when` grammar

One grammar serves `when` on a step and on a card section, the `when` of each
of the `overrides` of a step or a group, and the `when` of each of the
`overrides` of a copy block (`docs/commands.md`, "Overrides and facts"). Of a list
of overrides the first whose rule holds wins and replaces only the texts it
writes, the default text otherwise (`winner` in `app/core/conditions.py`,
`overridden_text` in `form_yaml.py`), for a step's title and description and a
group's alike. The grammar and its evaluator live in `app/core/conditions.py`,
pure (no Discord, nothing of the app), so the form engine and the copy
primitives of `app/ui` read the same one; the compiler checks every rule of a
form before the form exists, and the copy loader every rule of a copy file. In a
form a key reads the form's own answers, as below; in a copy file a key is a
fact's address, `<owner>.facts.<name>`.

A leaf tests one key with exactly one operator (the compiler refuses a leaf
with none or with two): `is`, `in`, `not_in`, `matches` (a regular expression),
`present` (`false` for an empty value), `count: {min, max}`. Combinators:
`all`, `any`, `not`.
Scopes: a bare `key` is an answer of this session; `parent.key` is an
answer of the session that opened this one (repeatable); `item.key` is a
value of the item being edited; `items.count` is how many items the
list holds.

```yaml
when:
  all:
    - key: mode
      is: block_all
    - key: allowed_links
      present: true
```

The compiler refuses a rule whose key is not produced earlier in the same
or an outer scope (an outer answer is always written `parent.key`), a value
of `is`/`in`/`not_in` that is not among the
declared options of that key, and a card section that names an unknown
state key. Booleans compare by their lower-cased spelling, so `is: true`
matches an answer stored as `True` or `"true"`.

## 5. Extension points and registries

Everything a definition may name by string is registered in one place;
nothing is looked up by reflection.

| Capability | Registry | Entries today |
|---|---|---|
| Step types | `registry()` in `app/forms/form/actions/__init__.py`, one `Kind(render, parse)` per name | the six types of 3.2 |
| Card section types | `SECTION_KINDS` in `app/forms/form/actions/configuration_card.py`, one `SectionKind(press, change, preview, reset, custom, controls)` per YAML `type` (`controls` are the selects or buttons the section draws on the card; a kind without them draws the button whose `press` opens its modal); what a section declares (`choices`, `holds_several`, `adds_to`, `opened_modal`, `inputs`, `checked_by`, `uploads`, `validators`, `offered_designs`, `drawn_over`, `looked_up`, `filled`, `picks`, `controls_drawn`, `pictured`, `picks_members`, `holds`, `value_style`, `pressed_label`, `select_placeholder`, `own_screen_title`, `own_screen_description`) is asked of the section model, never by `isinstance` | the thirteen types of 3.3 |
| Validators | `VALIDATORS` in `app/forms/form/responses/validations.py`; each declares `needs` (`answers`, `items`, `external:<service>`, where `{param}` is filled from the declaration, and `upload`, the size, pixels and type of a file a `file_upload` section's modal sent, `ValidationContext.upload`; the compiler refuses a validator that needs `upload` anywhere else, and one that does not on a `file_upload`) so the adapter prefetches before `decide`, the `facts` its error reads (passed with the refusal and filled into the error embed, as the global facts of `app/core/global.yml`), and the `params` its declaration must give: `validation: {name, params}`, an `Extension` like every reference to a validator, a transform or a normalizer, the params reaching the check in `ValidationContext.params` (the compiler refuses a declaration that does not give exactly the params the validator takes) | `registered_and_found` (`item` the saved items a name must not repeat, `service` the lookup that must find it, `id` the field of the lookup that proves it, `error` the prefix of the two errors: `<error>_already_registered` and `<error>_not_found` when it names a block of a command's copy (`<key>.ui.errors.<noun>`, the YouTube channel), `<error>-already-registered` and `<error>-not-found` when it names a shared error of `errors.yml` (the Twitch and the StreamElements streamer)), `link_or_domain`, `date` (`month` the answer key of the month the day is checked against), `image` (a PNG, JPEG, GIF or WEBP within `DBConfigs.IMAGE_MAX_BYTES` and `IMAGE_MAX_PIXELS`, the bounds the banner draws within) |
| Transforms | `TRANSFORMS` in `app/forms/form/responses/transforms.py`; a `Transform` names its `part_keys` (each read from and seeded into the answer key of its own name), `value_key`, `style`, `serialize` and `hydrate`; `transform: {name}`, which takes no `params` (the compiler refuses any) | `mm_dd_date_parts`, `normalize_link` |
| Normalizers | `NORMALIZERS` in `app/forms/form/responses/transforms.py`, one `Normalizer(apply, announce)` each, named by `normalize: {name}` on a text step, a modal input or a `title_content` modal, which takes no `params` either; a normalizer that declares an `announce` sends `commands.responses.<announce>` once, as `Announce` does, whenever it changed what the admin typed (`told`, run by `_on_section_changed` and `_on_answered` in `form.py`) | `handle`, `lowercase`, `without_group_mentions` (an @everyone, @here or role mention typed in a notice or birthday message goes on save, a member's stays, and `commands.responses.mention_removed`, "📣 Tirei a menção do texto!", says to use 📣 Mencionar) |
| Formatters | `STYLES` and `format_value` in `app/forms/form/responses/styles.py`; `stored_style` maps a declared `format` to the style it reads and is saved in | the formats of 3.1 |
| Copy tokens | every token is read by the one syntax of `app/core/tokens.py` (`docs/commands.md`, "The token syntax") in one pass, and `apply_tokens` (`app/forms/form/actions/action.py`) gives each its value: an answer through `TOKEN_FORMATTERS` (`app/forms/form/responses/styles.py`, the compiler's `TOKEN_FORMATS` with the value formats), then every value format (read through `stored_style`); a `${plural...}` picks one of its two texts by `count_of` (`app/core/conditions.py`, the count the `when` grammar's `count` leaf reads); the `${context.<name>}` values are declared in the `context:` of `app/core/global.yml` (`compile_form` refuses an undeclared one): the families known at load (`commands`, `groups`) are written in by `with_context` (`app/core/facts.py`) before the form compiles, and the rest are fields of `Context` and its `values`, filled in `Runtime._context` (`callbacks.py`) from `Opened.values` and from the server's settings `Runtime._open` read (`server_settings`, `app/core/servers.py`) | `host` and the formats, `plural`; context `commands.<key>`, `groups.<group>`, `server_name`, `prefix`, `server_language`, `server_timezone`, `command` and the feature's own values (`members_count`, `bots_count`) |
| Copy resolution | `text(key, locale)` and `normalize_locale` in `app/forms/form/copy.py` | reads the copy `ml` reads (`app/core/i18n.py`): the shared files of `app/languages/` and each command's `copy.yml`, under its key; a form names an error of its command by its full block, `error: <key>.ui.errors.<name>`, and `error_message` reads its `message` (a key that names a text and not a block is read as it is); the copy blocks and the primitives that read them are in `docs/commands.md` |
| Definitions | `DefinitionSource` (`load`, `list`) in `app/forms/form/form_yaml.py` | `DiskSource` over each command's `form.yml`, found by `form_file(key)` (`app/core/commands.py`) and read whole through `strict_yaml` |
| Shared vocabulary | `shared_vocabulary()` in `app/forms/form/form_yaml.py`, over `app/forms/vocabulary.yml` (3.5) | `fallbacks` (`footer`, `review_description`) and the `texts` a `${ref.<name>}` names |
| Features | the `feature` of each `Command` (`app/core/commands.py`); `feature_for(key)` in `app/forms/features/__init__.py` asks the registry for it | the seven feature modules of the command folders; a command with a form and no `feature` gets `GenericCogFeature`, and a key no command declares is refused |

An extension may: register a step type, a pure validator with its `needs`,
a transform, a formatter, a card section type; provide a `FeatureModule`.
An extension may not: change the session, call Discord, read another
feature's document, or receive a command key. A validator returns the
error key or `None`; the step turns it into a `Refusal` that names its
`target` (the step key, or `section:<n>` of a card), the `inputs` typed and,
for a card confirmed incomplete, the `missing` keys. `Form.refuse` draws the
screen that asked again and then answers with the message of the error block
(`error_message`, `app/forms/form/copy.py`) in the admin's locale on an ephemeral message of its own (`ShowError`), the last
thing the admin sees: a card section's modal reopens with what was typed, and
a modal step (Discord takes no second modal after a modal submit) becomes a
one-field card with the typed value and an Edit that reopens the modal
prefilled (`modal.refused_screen`). A refusal without a target (a picker
confirmed empty) only sends the message.
`setup.required_missing` is emitted only for missing fields, every other
refusal emits `setup.validation_failed`, whose `error_key` is the error's bare
name in kebab-case, without its copy path (`youtuber-not-found` for the block
`notifications_youtube_video.ui.errors.youtuber_not_found`), the spelling released
versions recorded (`_recorded_error` in `form.py`; `docs/analytics.md`), so a copy
that names the error in snake_case keeps its series.

## 6. Feature modules

`FeatureModule` (`app/forms/features/feature.py`) is what a feature
contributes, and nothing more:

| Member | Role |
|---|---|
| `key` | the form key |
| `open(context) -> Opened` | the saved document and its `PanelExtras` (`rows`, `pages`, `found`, `info`, `info_title`, `info_action` (one button drawn beside the info block, inside the card, such as StreamElements' command list), `extra_buttons` (the panel's last row), `enabled`; `GenericCogFeature` fills `info` and `info_title` from `panel.info` of its copy, unless the feature declares `fills_panel_info` because its `panel.info` reads facts only it can pass (default roles, StreamElements), and `extra_buttons` from its `panel_asides`, one button per aside read from `buttons.<aside>`), `previews` or `pending_previews`, or an empty `Opened` for setup; `refusal` names an error key when the feature cannot open; `values` are the `${context.<name>}` tokens its copy reads (default roles: the server's members and bots, which the setup's own step asking whether to give the roles to who is already there shows; a `hidden` options step after the card, so the summary and the panel never list it) |
| `prefetch(lookup, context)` | the external data a submitted modal asks for: `lookup.services` are the `external:<service>` needs of its validator (a text step's or a card section's modal) and `lookup.value` is the typed value after its `normalize`; the adapter defers the interaction before running it |
| `to_document(answers, locale)` / `from_document(document)` | answers to the persisted document and back, any schema version |
| `commit(kind, payload, context) -> CommitResult` | writes what `kind` asks (`setup`, `edit`, `toggle`, `edit_item`, `add_item`, `remove_item`, `pause`, `unpause`, `disable`), records the audit event, and reports `written` and `external` so a failure midway can say what happened, and `asks`, a `FollowUp(aside, values)` the adapter asks right after the save, below what the save drew: the confirmation of that aside, its text filled with `values` (default roles' first rung: "⏳ Dar os cargos por tempo agora?" with how many members are already past one, the aside giving them on Confirm), and, when its `see_all` is given, a grey "🔎 Ver todos" (`buttons.see_all`) before Cancel that calls it with the click, to open what the text cuts short apart from the question (the roles by time: every member the question counted, in a `RecordsBrowser` filtered by role). Every question an aside asks first, on a click or after a save, is drawn as its Response block says: an embed with `ConfirmActionView` below it, or, when the block writes `card: true`, a Components V2 card (`response_card`: the title and text beside Keiko's picture, the block's `info` after a divider, then the footer) with `ConfirmActionCard`'s Confirm, See all and Cancel in one row below it; once answered or cancelled the card stays, without its buttons, and both wait `ViewConstants.SHORT_TIMEOUT_SECONDS` (default roles' two questions, Sincronizar and the roles by time, are cards; the roles by time names its first `ViewConstants.QUESTION_GROUPS_SHOWN` roles, `QUESTION_GROUP_ITEMS_SHOWN` members each, and its `info` overrides name the roles left out or point to See all, the button showing exactly with that line; `tests/forms/discord/test_discord_adapter.py`, `tests/behavioral/scenarios/test_roles_flow.py`). The list and choice commits apply each change to the settings as they are saved when the admin confirms (`saved_document`, `saved_list`, read through the feature's `normalized`): `toggle` sets one option to the state its button asked for (`turned_on`), so the same click from two screens gives the same choice, and the item commits find the edited or removed items by the content the panel showed (a removal names `indexes` and the `items` they held, and takes every one of them or none, never leaving a `required` list empty), refuse a `unique.key` value already listed (`DuplicateItem`), keep on an edited item the `state` Keiko keeps about it (an item's bookkeeping, never an answer: the reaction roles menu's id), and write only while nobody changed the value meanwhile (`data().guild_cogs.update_cog_if_unchanged`); otherwise `ListChanged` saves nothing. `pause`, `unpause` and `edit` first check that the document is still saved (`saved_document`). A refusal drops the cached document, the adapter reads the feature again (`Runtime._reopen`, which keeps the document it had and logs the error when that read fails) and the manager answers: the out-of-date notice and the screen redrawn from the saved list, the duplicate copy once over the screen drawn again, or, once the feature is gone, `Finalize("closed")`, which takes the rows of buttons off the message and writes the closed notice inside it. A refusal keeps the session open (a duplicate included), or closes it after another panel's Disable, and emits no event: only a commit that fails the session emits `feature.commit_failed`. What a change needs outside runs before the write (`before_item_added`, `before_item_replaced`) and is let go when the write fails (`save_or_let_go`); what it leaves outside runs after it (`after_item_replaced`, `after_item_removed`, `after_disable`), best-effort and logged when it fails (`best_effort`); what may take long (default roles giving the saved roles to every member already in the server) starts after the commit through `in_background`, best-effort too, and the commit never waits for it, so the answer to the click stays in time. `CommitContext.guild` is the guild the session runs in. Disable deletes the document and lets go of what it listed before it records the flag; a `disable` that names `items` (the removal of a list's only item) first checks the list is saved as them |
| `asides()` | read-only side actions by button action name (`AsideAction(handler, defer, own_response, cooldown, confirm)`); a button may name one item of the list (`aside:<name>:<key>$<index>`), and that item cools down on its own; `GenericCogFeature` gives every feature `stats`, the statistics the panel's "📊 Estatísticas" opens (`send_feature_stats`, `app/core/feature_health.py`: the feature's `stats_lines`, none by default, then its last 7 days), and a feature that adds its own merges `super().asides()` |
| `responses_for_preview(answers, locale)` | the answers in the shape the preview functions read |
| `responses_for_aside(answers, locale, index)` | the same rows with the list replaced by the item at `index` (the first when none is named), from the answers on a form and from the saved document on the panel |

A feature never sees the session, never renders, and never reaches another
feature's document. `GenericCogFeature` (`app/forms/features/feature.py`)
covers the common case: one `guild.cogs` document per guild and feature
(`_id` `{guild}:{cog}`), lifecycle through `insert_cog_event`. End the
command's `service.py` with a `FEATURE` of its own only when persistence is not that
(birthdays write members, twitch and youtube subscribe through the shared
`SubscriptionFeature` of `app/forms/features/subscriptions.py`, welcome
messages render banners).

Documents keep the persisted shape `{style, values}` per key that every
reader outside the forms depends on, with the style spellings released versions
wrote (`code-block`, `mm_dd`, `boolean-mode`, `composition`: 3.1);
`app/forms/form/responses/responses.py` is the one place that shape is built
and read. A runtime reader reads it through
the read model there: `document_values` for the settings and `list_values`
(each item through `item_values`) for a list give machine values, the
`_raw_value` beside a label and never the label; saved items keep their
labels in `value`, because v0.9.0 shows and matches them.

## 7. The Discord adapter

- **Codec and stale clicks.** Every component id says which session, seen
  at which revision, doing what. A duplicate `event_id` is applied once; a
  click from an older revision gets the current screen drawn again and no
  notice (`Engine.redraw`). A session id names its feature
  (`<feature>-<16 hex>`, `form_state.feature_of`), so a custom id says which
  form it belongs to even once no session holds it.
- **Late clicks.** A form's view lives as long as its session
  (`ViewConstants.LONG_TIMEOUT_SECONDS`) and none outlives a restart, so a click
  that lands later reaches the bot by its custom id alone: `views.LateComponent`,
  a `DynamicItem` over every codec id but the reopen one, wraps the button or
  the select discord.py finds on the message, and a modal submitted with no live
  modal reaches `Runtime.late` from the shared `on_interaction` listener
  (`app/core/events.py`). When the session is gone, expired or past its deadline,
  the click edits its own message, with its own interaction, to what the expiry
  draws: the texts and pictures kept, every control gone, the expired line and
  one button, **Abrir de novo** / **Open again** (`views.ReopenButton`, coded
  `k:<session>:<revision>:reopen:<feature>`), rebuilt from the message itself
  (`transitions.message_surface`). That view has no timeout: it holds only the
  reopen item, which discord.py keeps no copy of, and a view that times out makes
  discord.py unregister the route of every dynamic item it holds, for every
  message. While the session is alive the late route
  does nothing and the live view answers. discord.py hands a click to both
  routes when a live view still holds it, so each claims the click first
  (`Runtime._claim`, kept for `DiscordLimits.INTERACTION_TOKEN_SECONDS`) and
  the second leaves it. The reopen button opens the feature for an admin the
  way its slash command does (its panel when configured, else its setup; source
  `expired_button`). The sweep is unchanged. Both items are registered once in
  `setup_hook` (`views.routed_items`); pinned by
  `tests/behavioral/regressions/test_card.py`
  and `tests/behavioral/scenarios/test_card_flow.py`.
- **Language.** Every app command, slash or context menu, passes
  `KeikoTree.interaction_check` (`app/bot.py`) before it runs, which makes
  `interaction_language` (`app/core/servers.py`) the interaction's
  locale and keeps the reader's own in `reader_locale` (the translation reads
  it); components that are not a session call `interaction_language` in their
  own callback. A server with no saved settings (`ServerSettingsMissing`, the
  seconds between Keiko joining and saving its guess) is answered in the
  person's own language, the only fallback. A session opens in the language
  its opening interaction was given: the server's
  saved one, whatever the admin's Discord says, and `Origin.locale` keeps it.
  Every click on the session speaks it (`speak`, before anything reads
  `interaction.locale`), so a side action, an error and a confirmation answer
  in the language the screen is drawn in; a click on an unknown session asks
  `interaction_language` itself.
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
  confirmed empty, a commit that failed), `Notice` (a short note, gone after
  `ViewConstants.ACTION_NOTICE_SECONDS`) and `Announce` (Keiko's embed of
  `commands.responses.<kind>` beside the screen, gone after the same time: a
  pause, an unpause, a typed mention taken out) are ephemeral follow-ups; `Confirm` and `Dismiss` handle the discard
  confirmation on a message of its own (an edit the manager panel opened
  and discarded there is dismissed like a kept one, and the manager draws the
  screen the edit was opened from again and saves nothing: `_on_child_finished`
  in `manager.py`), which Cancel asks only when there is something to lose: a session with no answer, no saved document and no
  answered parent (`_holds_nothing` in `form.py`) is discarded at once, with
  the same `setup.discarded` and the same `Finalize("discarded")`
  (`test_cancel_with_nothing_answered_closes_at_once`,
  `tests/behavioral/scenarios/test_card_flow.py`); `OpenChild`, `ResumeChild` and
  `ResumeParent` move between a parent and its child session; `Commit`
  calls the feature; `Finalize(kind)` closes the message with
  `commands.responses.<kind>`; `OpenPanel` sends the manager panel of a
  saved setup as a new message below its activation embed, or, for a setup
  opened with an `ending`, takes the place of that `Finalize` and draws the
  ending's view on the setup's message; `ReturnToOpener` closes a setup opened
  with a `back` from its first screen, drawing the back's view on its message; `RunAside` runs a help, history,
  preview or feature aside; `Ack` acknowledges and changes nothing.
- **Limits.** The compiler refuses a card that would exceed Discord's forty
  components (`CV2_MAX_COMPONENTS`), counted by `card_components`: the frame
  (the header), each section's one text (its heading,
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
- **Expiry.** The shared listeners (`app/core/events.py`) run `Runtime.sweep` every
  `ViewConstants.FORM_SWEEP_SECONDS`. A session is due when it sat idle for
  `ViewConstants.LONG_TIMEOUT_SECONDS` since its last accepted event and no
  child of it is still in use; it reaches `decide` as `Expired` and its journey
  closes. The message loses its buttons only while the latest interaction token
  is valid (`DiscordLimits.INTERACTION_TOKEN_SECONDS`); after that, the next
  click on the message draws the expiry (late clicks, above). Closed sessions past their deadline are
  forgotten. A closed, expired, discarded or previewed card keeps its texts
  and pictures and loses every control (but an expired one's Abrir de novo),
  the Edit or Add beside a heading included (`views.finalized_layout`: a section whose accessory is a button
  becomes its texts, a thumbnail stays;
  `tests/behavioral/regressions/test_panel.py`).
- **Layout.** `card_frame`, `card_header`, `card_row` and `card_footer`
  (`app/ui/embed.py`) hold the Components V2 frame, header, rows and footer;
  the renderer (`app/forms/discord/views.py`) and the `/setup` card
  (`app/commands/setup/service.py`) both draw through them. A screen's own buttons sit below the card, outside its frame;
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

A form belongs to a command, and the command's folder holds all of it
(`docs/commands.md`). `make command name=<x> form=yes` writes the folder with
a form that compiles; then:

0. **The declaration**, `COMMAND = Command(...)` in
   `app/commands/<x>/__init__.py`, listed in `COMMANDS`
   (`app/commands/__init__.py`): the key, the cogs, `feature` (its module,
   or `None` for the generic one), the slash command's group, its /setup row
   (`on_setup`) and emoji, the
   permissions it needs and whether it assigns roles. Every list of
   features (/setup, the greeting, `feature_keys`, the feature health line)
   is read from these declarations through the registry, so it cannot drift.
   No field names its files: the copy and the form are the folder's
   `copy.yml` and `form.yml`. `tests/test_commands.py` refuses a command whose
   form does not compile, a YAML in the folder other than `copy.yml` and
   `form.yml`, or copy missing in a locale.
1. **The definition**, `app/commands/<x>/form.yml`: `steps:` composed from
   3.2, opening with `type: intro` and closing with `type: review`, every
   string in both locales, in schema order with each open block after the
   closed ones under its `# open:` line; the standard footer, the review's title and
   description and a card's title equal to the intro's are left to the defaults,
   and a text the vocabulary holds is named with `${ref.<name>}` (3.5).
   `pytest tests/forms/form -q` compiles every form
   on disk; a bad rule or a missing locale fails there.
2. **The cog**, `app/commands/<x>/cog.py`: the slash command's body calls
   `open_feature(interaction, key)` (through the service's `manager`, the
   convention every feature follows), in a subgroup whose `namespace` is the
   command's `<key>.command.slash` block, under the group the declaration names.
3. **The copy**, `app/commands/<x>/copy.yml` beside it, every text in `en-us` and
   `pt-br` under a key relative to the command (its `slash` name and
   description, its /setup row under `setup` and `stats`, and every string
   only it shows), each in the block of the primitive that draws it
   (`docs/commands.md`), snake_case, a filled value written `$name`; an error a
   shared validator answers with is named by its block in the form
   (`error: <key>.ui.errors.<name>`), or goes to `app/languages/errors.yml` when
   several commands share it. Reuse `buttons.*` labels and `commands.responses.*`
   state messages before adding keys.

Persistence is generic (`GenericCogFeature`, `feature=None`) unless the
feature needs more; then the folder's `service.py` ends with a `FEATURE` that
fulfils section 6, and the declaration names it. A behavioral scenario in
`tests/behavioral/scenarios/` and a golden path in
`tests/behavioral/golden/paths/` pin what the admin sees
(`docs/form-scenario-testing.md`).

## 9. Decision order for a change

1. **YAML only**: compose the step types, sections, rules, validators,
   transforms and formats above.
2. **Reuse a capability**: a step type, validator or feature module already
   supports it, possibly with one more declared field.
3. **A new reusable primitive**: a step type, validator, transform, formatter or
   section type in its registry, generic by construction (no command key,
   no Discord, no session).
4. **An architecture exception**, declared and justified in the plan, never
   silent. The platform has none today; a command key in `app/forms/` is
   refused by `tests/test_structure.py`, and `command_key` by the boundary
   test.

## 10. Enforcement

- `tests/forms/test_boundary.py`: `discord` only under the adapter; the
  engine imports nothing internal but itself, `app/constants.py`,
  `app/core/calendar.py` and `app/core/commands.py` (which imports the forms
  only for its types); no `command_key`, no blocking call, no reflection
  outside the adapter, no comment longer than one line; and nothing in
  `app/core`, `app/commands`, `app/ui` or `app/integrations` redefines a
  public name the `responses` package owns (names only, not bodies).
- `tests/test_structure.py`: `app/forms/` imports no command and `discord`
  only in its adapter; `tests/test_structure.py`: the engine, the
  adapter and the shared feature modules name no command key; `tests/test_commands.py`:
  every declared form compiles and is the one in its command's folder;
  `tests/forms/features/test_features.py`: no folder holds a `feature.py`, and the
  `FEATURE` of a service is the `feature` its command declares.
- `tests/forms/test_invariants.py`: I1 to I9 of the review (one session one
  id and a strictly increasing revision; only `decide` changes a session; a
  closed session rejects change; the same event id applies once; an older
  expected revision is rejected; render is a pure function; a feature never
  receives the session; an invalid definition never compiles; every session
  names its definition version; the locale is normalized once).
- `tests/forms/form`, `tests/forms/actions`, `tests/forms/features`,
  `tests/forms/discord`: the compiler, `decide`, the rules, the session, the
  step types, the feature modules and the adapter choreography;
  `tests/forms/form/test_form_yaml.py` also refuses a YAML anchor or alias in
  a form or in the shared vocabulary and a text written out three times
  across the forms (3.5).
- `make lint`: `ruff` and `mypy --strict` over `app/forms/`, `app/data/`, the
  whole of `app/commands/`, the registry, dispatch, groups, copy loader and copy
  schema of `app/core/`, and the pagination, embeds and records browser of
  `app/ui/`, `ruff` also over `app/migrations/`, `tests/forms/` and
  `tests/core/`, plus `ruff check --select F401,F821` over the whole of `app/`
  (unused imports and undefined names), which is the only static check that
  reaches the rest of `app/core/`, `app/ui/` and `app/integrations/`; the
  style rules are `.claude/rules/code-style.md`, rules 13 onwards.
- `pytest tests/behavioral/golden -q`: the goldens are the UX contract; a
  diff that changes what an admin sees needs a row in `docs/ux-changes.md` before
  re-recording; one that moved only because the tests changed needs none.
