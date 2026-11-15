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
in its own `copy.yml`, at the fixed keys the shared layers read: `setup.label`,
`setup.description`, `stats.title`, `stats.delivered.one`, `stats.delivered.other`,
and `slash.subgroup` and `slash.name` of its slash command
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
  `ml("<key>.<path>", locale)` (`ml("block_links.stats.empty", locale)`); a shared
  layer reads a command's fixed keys with `command_text(command, path, locale)`. The
  slash names and descriptions live there too, under `slash` (`slash.name`,
  `slash.description`, `slash.subgroup`), and the cog names the block:
  `locale_str("links", type="name", namespace="block_links.slash")`. An error of
  the command is shown by its block: `response_error_embed("<key>.errors.<name>",
  locale)`. Generic copy (buttons, shared responses, lists, messages and errors) stays
  in `app/languages/<namespace>.yml`, bilingual the same way. A text missing a locale,
  a key defined twice, a YAML anchor or alias and a file its schema refuses fail at
  load, in a `copy.yml` and a `form.yml` alike.
- Every string exists in both locales, in Keiko's voice:
  `.claude/skills/keiko-writing-style/SKILL.md`.
- The form is its `form.yml`, YAML first (`docs/form-configuration.md`).
  It writes only what differs from the engine's defaults (the report footer on every
  step, the same `/report` line every embed closes with, the summary sentence and the
  intro's title on the summary) and names a text
  of `app/forms/vocabulary.yml` with `{ref: name}` instead of writing it again. The
  engine finds it, and the `FeatureModule`, through the registry.

## The YAML reference

`docs/yaml/index.html` documents every YAML type Keiko reads, the way an API reference
does (`form.yml`, `vocabulary.yml`, `copy.yml`, each file of `app/languages/` and
`catalog.yml`), in two views of one page, linked both ways. The Reference reads one
object at a time: its keys grouped by role, a child object or kind opening in place
under its parent with the path above it (`form.yml › steps[] › type: card ›
sections[]`), and a real file beside it that marks the key under the pointer. The Tree
shows the whole file as one tree, filtered by role and searched by path, with the
type, default, rules, open keys, a real example and the child keys of the node picked.
Open the file in a browser; EN / PT switches every text.

- It is generated from the pydantic root model each type names in `YAML_TYPES`
  (`app/core/commands.py`) and from the files of the type: run `make yaml-docs` after
  changing a schema or a YAML file. `tests/test_yaml_reference.py` fails when the page
  is stale, and when a field has no `Field(description=...)`.
- A field names its role in the schema, which the page groups and colors it by:
  `Field(description="...", json_schema_extra={"role": "copy"})`. `identity` names the
  item and its kind, `copy` is words Keiko shows, `visual` how and where it looks,
  `behavior` what the bot checks or does, `conditions` when and where it shows, `data`
  the answer and state keys it reads or writes, and `structure` the nested parts it
  holds (a child list, a screen, a button, a modal). The test fails on a field with no
  role (the page draws it under Other until then); an unknown role fails the generator.
- The Portuguese of every text is `docs/yaml/pt-br.yml`, keyed by what it translates
  (`<type>` for the purpose, `<type>/<Model>`, `<type>/<Model>.<field>`) with the
  English it translated beside it. The test fails on a text with no translation, on a
  stale one (the English changed since) and on one whose field is gone; the page shows
  the English, marked, until the translation is current.
- The layout standard: a union item writes its `type` first, then the closed keys in
  schema order, then the open blocks (`Open[T]`), each under one comment line
  `# open: <what the keys are> -> <what each child is>`. `tests/test_yaml_layout.py`
  checks every file of every type with a schema.
- The tests carry a list of what is not converted yet (`UNDESCRIBED`, `EXCEPTIONS`,
  and `UNTRANSLATED` for the types without Portuguese yet); all three are empty, and an
  entry that already passes fails the test, so they only shrink: a new file or field
  follows the standard from its first line.

## The copy schema and the primitives that read it

A `copy.yml` is `CommandCopy` (`app/core/copy_schema.py`): a closed top level of one
block per primitive that draws its texts, `slash`, `setup`, `stats`, `panel` and
`announcement` (closed blocks, written first, in that order), then the open ones,
`errors`, `responses`, `records`, `buttons`, `cards` and `messages`, whose keys the
command chooses. Each block's model lives beside the primitive that reads it, and every
field says in its description what it makes the bot do. Every key is snake_case, every
text is a `Text` (`app/core/commands.py`, the one `{en-us, pt-br}` node forms use too),
a value the code fills is written `$name` (a `{name}` is refused, braces belong to the
templates an admin writes; `fill` refuses a line that names a given value in braces),
and `{ref: name}` writes a text of `app/forms/vocabulary.yml`, as in a form. A command
with cogs writes `slash`; one /setup offers writes `setup` and `stats`. The shared files
of `app/languages` have their own schemas (`SHARED_FILES`): `buttons.yml` holds only
buttons (`ButtonCopy`), `errors.yml` only error blocks, `messages.yml` the shared lines
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
| `slash` | `Slash` | the `Translator` (`app/translator.py`) | the app commands' names, captions and parameters |
| `setup` | `SetupRow` | `command_text(command, "setup.label")` | the row on /setup and the greeting |
| `stats` | `Stats` | `send_feature_stats`, `figure_lines`, `health_line` | the statistics embed and the recent-activity line (`stats.delivered.one`, `stats.delivered.other`) |
| `panel` | `Panel` | `GenericCogFeature` (`panel_info`, `panel_info_copy`) and the feature's own panel | `panel.info`, `panel.pages`, `panel.rows`, `panel.authors` |
| `announcement` | `Announcement` | `announcement_embed` and the command | `fields`, `states`, `samples`, `defaults` of what it posts in a channel |
| `errors.<name>` | `ErrorBlock` | `response_error_embed`, `error_message`, a form's `error:` | a red error embed, or the plain line of a form |
| `responses.<name>` | `Response` | `response_embed` | an answer, a confirmation question, an aside's result |
| `records.<name>` | `Records` | `RecordsBrowser(copy=...)` | a browsable list, its empty state and its member filter |
| `buttons.<name>` | `ButtonCopy` | `panel_asides`, `panel_button`, the command's views | a button's label and its Help caption (`description`) |
| `cards.<name>` | `Card` | `card_header`, `card_row`, `card_footer_text` | a Components V2 card |
| `messages.<name>` | `CopyText`, or one level of table | the command's own code | what no primitive draws |

- `response_embed("<key>.responses.<name>", locale, values=..., fields=...,
  variant=..., empty=..., thumbnail=..., inline=...)` reads a `Response` block: `$name`s
  are filled from `values`, `variant` draws `variants.<variant>` in place of the title
  or description, `empty` draws `empty`, and `fields` gives the value of each
  `fields.<field>` (drawn in the order the copy writes them; one with no value draws its
  own `value` or `empty`, or is left out). An absent `footer` closes the embed with the standard `/report` line, a
  `footer: null` with none, a text with that text, always as `-# • ` subtext so its
  markdown renders, and always as the last line: `with_footer` draws it at the end of
  the description, or in a full-width field of its own under the fields when the embed
  has some. The copy decides the footer; the call has no flag for it.
- `response_error_embed("<key>.errors.<name>", locale, values=...)` and, on the forms'
  plain path, `error_message("<key>.errors.<name>", args, locale)` both take the
  `ErrorBlock` key, never its `.message` leaf (a short name is a block of
  `errors.yml`); the embed adds 🚨 and the standard line.
- `RecordsBrowser(fetch, to_fields, copy="<key>.records.<name>", values=...,
  locale=...)` reads one `Records` block: its `title` and `description` above the
  page, `empty` alone when nothing is found (without one, the empty page is sent), and
  `filter` for the member filter button and its picker; `lines` holds the line
  templates the formatter fills. Its texts speak `locale` when given (a form's History
  speaks the session's language), else the language of each interaction. `copy` is
  required: the browser takes no title or description of its own.
- A feature's panel: `GenericCogFeature.panel_asides = {"<aside>": "<emoji>"}` draws one
  panel button per aside from `buttons.<aside>` (`ButtonCopy`, label and caption);
  `panel_button(aside, emoji, locale, copy="buttons.preview")` reads a shared button
  block instead. `panel_info` and `panel_info_title` read `panel.info` of the `Panel`
  block on every panel, and `panel_info_copy(locale, variant=..., values=...)` gives
  the title and description with a variant and values for a feature that fills them
  itself.
- `announcement_embed(title, message, footer, image, copy="<key>.announcement",
  locale=..., fields=..., url=..., colour=..., thumbnail=...)` posts what a command
  announces: each of `fields` is an inline field labelled by `fields.<name>` of the
  `Announcement` block, left out while its value is empty; its `states`, `samples` and
  `defaults` (an admin template, braces allowed) are read by the command.
- Statistics: `figure_lines(key, [Figure(name, emoji, value), ...], locale)` draws each
  figure as `emoji **label:** value` with the label from `stats.figures.<name>` of the
  `Stats` block, or `stats.empty` alone while there is no figure; a feature's
  `stats_lines` returns them. `send_feature_stats` titles the embed with `stats.title`
  and `health_line` reads `stats.delivered`, a `Plural` (`one` for a single delivery,
  `other` for any other count).
- `slash` (`Slash`) is read by the `Translator` through `locale_str(namespace=
  "<key>.slash")`; the folder's other app commands use the namespace
  `<key>.slash.commands.<name>` and its parameters the type `params.<name>.name`.
- `setup` (`SetupRow`) is read through `command_text(command, "setup.label")`.
- `cards` (`Card`) are drawn by the command through `card_header`, `card_row` and
  `card_footer`; `card_footer_text(card, locale)` applies the same footer rule as a
  response.
- `messages` holds the texts no primitive draws, read by the command's own code: a
  plain chat line, a badge, or one level of table keyed by a closed vocabulary in code.

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
| `tests/commands/<name>/` | the command's own behaviour |

Each failure names the file, the line and what to add or move. An exception to the
import contracts goes in `EXCEPTIONS` of `tests/test_structure.py` with its reason,
and it fails once it is no longer needed.
