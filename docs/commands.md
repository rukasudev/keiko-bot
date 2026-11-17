# Adding a command

One command is one folder under `app/commands/`. Everything that belongs to it lives
there: its slash commands, its rule, its form, its copy. Its `__init__.py` declares it
once as `COMMAND = Command(...)`, and `app/commands/__init__.py` holds `COMMANDS`, the
explicit, ordered tuple of those declarations every shared layer reads through the registry in
`app/core/commands.py`. The shared layers (`app/core`, `app/forms`, `app/ui`,
`app/integrations`, `app/data`) serve every command the same way and never name one.

## Start with the scaffold

```
make command name=greet            # a slash command that answers
make command name=greet form=yes   # the same, opening a form
```

It writes the folder, its `copy.yml` (and, with `form=yes`, its `form.yml`) and a test, and refuses a folder that already exists. Then add
the command to `app/commands/__init__.py`: import its folder and put its `COMMAND` in
`COMMANDS`. The tuple is explicit and ordered: /setup lists the commands in that order,
their hooks run in it, and their cogs load in it.

The form suites read every form from the registry. A command with a form needs one
golden module, `tests/behavioral/golden/paths/<key>.py`, with its `setup_happy` path.
To offer it on /setup, give it `on_setup=True` and an `emoji`, and write its row's copy
in its own `copy.yml`, at the fixed places the shared layers read under `command`:
`command.setup.label`, `command.setup.description`, `command.stats.title`,
`command.stats.delivered.one`, `command.stats.delivered.other`, and
`command.slash.subgroup` and `command.slash.name` of its slash command
(`tests/test_setup_row_copy.py` checks them).

## One command folder

```
app/commands/greet/
  __init__.py    COMMAND = Command(...): the one declaration of the command
  cog.py         the slash commands and context menus, thin: they call the service;
                 ends with `async def setup(bot)`, the entry point load_extension calls
  service.py     the command's rule; it may be wholly specific to the command, and it
                 ends with `FEATURE`, its FeatureModule, when the generic save of a
                 form is not enough
  copy.yml       its copy; every text in en-us and pt-br
  form.yml       its form, only for a command with one; every text in en-us and pt-br
tests/commands/greet/test_greet.py
```

Both files are found by convention: the registry reads the ones beside the package
whose `COMMAND` it is (`copy_file(key)`, `form_file(key)`), so the declaration names no
path, a command without a form has no `form.yml` (the folder listing shows which
commands have one), and a command without copy or form (admin, config) has neither.
They are the only YAML in the folder. Copy is text the Python code reads and a form
is a flow the engine compiles, so they are different edits and different diffs:

```yaml
# app/commands/greet/copy.yml
command:
  slash:
    name:
      en-us: greet
      pt-br: saudar
    description:
      en-us: Keiko greets the server
      pt-br: O Keiko cumprimenta o servidor
```

```yaml
# app/commands/greet/form.yml
steps:
  - type: intro
    key: form
    title:
      en-us: "👋 Greet"
      pt-br: "👋 Saudar"
    description:
      en-us: "What I do once this is set up."
      pt-br: "O que eu faço quando isto estiver configurado."
  - type: review
    key: confirm
```

The two files are the two halves of the document a command made from data will store
(`{copy, form}`), so a command written in the repository and one an admin makes share
one format and one validator: every text a `{en-us, pt-br}` node, no YAML anchor or
alias, snake_case keys relative to the command, each file read against its schema
(below, "The copy schema and the primitives that read it").

A command folder may hold several cogs (birthdays has two). It imports the shared
layers and never another command: what two commands share moves to a shared layer.
There is no `feature.py`: the `FeatureModule` lives at the end of `service.py`, beside
the helpers it calls, and the whole of `app/commands` is linted by ruff and typed by
`mypy --strict` (`make lint`).

## The declaration

| Field | What it holds |
| --- | --- |
| `key` | The immutable key: the `_id` in `guild.cogs` and the analytics `cog`. |
| `cogs` | The cog classes of the command (one or more). An `app_commands.Group` is attached under the command's `group` by `app/core/groups.py`, which refuses one whose command names no group; any other cog loads through the `setup(bot)` of its module, and a module without one is refused. |
| `feature` | Its `FeatureModule`, the typed contract with the form engine, declared as `FEATURE` at the end of its `service.py`, or `None`: a command with a form and no feature is saved by `GenericCogFeature`. A feature whose storage is not the plain `guild.cogs` document overrides `config_states`, the read of every server's configuration the reports use. |
| `hooks` | A `Hooks`: what it does on the Discord events every command shares. |
| `reminders` | Reminder title → sync handler, run on the Flask thread. |
| `webhooks` | Webhook event name (`<service>.<event>`) → sync handler, run on the Flask thread. |
| `opens` | What a button on another command's screen opens of this one: `async (interaction, source)`. |
| `group` | The parent slash group, a key of `PARENT_GROUPS` in `app/core/commands.py` (`moderations`, `notifications`, `integrations`, typed `ParentGroup`). |
| `on_setup`, `emoji` | Its row on /setup and the greeting; a command without `on_setup` is not offered there. |
| `category` | Where /setup places it, a key of `PARENT_GROUPS`; the `group` unless declared. A command on /setup needs one. |
| `channel_permissions`, `server_permissions`, `assigns_roles` | What Keiko needs for it, checked by the feature health line. |
| `lazy` | `True` for a command whose cogs load once Keiko is ready instead of at startup (admin, config). |

`Command` lives in `app/core/commands.py`, and it carries what the command's row on
/setup needs, so each command is declared once. Its copy and its form are not fields:
they are its folder's `copy.yml` and `form.yml`. One answer is derived, never
declared: `answers_prefix` (it has a `prefixed_message` hook).

## Hooks

The shared listeners in `app/core/events.py` call the hooks of every command, in the
order of `COMMANDS`. A hook is async and receives one frozen context; the Discord
objects in it are typed `Any`.

| Hook | Context | Runs |
| --- | --- | --- |
| `member_joined` | `MemberJoined(guild_id, user_id, member)` | By `join_stage`: `CHANGES_MEMBER` (roles) before `ANNOUNCES` (the default), then in the list order. |
| `message_sent` | `MessageSent(guild_id, user_id, channel_id, content, message, prefix, prefix_handled)` | On every message, direct messages included (`guild_id` is `None` there); `prefix_handled` is `True` once a prefix hook answered or failed, or the prefix could not be read. |
| `prefixed_message` | `MessageSent` | On a message that starts with the server's prefix; returns `True` when it answered; the first that answers or fails stops the rest. |
| `message_edited` | `MessageEdited(guild_id, channel_id, payload, client)` | On every edited message. |
| `guild_joined` | `GuildJoined(guild_id, guild)` | When a server adds Keiko, after the join is recorded. |
| `persistent_views` | none | On ready: the views Keiko keeps listening to after a restart. |

Reminder handlers take a `Reminder(id, title, notes)`; webhook handlers take a
`WebhookEvent(name, subject, payload)` and return a `WebhookReply(status, body)`.
Both run on the Flask thread, so the reminders retry and the service's redelivery see
their outcome.

## The registry

`app/core/commands.py` reads `app.commands.COMMANDS` lazily, once, and refuses a key,
a reminder title or a webhook event declared twice, a `group` or a `category` that is
no key of `PARENT_GROUPS` (the parent groups in /setup's order, each with its emoji),
and a command on /setup in no category. It imports only
the standard library, so the form engine may read it like the pure calendar.

- `commands()`: every command in the declared order.
- `command_for(key)`: one command; `KeyError` for an unknown key.
- `setup_features()`: the commands /setup and the greeting offer, in order.
- `setup_feature(key)`: the command /setup offers under `key`, or `None`.
- `member_hooks()`, `message_hooks()`, `prefix_hooks()`, `edit_hooks()`,
  `guild_join_hooks()`, `view_hooks()`: `(key, hook)` pairs in dispatch order.
- `reminder_handlers()`, `webhook_handlers()`: name → handler, one owner each.
- `opener(key)`: what the command `key` opens from another command's screen; how
  /help and /setup open each other without importing each other.
- `copy_file(key)` and `form_file(key)`: the `copy.yml` and the `form.yml` of the
  command `key`, or `None`; `copy_files()` and `form_files()` map every key to its
  file. The copy loader (`app/core/i18n.py`) reads each `copy.yml` whole, the form
  source (`DiskSource` in `app/forms/form/form_yaml.py`) each `form.yml` whole, both
  through `strict_yaml`, which refuses a YAML anchor or alias and a key written twice.

`app/core/utils.py` reads the cogs from it too: `command_extensions(command)` is the
module of every cog of a command that is no slash subgroup, each loaded as an
extension through its `setup(bot)`; `startup_extensions()` is the core cogs and the
extensions of every command that is not `lazy`; `lazy_commands()` load on ready;
`extension_paths(name)` turns a core cog or a command key (`/admin cogs reload help`)
into its modules; and `extension_names()` is what `/admin cogs` completes: the core
cogs, then every command with a cog that loads as an extension. A command whose cogs
are all subgroups is reached through `groups`, and reloading `groups` attaches the
subgroups again from the registry without importing their modules again.

`app/core/dispatch.py` runs them. Each async listener function takes the
`trace.listener` unit of work and runs every hook as its own part labelled by the
command's key, so a failing hook is logged and the next one still runs:
`member_joined`, `message_sent` (reads the prefix as the part `prefix` first, and a
prefix it cannot read skips the prefix hooks),
`message_edited`, `guild_joined`. `persistent_views()` gathers the views;
`reminder(fired)` and `webhook(event)` hand one item to its owner and say when no
command owns it.

## Where the data goes

Ask the seven questions of [docs/data-model.md](data-model.md#a-new-command-seven-questions)
in order. Each "yes" names the collection: what an admin configures goes to
`guild.cogs` through the form; what Keiko remembers about a member to
`guild.members`; what happens to `events.*` with its type in
`app/analytics/catalog.yml`. No collection is named after a command, and the command
reads them through the repositories of `app/data/`, passing its key.

## Copy and forms

- The command's copy is its `copy.yml`: every text is a node with its
  `en-us` and its `pt-br`, under a key relative to the command, read with
  `ml("<key>.<path>", locale)` (`ml("block_links.command.stats.empty", locale)`); a shared
  layer reads a command's fixed places with `command_text(command, path, locale)`. The
  slash names and descriptions live there too, under `command.slash`
  (`command.slash.name`, `command.slash.description`, `command.slash.subgroup`), and
  the cog names the block:
  `locale_str("links", type="name", namespace="block_links.command.slash")`. An error of
  the command is shown by its block: `response_error_embed("<key>.ui.errors.<name>",
  locale)`. Generic copy (buttons, shared responses, lists, texts and errors) stays
  in `app/languages/<namespace>.yml`, bilingual the same way. A text missing a locale,
  a key defined twice, a YAML anchor or alias and a file its schema refuses fail at
  load, in a `copy.yml` and a `form.yml` alike.
- Every string exists in both locales, in Keiko's voice:
  `.claude/skills/keiko-writing-style/SKILL.md`.
- The form is its `form.yml`, YAML first (`docs/form-configuration.md`).
  It writes only what differs from the engine's defaults (the report footer on every
  step, the same `/report` line every embed closes with, the summary sentence and the
  intro's title on the summary) and names a text
  of `app/forms/vocabulary.yml` with `${ref.<name>}` instead of writing it again. The
  engine finds it, and the `FeatureModule`, through the registry.
- A value a text does not hold is a token of one syntax, the same in every file
  (below, "The token syntax").

## The token syntax

Every Keiko text (a `copy.yml`, a `form.yml`, `app/languages/*.yml`,
`app/forms/vocabulary.yml`) writes a value it does not hold as a token, read by one
reader, `app/core/tokens.py`:

| written | what it is |
| --- | --- |
| `$count` | a fact the command's code passes; the name is read whole, up to the first character that is not a letter, digit or `_` |
| `${answer.streamer}` | an answer the admin gave in the form; `${answer.link:host}` with a format, `${answer.link:host\|youtube.com}` with a fallback, `${answer.x\|fallback}` |
| `${plural.default_roles\|Role to give\|Roles to give}` | one or many, from an answer |
| `${context.commands.setup}` | a value Keiko knows (the `context:` of `app/core/global.yml`: command and group names, server name, timezone, prefix...) |
| `${ref.support_footer}` | a shared text of `app/forms/vocabulary.yml`; a field written only as `${ref.x}` takes that text in both locales, and inside a text it writes the text of that locale |
| `${prefix}ping` | braces also mark where a name ends, to glue text |
| `$$` | a literal dollar |
| `{user}` | braces alone stay the admin's own variables in the messages admins write (welcome, birthdays, notifications), filled by `fill_placeholders` as before |

The kinds are closed (`answer`, `plural`, `context`, `ref`), and `$name` or `${name}`
is always a fact. A `$` before anything else (`R$ 5`, `$5`) is text.

- When each is read: as the file loads, `${ref...}` and the command and group
  names of `${context.commands.<key>}` and `${context.groups.<group>}`
  (`with_context`, `app/core/facts.py`); as the code draws the text, the facts
  (`fill`, `filled`, the primitives below); as a form draws a screen, the answers,
  the plurals and the rest of the context (`apply_tokens`). Each pass reads a text
  once, so a value it puts in is never read again: an answer an admin typed as
  `${context.prefix}` or `$count` shows as typed. A pass that leaves a token for a
  later one keeps each `$$` and doubles the `$` of what it put in; the pass that
  leaves no token writes `$$` as `$`.
- What is refused, naming the file, the text and the token, with the closest
  declared name: in a copy, a `$name` that is no fact of its command or `global` (a
  shared file of `app/languages` reads only global facts), a `${context...}` that is
  no command or group known at load, an answer; in a form (`compile_form`), a `$name`
  that is no global fact (the engine fills `$item`, `$count`, `$max` and
  `$feature`), an `${answer.x}` or `${plural.x...}` whose key no earlier step or the
  step itself produces (the rule a `when` follows), a format that is neither a value
  format nor `host`, a `${context.x}` `global.yml` does not declare; everywhere, a
  `${` never closed, an unknown kind, a `${ref.x}` the vocabulary does not hold, and
  the old spellings (`{context:...}`, `{answer:...}`, `{response:...}`,
  `{plural:...}`, the `{ref: name}` node), with the new one in the message:
  `app/commands/x/copy.yml: ui.texts.hint.en-us: $counts is no fact of x or global;
  did you mean ${count}s or $count?`.
- The admin's braces: Keiko's own templates (a card's `defaults`, a copy's
  `ui.announcement.defaults`, the variables a step explains, a modal's placeholder)
  name only the variables the command's code fills, and a default writes no token a
  later pass would fill (`tests/test_admin_templates.py`). A message an admin saved
  stays as tolerant on screen as before, an unknown `{name}` shown as typed; neither
  it nor a reply StreamElements sends (`$(touser)`, `${random.1-10}`) ever goes
  through Keiko's reader (`tests/core/test_tokens.py`).

## The YAML reference

`docs/yaml-reference.html` documents every YAML type Keiko reads, the way an API
reference does (`form.yml`, `vocabulary.yml`, `copy.yml`, each file of `app/languages/`,
`global.yml` and `catalog.yml`), in two views of one page, linked both ways. The Reference reads one
object at a time: its keys grouped by role, a child object or kind opening in place
under its parent with the path above it (`form.yml › steps[] › type: card ›
sections[]`), and a real file beside it that marks the key under the pointer. The Tree
shows the whole file as one tree, filtered by role and searched by path, with the
type, default, rules, open keys, a real example and the child keys of the node picked.
Open the file in a browser; EN / PT switches every text.

- It is generated from the pydantic root model each type names in `YAML_TYPES`
  (`app/core/commands.py`) and from the files of the type: run `make docs` after
  changing a schema or a YAML file (it writes `docs/index.html`, the index of every doc,
  with it). `tests/test_yaml_reference.py` fails when the page is stale, and when a field
  has no `Field(description=...)`.
- A field names its role in the schema, which the page groups and colors it by:
  `Field(description="...", json_schema_extra={"role": "copy"})`. `identity` names the
  item and its kind, `copy` is words Keiko shows, `visual` how and where it looks,
  `behavior` what the bot checks or does, `conditions` when and where it shows, `data`
  the answer and state keys it reads or writes, and `structure` the nested parts it
  holds (a child list, a screen, a button, a modal). The test fails on a field with no
  role (the page draws it under Other until then); an unknown role fails the generator.
- The Portuguese of every text is `scripts/keiko/yamldoc/translations.yml`, the
  generator's input beside its code, keyed by what it translates (`<type>` for the
  purpose, `<type>/<Model>`, `<type>/<Model>.<field>`) with the English it translated
  beside it. The test fails on a text with no translation, on a
  stale one (the English changed since) and on one whose field is gone; the page shows
  the English, marked, until the translation is current.
- The layout standard: a union item writes its `type` first, then the closed keys in
  schema order, then the open blocks (`Open[T]`), each under one comment line
  `# open: <what the keys are> -> <what each child is>`. A model whose keys are the
  blocks of a file in a chosen order declares `json_schema_extra={"in_order": True}`
  and writes every key in schema order, open or closed (`CommandCopy`, `CommandPlaces`,
  `CommandUi`). `tests/test_yaml_layout.py` checks every file of every type with a
  schema.
- The tests carry a list of what is not converted yet (`UNDESCRIBED`, `EXCEPTIONS`,
  and `UNTRANSLATED` for the types without Portuguese yet); all three are empty, and an
  entry that already passes fails the test, so they only shrink: a new file or field
  follows the standard from its first line.

## The copy schema and the primitives that read it

A `copy.yml` is `CommandCopy` (`app/core/copy_schema.py`), three closed groups in this
order: `facts` (below, "Overrides and facts"), the values its texts read; `command`
(`CommandPlaces`), the fixed places Keiko's shared screens fill, `slash`, `setup`,
`stats` and `panel`; and `ui` (`CommandUi`), what the command draws itself, one block
per primitive in the order `cards`, `texts`, `records`, `responses`, `errors`,
`buttons` and `announcement`, the open ones keyed as the command chooses. An address
follows the file: `<key>.facts.<name>`, `<key>.command.<place>...` and
`<key>.ui.<block>.<name>`.

```yaml
# app/commands/<key>/copy.yml
facts: ...        # the values a rule or a $name reads
command:          # the fixed places the shared screens fill
  slash: ...
  setup: ...
  stats: ...
  panel: ...
ui:               # what the command draws itself
  cards: ...
  texts: ...
  records: ...
  responses: ...
  errors: ...
  buttons: ...
  announcement: ...
```

Each block's model lives beside the primitive that reads it, and every
field says in its description what it makes the bot do. Every key is snake_case, every
text is a `Text` (`app/core/commands.py`, the one `{en-us, pt-br}` node forms use too),
a value the code fills is written `$name`, a fact the command's `facts:` or
`app/core/global.yml` declares (a `{name}` is refused, braces belong to the
templates an admin writes; `fill` refuses a line that names a given value in braces),
and `${ref.<name>}` writes a text of `app/forms/vocabulary.yml`, as in a form. A command
with cogs writes `command.slash`; one /setup offers writes `command.setup` and
`command.stats`. The shared files
of `app/languages` have their own schemas (`SHARED_FILES`): `buttons.yml` holds only
buttons (`ButtonCopy`), `errors.yml` only error blocks, `texts.yml` the shared lines
and tables (months, parent groups, notices, permission lines), `locales.yml` one name
per language code, and `commands.yml` the shared `responses`
(`commands.responses.admin_only`, `cooldown`, `help`, the lifecycle answers `enabled`,
`paused`, `unpaused`, `disabled`, the member pickers `edit_member`, `search_member`, `remove_member`, and the
`remove` and `discard` questions) and `records` (`commands.records.history`, a
feature's History, and `commands.records.changes`, every change of a server). Every
copy file is read against its schema at load, and `i18n.block(key, Model)` returns a
typed block (or raises when no copy file writes it). `description` names a block's
body of text (an embed's, a list's, a card's, a slash command's or a button's caption),
`value` the text a field shows, and an error's text keeps `message`. The standard
`/report` line is written once, as `fallbacks.footer` of `app/forms/vocabulary.yml`:
`standard_footer(locale)` (`app/ui/embed.py`) reads it there for every embed and card
that writes no footer, as every form step does.

| Block | Model | Read by | What it draws |
| --- | --- | --- | --- |
| `facts.<name>` | `FactSpec` | the copy loader, `resolved` (`app/core/i18n.py`) | nothing: what a rule or a `$name` of the command reads, with its type |
| `command.slash` | `Slash` | the `Translator` (`app/translator.py`) | the app commands' names, captions and parameters |
| `command.setup` | `SetupRow` | `command_text(command, "command.setup.label")` | the row on /setup and the greeting |
| `command.stats` | `Stats` | `send_feature_stats`, `figure_lines`, `health_line` | the statistics embed and the recent-activity line (`command.stats.delivered.one`, `command.stats.delivered.other`) |
| `command.panel` | `Panel` | `GenericCogFeature` (`panel_info`, `panel_info_copy`) and the feature's own panel | `command.panel.info`, `.pages`, `.rows`, `.authors` |
| `ui.cards.<name>` | `Card` | `card_copy`, then `card_header`, `card_row`, `card_footer` | a Components V2 card |
| `ui.texts.<name>` | `CopyText`, or one level of table | the command's own code | what no primitive draws |
| `ui.records.<name>` | `Records` | `RecordsBrowser(copy=...)` | a browsable list, its empty state and its member filter |
| `ui.responses.<name>` | `Response` | `response_embed`, `block_texts` (the form engine) | an answer, a confirmation question, an aside's result |
| `ui.errors.<name>` | `ErrorBlock` | `response_error_embed`, `error_message`, a form's `error:` | a red error embed, or the plain line of a form |
| `ui.buttons.<name>` | `ButtonCopy` | `panel_asides`, `panel_button`, the command's views | a button's label and its Help caption (`description`) |
| `ui.announcement` | `Announcement` | `announcement_embed` and the command | `fields`, `states`, `samples`, `defaults` of what it posts in a channel |

- `response_embed("<key>.ui.responses.<name>", locale, facts=..., fields=...,
  empty=..., thumbnail=..., inline=...)` reads a `Response` block: the item of its
  `overrides` (and of each field's) the `facts` choose replaces what it writes, its
  `$name`s are filled from the `facts`, `empty` draws `empty`, and `fields` gives the
  value of each `fields.<field>` (drawn in the order the copy writes them; one with no
  value draws its own `value`, as its overrides choose it, or `empty`, or is left out). An absent `footer` closes the embed with the standard `/report` line, a
  `footer: null` with none, a text with that text, always as `-# • ` subtext so its
  markdown renders, and always as the last line: `with_footer` draws it at the end of
  the description, or in a full-width field of its own under the fields when the embed
  has some. The copy decides the footer; the call has no flag for it.
- `response_error_embed("<key>.ui.errors.<name>", locale, facts=...)` and, on the forms'
  plain path, `error_message("<key>.ui.errors.<name>", args, locale)` both take the
  `ErrorBlock` key, never its `.message` leaf (a short name is a block of
  `errors.yml`); the embed adds 🚨 and the standard line.
- `RecordsBrowser(fetch, to_fields, copy="<key>.ui.records.<name>", facts=...,
  locale=...)` reads one `Records` block: its `title` and `description` above the
  page, `empty` alone when nothing is found (without one, the empty page is sent), and
  `filter` for the member filter button and its picker; `lines` holds the line
  templates the formatter fills. Its texts speak `locale` when given (a form's History
  speaks the session's language), else the language of each interaction. `copy` is
  required: the browser takes no title or description of its own.
- A feature's panel: `GenericCogFeature.panel_asides = {"<aside>": "<emoji>"}` draws one
  panel button per aside from `ui.buttons.<aside>` (`ButtonCopy`, label and caption);
  `panel_button(aside, emoji, locale, copy="buttons.preview")` reads a shared button
  block instead. `panel_info` and `panel_info_title` read `command.panel.info` of the `Panel`
  block on every panel, and `panel_info_copy(locale, facts)` gives the title and
  description with the overrides its facts choose and its `$name`s filled, for a feature
  that fills them itself: one whose `command.panel.info` reads facts declares
  `fills_panel_info = True`, and the generic panel leaves the paragraph to it (default
  roles, StreamElements).
- `announcement_embed(title, message, footer, image, copy="<key>.ui.announcement",
  locale=..., fields=..., url=..., colour=..., thumbnail=...)` posts what a command
  announces: each of `fields` is an inline field labelled by `fields.<name>` of the
  `Announcement` block, left out while its value is empty; its `states`, `samples` and
  `defaults` (an admin template, braces allowed) are read by the command.
- Statistics: `figure_lines(key, [Figure(name, emoji, value), ...], locale)` draws each
  figure as `emoji **label:** value` with the label from `command.stats.figures.<name>`
  of the `Stats` block, or `command.stats.empty` alone while there is no figure; a
  feature's `stats_lines` returns them. `send_feature_stats` titles the embed with
  `command.stats.title` and `health_line` reads `command.stats.delivered`, a `Plural`
  (`one` for a single delivery,
  `other` for any other count).
- `command.slash` (`Slash`) is read by the `Translator` through `locale_str(namespace=
  "<key>.command.slash")`; the folder's other app commands use the namespace
  `<key>.command.slash.commands.<name>` and its parameters the type `params.<name>.name`.
- `command.setup` (`SetupRow`) is read through
  `command_text(command, "command.setup.label")`.
- `ui.cards` (`Card`) are drawn by the command through `card_copy("<key>.ui.cards.<name>",
  locale, facts)`, which gives the title, intro, note and closing line of the block
  with its overrides chosen and its `$name`s filled, then `card_header`, `card_row` and
  `card_footer`; `card_footer_text(card, locale)` applies the same footer rule as a
  response. A card's `rows`, like a block's `lines`, are templates the command fills
  one by one.
- `ui.texts` holds the texts no primitive draws, read by the command's own code: a
  plain chat line, a badge, or one level of table keyed by a closed vocabulary in code.

## Overrides and facts

A text that changes with the state is written in the YAML, never picked in code. The
block keeps its default fields and an `overrides` list; each item is a `when` rule
(the grammar of the forms, `docs/form-configuration.md` section 4: `is`, `in`,
`not_in`, `matches`, `present`, `count`, `all`, `any`, `not`) and the fields it
replaces. The first item whose rule holds wins and replaces only the fields it writes;
when none holds, the block draws its defaults. A `Response` (and each of its
`fields`), a `Card` and a `PanelInfo` take `overrides`, and a form's steps and groups
take the same list for their title and description. `lines` that are templates
repeated or shown together (a category line of the greeting, a History line, the link
check's exemption and link lines) stay `lines`, filled one by one by the code.

```yaml
# app/commands/block_links/copy.yml
ui:
  responses:
    check:
      title:
        en-us: ✅ I would let this message through
        pt-br: ✅ Eu deixaria essa mensagem passar
      overrides:
        - when:
            key: block_links.facts.would_block
            is: true
          title:
            en-us: 🚫 I would block this message
            pt-br: 🚫 Eu bloquearia essa mensagem
```

A value a rule or a text reads is a **fact**, addressed `<owner>.facts.<name>`: the
owner is a command's key (`block_links.facts.would_block`) or `global`
(`global.facts.feature_state`). A command's facts are declared in the `facts:` of its
`copy.yml`, each with its `type` (`boolean`, `enum` with its `values`, `number`,
`text`) and a one-sentence `description`; the facts no single command owns live in
`app/core/global.yml` (`GlobalCatalog` in `app/core/facts.py`, registered in
`YAML_TYPES`). A fact starts in its command and moves to `global` only when a second
command needs it, and no command is keyed `global` (the registry refuses it).

```yaml
# open: fact names -> a fact the code passes
facts:
  would_block:
    type: boolean
    description: Whether Keiko would block the checked message.
```

- A rule in copy names a fact by its full address, of its own command or `global`;
  a rule in a form keeps reading the form's own answers.
- A `$name` in copy is a fact too: the command's own or a global one by its short
  name, read whole (`$prefixping` is no fact; `${prefix}ping` glues `ping` to
  `$prefix`), and a shared file of `app/languages` reads only global facts. A short
  name declared by a command and by `global` is refused.
- At load, the copy loader refuses a rule on an unknown fact or on another command's,
  a value its type refuses (`'oval' is none of ['round', 'square']`), an operator a
  boolean has no use for, an undeclared `$name` and a fact declared twice, naming the
  file, the block and the key (`app/commands/x/copy.yml:
  ui.responses.check.overrides[0].when: x.facts.gone: no such fact in x`), and every
  token the syntax or the catalogs refuse (above, "The token syntax").
- At render, the primitive (`response_embed`, `response_error_embed`, `card_copy`,
  `panel_info_copy`, `RecordsBrowser(copy=)`, `block_texts`) checks that every fact
  the block reads (the keys of its rules and its `$name`s, its `lines` and `rows` left
  out) was passed and that no fact neither its command nor `global` declares was
  passed (`i18n.resolved`).
- The code passes facts through its command's helper, `command_facts(**values)` at
  the top of its `service.py` (`facts("<key>", **values)`, which addresses a global
  name to `global` on its own), or `facts(GLOBAL, ...)` in a shared layer; never a
  state name. `tests/test_facts.py` walks every call of the primitives and of those
  helpers and refuses a name no catalog declares, and a call on a literal block that
  does not pass every fact the block reads.

### Context

Values Keiko already holds in memory or in its cache are the **context**, declared
once in the `context:` of `app/core/global.yml` (`type`, `description`, and `public`:
whether a message every member sees may show it), and read in a copy or a form text as
`${context.<name>}`. A family (`each: command` or `each: group`) is written in when the
file loads, in each locale: `${context.commands.setup}` reads `/setup` or
`/configurar`, `${context.commands.block_links}` `/moderations block links`, and
`${context.groups.moderations}` `/moderations`, from the command's own `command.slash` copy and
`texts.groups`. Copy reads only those families for now; a form also reads the
entries the adapter fills (`server_name`, `prefix`, `server_language`,
`server_timezone`, `command`, and a feature's `Opened.values`), and its compiler
refuses an undeclared one. A command or a group a text names is never typed in.

## Rule 22: the shared layers never name a command

A shared module takes the key, the field or the kind as a parameter. A step one
command needs is a field of its `Command`, a hook, or a member of its
`FeatureModule`, never an `if` on its key. The command's own rule stays in its
folder (`.claude/rules/code-style.md`, rule 22).

## Two commands to copy from

- `app/commands/birthdays/`: two cogs (the admin's and the member's), a form, the
  member's birthday in `guild.members`, a reminder per date in `keiko.schedules`,
  and the `birthday_reminder` title in `reminders`.
- `app/commands/block_links/`: a subgroup of `/moderations` and a context menu (its own
  cog, `LinkCheck`), a form, the `message_sent` and `message_edited` hooks, and a
  `link.blocked` event per removed message.

## What the tests tell you

| test | fails when |
|---|---|
| `tests/test_commands.py` | a folder has no `COMMAND`, the list misses a folder or lists one twice, two commands share a key, a YAML other than `copy.yml` and `form.yml` lies in the folder, its form does not compile, an `ml("...")` key is missing in a locale, an `analytics.record("...")` event is not in the catalog, a reminder title or a webhook event has two owners, the module of a cog that loads as an extension has no `setup(bot)`, or its `setup(bot)` never adds a cog the command declares, a `command_for`, `opener` or `setup_feature` literal anywhere in `app/` names no command that answers it |
| `tests/forms/features/test_features.py` | a folder holds a `feature.py`, or the `FEATURE` of a service is not the `feature` its command declares |
| `tests/test_structure.py` | a command imports another command or a module outside the shared layers, a shared layer imports a command, the form engine imports `discord` outside `app/forms/discord`, `app/data` imports more than itself and the constants |
| `tests/test_generic_layers.py` | a shared layer names a command (rule 22) |
| `tests/test_facts.py` | a call passes a fact no catalog declares, or calls a primitive on a literal block without every fact the block reads (rule 28) |
| `tests/test_admin_templates.py` | a `{name}` the command's copy or form writes is no variable its code's `fill_placeholders` fills, or a default writes a token a later pass would fill (rule 29) |
| `tests/commands/<name>/` | the command's own behaviour |

Each failure names the file, the line and what to add or move. An exception to the
import contracts goes in `EXCEPTIONS` of `tests/test_structure.py` with its reason,
and it fails once it is no longer needed.
