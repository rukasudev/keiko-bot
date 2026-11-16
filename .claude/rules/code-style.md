# Code style rules

Patterns established through PR review. Every one of these was pointed out by the
maintainer on real code; do not reintroduce them.

## 1. No narrative comments in code

Do not write comments that explain design rationale, history ("this used to break
because..."), or anything the code already says. Multi-line comment blocks are never
acceptable. Design rationale belongs in `docs/`, the commit message, or the PR
description — not in the source.

```python
# WRONG
# Never clear_items() here: the ViewStore reads `item.view` live, so
# clearing a view whose message is still on screen kills every button
# the user can see. The transition below replaces the message.
parent_view = self.view

# RIGHT
parent_view = self.view
```

The rare fact that is essential and impossible to express in code may keep ONE short
line. Short docstrings on public modules/classes/functions are fine; docstrings never
appear mid-function.

## 2. Magic values become constants in `app/constants.py`

Never define module-level constants in `app/ui/` or a command's folder. A shared or
tunable value (a Discord limit, a window in seconds, the blank name of a field that
carries only its value) goes to the matching class in `app/constants.py`
(`DiscordLimits`, `Commands`, `Style`...: `Style.BLANK_FIELD_NAME`).

## 3. No hyper-specific constant explosions

A constant exists for a genuinely shared or tunable value — not to rename every domain
literal. Closed vocabularies (enum values stored in documents, i18n key suffixes like
`"block_all"`, `"role-exempt"`) are used as literals where they occur. A command's key
is a literal in its own `COMMAND = Command(key=...)`, and shared code receives it as a
parameter: no key constant in `app/constants.py`. Field-name `*_KEY` constants follow
the pre-existing project pattern and are fine.

## 4. No module-level "constants" in a command's modules

i18n namespaces and similar strings live inline in the helper that uses them, not as
module-level names in `app/commands/<command>/service.py` or its cog.

## 5. `app/data/` is one repository class per collection, reached through `data()`

Each collection has one small class, built over its collection by `app.data.build` at
startup; its methods are the collection's generic operations, all `async`, and a caller
awaits them through the installed registry: `await data().guild_cogs.find_cogs_by_guild(
guild_id)`. No module-level client, no `DATABASE`/`COLLECTION` constants, no
`_collection()` helper, no alternative database names. A method returns the document
the app reads and takes what the app writes; the stored shape (`options`, `state`) stays
inside the file. A thread job that cannot be a coroutine waits on a method through the
client's loop (`wait(...)`, `iterate(...)`); nothing outside `app/migrations` and the
scripts imports the sync pymongo client.

## 6. Data-layer files are named after their collection

`app/data/<database>/<collection>.py` holds the repository of that one collection
(`Cogs` in `app/data/guild/cogs.py` for `guild.cogs`, reached as `data().guild_cogs`;
`Settings` in `app/data/keiko/settings.py` for `keiko.settings`, reached as
`data().keiko_settings`), whichever command asks. A query that only one command sends
keeps its paths in that command's `service.py`, and calls a generic method of the
collection's repository (`data().guild_cogs.find_cogs_by_option`). The repositories of
one database's collections that share their reads inherit them from one base in the
database's package (`EventCollection` in `app/data/events/__init__.py`). `logs.py`
(`guild.logs`, `guild.logs_files`) keeps its name until a later pull request moves
those collections.

## 7. `app/ui/` is generic infrastructure only

Files in `app/ui/` must be generic and reusable across commands. A screen that belongs
to one command lives in that command's `service.py` and composes the generic views
(`PaginationView`, `RecordsBrowser`...).

## 8. Redis key constants carry a `REDIS_` prefix

When a Redis key template lives in `app/constants.py`, its NAME starts with `REDIS_`
(or `CACHE_`), and the key starts with the prefix of its role
(`docs/data-model.md`): `cache:` for a copy of a document, as
`DBConfigs.CACHE_COG_KEY` (`cache:cog:{guild_id}:{cog}`). The `guild:{guild_id}:...`
keys of before are retired: `python -m app.migrations redis-cleanup` unlinks them.

## 9. Database changes are versioned in `app/migrations`

A change to the database's structure (a collection, an index) is a file in
`app/migrations/schema/`; a change to its data is a file in `app/migrations/data/`.
Each folder is numbered on its own (`0001_slug.py`, no gap), and every pending schema
file runs before every pending data file. A release only adds; dropping what the old
version reads comes in the next release. A data file writes inside one
`with begin(db) as tx:` block. The release deploy runs them (`docs/migrations.md`). No
one-off script outside them, no index created at boot.

## 10. Reusable orchestration becomes a generic view

Rule 7 says where a command's screen lives; this rule says what must be extracted
from it. When a screen's structure could serve another command — fetch records →
format → paginate/filter, an interaction round trip, an embed skeleton — the
orchestration becomes (or extends) a generic primitive (`RecordsBrowser`,
`PaginationView`, `ReportBrowser` in `app/ui/`; `base_embed` and the card frame in
`app/ui/embed.py`), and the command's service keeps only the fetch, the formatter, and
the copy. The smell: near-identical screen functions accumulating across
`app/commands/*/service.py`.

## 11. A command's service holds only its domain logic

A helper with no command-specific content (URL parsing, timestamp formatting,
envelope unwrapping) does not live in a command's folder — it goes to the generic
home: `app/core/utils.py`, a focused module like `app/core/dates.py`, or
`app/ui/embed.py` for embeds. What is left in `service.py` may be wholly specific to
its command (rule 22). Absolute red flag: a shared layer (a validator, a transform,
the form engine, a core module) importing from a command's folder — that inversion
means the helper is in the wrong module (rule 24).

## 12. View/UI tunables are global

Timeouts, cooldowns, notice lifetimes, and preview limits live in `ViewConstants`
(`app/constants.py`), and every view/modal reads them from there — never a literal,
never a command-prefixed constant. Domain numerics (a retention TTL, a per-message
cap) keep the command's prefix in `Commands`. Refines rules 2-3: a UI tunable is
always "genuinely shared".

## 13. The typed layers are typed, linted and silent

`make lint` runs `ruff` (format plus `E`, `F`, `I`, `D1xx` on public names, `C901`,
`ARG`, `RET`) at 88 columns, with two blank lines between top-level definitions, over
`app/forms/`, `app/data/`, `app/migrations/`, the whole of `app/commands/`, the
registry, the `when` grammar, the facts, dispatch, groups, copy loader, copy schema and
calendar of `app/core/` (`commands.py`, `conditions.py`, `facts.py`, `dispatch.py`,
`groups.py`, `i18n.py`, `copy_schema.py`, `calendar.py`), `app/decorators.py`, `app/types/cogs.py`, the pagination, embeds and
records browser of `app/ui/` (`pagination.py`, `embed.py`, `records.py`),
`tests/forms/` and `tests/core/`, and `ruff check --select F401,F821` over all of
`app/`. `mypy --strict` checks the files listed in `pyproject.toml`: the same app
modules, of `app/migrations/` its package, runner, helpers, Redis keys and CLI. A
module typed once joins both lists.
Rule 1 hardens in `app/forms/`: no comment block longer than one line, and that one
line only states an external constraint. `tests/forms/test_boundary.py` fails the
build otherwise.

## 14. One-sentence docstrings, public names only

Every public module, class and function `make lint` checks carries a docstring of one
sentence that says what it is or does. Private helpers carry none. A docstring never
narrates history, alternatives or rationale.

## 15. No reflection in the form engine

`hasattr`, `getattr`, `isinstance` ladders on duck types and `__getattr__` proxies do
not appear in `app/forms/form/` or `app/forms/features/`. The only exception is the
Discord adapter (`app/forms/discord/`) reading `discord.py` objects, where the
library's own surface is the boundary. `tests/forms/test_boundary.py` refuses
`hasattr(` and `getattr(` anywhere in `app/forms/` outside the adapter.

## 16. Typed boundaries

Nothing public in `app/forms/` accepts or returns `Dict[str, Any]` as its contract:
definitions are pydantic models, sessions and effects are frozen dataclasses, and a
command exchanges `Answer` mappings and documents through the `FeatureModule`
protocol. `Mapping[str, Any]` is allowed only where the value is a persisted document
by definition. The registry's contexts (`MemberJoined`, `MessageSent`...) are frozen
dataclasses too, with the Discord objects typed `Any`.

## 17. The form engine never names a command

No `command_key ==` branch, no command key literal, no import of `app.commands`
anywhere under `app/forms/`. The engine finds a command's form and `FeatureModule`
through the registry (`command_for(key)`); a behaviour that one command needs is a
registered extension or a member of its `FeatureModule`. `tests/test_generic_layers.py`
refuses the key literal, `tests/forms/test_boundary.py` the `command_key` name and
`tests/test_structure.py` the import. Rule 22 extends this to every shared layer.

## 18. Nothing in `app/forms/` blocks the loop

No `requests`, `pymongo`, `time.sleep` or other synchronous I/O under `app/forms/`.
Feature modules await the async data layer (`data()`, `app/data/`) or hand legacy
synchronous calls to `app.core.threads.to_thread`, never `asyncio.to_thread`, whose
threads the loop keeps for its own host name lookups and TLS handshakes.

## 19. A name says what it holds

No one or two letter names, in a comprehension or anywhere else: `for index, option
in enumerate(section.options)`, not `for i, o in ...`. A field is named after what it
reports (`Outcome.succeeded`, never `Outcome.ok`). Real words of two letters that are
the name of the thing, like `id`, are fine. `tests/forms/test_boundary.py` refuses the
rest in `app/forms/`.

## 20. A blank line between the blocks of a function

A function that does two things separates them with a blank line: the setup from the
branch that reads it, the loop from what follows it. Section markers (`# --- opening`)
never appear: if a file needs headings to be read, it holds two contexts and should be
two files.

## 21. Functions tell a story, and files hold one context

A function reads as its named steps, each handling a real part of the data; a tiny
helper called from one place folds into the step that uses it, unless its name states
a rule the call site would not say (`_truthy`, `_same_text`, `_declared_order`). A
large file is fine when everything in it is one context: split a file by context,
never by size.

## 22. Shared layers never name a command

A module named after a collection or a capability serves every command the same way:
everything in `app/core/`, `app/forms/`, `app/ui/` and `app/integrations/`, and
`app/data/<database>/<collection>.py`. It takes the command's key, the field or the
kind as a parameter, and it holds no function, constant, literal, branch or import
that belongs to one command. An integration is named after its service (Twitch,
YouTube), never after the command that uses it.

Each command's rule lives in that command's folder, so it is found in one place:

- `app/commands/<command>/service.py`: its domain rules, the paths of its fields, its
  defaults and its screens, and, at its end, `FEATURE`, its `FeatureModule`: what its
  form saves and how it plugs into the form engine. A service may be wholly specific
  to its command.
- `app/commands/<command>/copy.yml` and `form.yml`: its copy, its /setup row
  included, and its form (rule 25).
- `app/commands/<command>/__init__.py`: what the shared layers need to know about it,
  in its `Command` (rule 26).

A shared step that one command needs is a parameter, a field or a hook of its
`Command`, or a member of its `FeatureModule` that the shared code calls, never an
`if` on its key.

```python
# WRONG: app/data/guild/members.py
class Members(Repository):
    async def find_birthdays_on(self, guild_id, mm_dd):
        return await self._collection.find(
            {"guild_id": guild_id, "options.birthday.date": mm_dd}
        ).to_list()

# RIGHT: app/data/guild/members.py takes the key
class Members(Repository):
    async def find_members_by_option(self, guild_id, cog, filters):
        ...

# and app/commands/birthdays/service.py holds the birthday's rule
async def birthdays_on(guild_id, mm_dd):
    return await data().guild_members.find_members_by_option(
        guild_id, "reminders_birthday", {"date": mm_dd}
    )
```

Before adding a function to a shared module, ask whether another command would call
it the same way. If not, it goes to the command's folder, and the shared module gains
a parameter. `tests/test_generic_layers.py` scans the layers its `GENERIC_LAYERS` lists
(`app/core/`, `app/ui/`, the form engine, the data repositories, the subscription base,
the reminders webhook, the Flask app and the migration runner) and refuses there a
command key (every key the registry declares but the platform words help, setup,
report and support), a constant that holds one, a feature word or an import of a
command's module; an exception it lists carries its reason, and one that no longer
matches fails. The list is empty: the copy loader names no copy file either, since
every copy file is read against its schema.

## 23. A command folder never imports another command

`app/commands/<command>/` imports the shared layers (`app/core`, `app/forms`, `app/ui`,
`app/integrations`, `app/data`) and the package root modules, never
`app.commands.<other>`. What two commands share moves to a shared layer, as the
subscription base of Twitch and YouTube in `app/forms/features/subscriptions.py`.
Reason: a command is read, changed and deleted as one folder. `tests/test_structure.py`
refuses the import.

## 24. Shared layers reach a command only through the registry

`app/core/commands.py` is the one module that reads `app.commands.COMMANDS`, inside a
function; every other shared module asks it (`commands()`, `command_for(key)`,
`setup_feature(key)`, `opener(key)`, the hook and handler lists) and never imports
`app.commands`. Discord events, reminders and webhooks reach a command through
`app/core/dispatch.py`: the shared listeners of `app/core/events.py` run each
command's hook as its own trace part labelled by its key, and a failed prefix hook
ends the prefix chain; a fired reminder goes to the command owning its title, and a
webhook event to the command that registered it. Reason: the shared code stays the
same when a command is added or removed. `tests/test_structure.py` refuses the
import.

## 25. A command's copy and form live in its folder's `copy.yml` and `form.yml`

`app/commands/<command>/copy.yml` holds the strings the command shows, and
`app/commands/<command>/form.yml` its form (`steps:`), only when the command has one,
so the folder listing shows which commands have a form. Copy is text the Python code
reads and a form is a flow the engine compiles: two files keep their edits and their
diffs apart. Nothing declares a path: the registry finds each file beside the package
whose `COMMAND` it is (`copy_file(key)` and `form_file(key)` in
`app/core/commands.py`), the copy loader reads the `copy.yml` whole and the form
source the `form.yml` whole, both through the same `strict_yaml`, and no other YAML
lives in the folder. Every text in both files is a `{en-us: ..., pt-br: ...}` node. A
copy key is snake_case and relative to the command, and the loader registers it under
the command's key (`block_links.command.stats.title`). A `copy.yml` is a `CommandCopy`
(`app/core/copy_schema.py`) of three groups, in this order: `facts` (rule 28);
`command`, the fixed places Keiko's shared screens fill (`slash`, `setup`, `stats`,
`panel`); and `ui`, what the command draws itself, one block per primitive (`cards`,
`texts` for what no primitive draws, `records`, `responses`, `errors`, `buttons`,
`announcement`). An address follows the file (`<key>.facts.<name>`,
`<key>.command.stats.title`, `<key>.ui.responses.<name>`); `docs/commands.md` maps
each block to the primitive that reads it. A value the code fills is written `$name`,
a declared fact (rule 28): the loader
refuses a `{name}` in copy, since braces belong to the templates an admin writes
(Keiko's own, `ui.announcement.defaults`, are the one place they appear). `description`
names a block's body of text or a caption Discord shows (a slash command's, a button's
Help line), `value` the text a field shows, and an error's text keeps `message`. A
form names an error of its command by its block
(`error: notifications_twitch.ui.errors.preview_needs_streamer`), never by a `.message`
leaf. `app/languages/<namespace>.yml` keeps only the shared copy, each file on its own
schema (`buttons.yml`, `commands.yml` with `commands.responses.*` and
`commands.records.*`, `errors.yml`, `texts.yml`, `locales.yml`), bilingual the same
way; the standard `/report` line is written once, as `fallbacks.footer` of
`app/forms/vocabulary.yml`, which `standard_footer` reads. The row a command has on
/setup is its own copy too, under the fixed places the shared layers read through
`command_text(command, path, locale)` (`command.setup.label`,
`command.setup.description`, `command.stats.title`, `command.stats.delivered.one`,
`command.stats.delivered.other`, `command.slash.subgroup`, `command.slash.name`;
`tests/test_setup_row_copy.py`); a shared layer never
builds a key from a command's old key path. A text missing a locale or carrying an
unknown one, a key defined twice, a YAML anchor or alias, and a file its schema
refuses fail at load (`tests/core/test_i18n.py`, and
`tests/forms/form/test_form_yaml.py` for a `form.yml`); every `ml("...")` literal of
a command's folder resolves in both locales (`tests/test_commands.py`). A command's
slash names and descriptions are its copy too: its cogs pass the block as `namespace`
(`namespace="block_links.command.slash"`) and `app/translator.py` reads `<namespace>.<type>`.
A form writes only what differs from the engine's defaults (the report footer, the
summary sentence, the intro's title on the summary), and a copy or a form names a text
of the shared vocabulary with `{ref: name}` (the `texts` of
`app/forms/vocabulary.yml`); no YAML anchor or alias anywhere, in either file.
Reason: a command's copy and form are changed with its code and deleted with its
folder, and the two files are the two halves of the document a command made from
data will store (`{copy, form}`), so a command in the repository and one an admin
makes share one format and one validator; such a command cannot invent a global key
path.

## 26. A command is declared once

Everything a shared layer needs to know about a command (key, cogs, feature
module, hooks, reminders, webhook events, what it opens from another command's screen,
its parent group, its /setup row and category, its permissions, whether it loads
lazily) is a field of its `COMMAND = Command(...)`; its copy and form are its
folder's `copy.yml` and `form.yml` (rule 25), found by convention, never a field. `COMMANDS` in
`app/commands/__init__.py` lists the folders in load and dispatch order. The parent
groups are one table, `PARENT_GROUPS` (`app/core/commands.py`), and the registry
refuses a `group` or `category` outside it. No parallel list of commands, keys or setup
rows in `app/constants.py` or anywhere else. Reason: lists kept apart drift apart.
`tests/test_commands.py` refuses a command that is not whole: a folder without its
`COMMAND`, a list that misses a folder or holds one twice, a key used twice, a YAML
in the folder other than `copy.yml` and `form.yml`, a form that does not compile, a copy key missing in a locale, an event outside the catalog, a reminder title or webhook event with two
owners, a `command_for`, `opener` or `setup_feature` literal that names no such
command, a cog module without its `setup(bot)` (`docs/commands.md`).

## 27. Every YAML type is its own schema, and its reference is generated

Every kind of YAML Keiko reads (`form.yml`, `vocabulary.yml`, `copy.yml`, each file of
`app/languages/`, `global.yml`, `catalog.yml`) has one pydantic root model registered
in `YAML_TYPES` (`app/core/commands.py`), and the YAML is that model: `extra="forbid"`
at every level, no alias layer (what an author writes is the field's name), no node
rebuilt from known keys, `type` as the discriminator of step and section types, and
each field only on the types that read it. A text that changes with the state is a
block's `overrides`, a list of `{when, <the fields it replaces>}` whose first holding
item wins (copy and forms alike; `Override` in `app/core/conditions.py` is the base of
every item, and each block's item type carries only the fields that block has), never
an open map of states the code picks by name. Every field carries a
`Field(description=...)` of one sentence saying what it makes the bot do, with an
`examples=` where the value is not obvious, and its role,
`json_schema_extra={"role": ...}` (`identity`, `copy`, `visual`, `behavior`,
`conditions`, `data`, `structure`: `docs/commands.md`); its English has a Portuguese in
`scripts/keiko/yamldoc/translations.yml`. A mapping whose keys the author chooses is an
`Open[T]` (`app/core/commands.py`), never a plain `dict` or `Mapping[str, Any]`, and a
file writes it under one comment line `# open: <what the keys are> -> <what each child
is>`; a union item writes its `type` first, then the closed keys in schema order, then
the open blocks, and a model that declares `json_schema_extra={"in_order": True}`
(`CommandCopy` and its groups `command` and `ui`) writes every key in schema order.
`docs/yaml-reference.html` is generated from the schemas by `make
docs`, run after changing a schema or a YAML file:
`tests/test_yaml_reference.py` fails on a stale page, a field without a description
or a role, and a text without a current Portuguese, and `tests/test_yaml_layout.py` on
a file out of order or an open block without its line; their `UNDESCRIBED`,
`UNTRANSLATED` and `EXCEPTIONS` lists are empty and only shrink. Reason: an
author reads one page to know every key a file may hold, and a key the bot never reads
cannot be written.

## 28. A value a rule or a text reads is a declared fact

A fact is addressed `<owner>.facts.<name>`, the owner a command's key or `global`.
A command declares its own in the `facts:` of its `copy.yml`, each with a `type`
(`boolean`, `enum` with `values`, `number`, `text`) and a one-sentence `description`;
`app/core/global.yml` declares the facts more than one command reads, and the context
Keiko already holds (`{context:commands.<key>}`, `{context:groups.<group>}`, and the
form tokens). A fact starts in its command and moves to `global` when a second command
needs it; a short name lives in one owner, and no command is keyed `global`. A rule in
copy names the full address, a `$name` the short one, and a shared file of
`app/languages` reads only global facts. The code passes facts, never a state or a
variant name: through its command's `command_facts(**values)` (the first function of
its `service.py`, `facts("<key>", **values)`), or `facts(GLOBAL, ...)` in a shared
layer. A slash command or a parent group a text names is its context entry, never typed
in. Reason: one place to look for every name a text reads, typos caught at load
(`tests/core/test_i18n.py`), at render (`i18n.resolved`) and in the code
(`tests/test_facts.py`), and the same shape for the copy a command made from data
will store.
