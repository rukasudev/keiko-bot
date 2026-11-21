# CLAUDE.md

Keiko Bot is a Discord bot written in Python (discord.py) providing server moderation,
stream notifications (Twitch/YouTube), birthday reminders, and integrations.

Core architectural principle:

> **Python implements reusable capabilities. YAML combines those capabilities to create
> commands, forms, and interaction flows.**

## Commands

```bash
make setup        # create venv and install dependencies
make run          # start bot with Docker services (Redis, MongoDB), after make migrate
make migrate      # apply the pending database migrations (docs/migrations.md)
make docker-up    # start only Redis and MongoDB containers
make test         # python -m pytest tests/ -x -q: the whole suite, the gate
make test-fast    # forms, scenarios, goldens, harness and contracts, no `slow` test
make logs ARGS="errors --since 7d"         # the log reader in scripts/keiko/logs
make repair-birthdays                      # dry run of scripts/keiko/birthdays/repair.py; ARGS="--apply" writes
make soak         # the memory soak, through scripts/keiko/soak.py
make test-guard   # what the suite still guards, against a baseline (docs/testing-strategy.md)
make command name=x [form=yes]   # scaffold a command folder (docs/commands.md)
make docs         # regenerate docs/index.html (every doc) and docs/yaml-reference.html
make clean        # clean cache and venv
```

Every YAML type (`form.yml`, `copy.yml`, `app/languages/*.yml`, `vocabulary.yml`,
`global.yml`, `catalog.yml`) is its own schema, registered in `YAML_TYPES` (`app/core/commands.py`),
and is documented in `docs/yaml-reference.html`, generated from those schemas: read it
before writing a YAML key, and run `make docs` after changing a schema or a YAML
file (`docs/commands.md`, "The YAML reference"; code-style rule 27).

## Repo map

- `app/commands/<command>/` — one command, one folder, everything it owns:
  `__init__.py` declares it once (`COMMAND = Command(...)`), `cog.py` holds its slash
  commands and context menus (thin, they call the service), `service.py` its rule,
  `async manager(interaction)` and, at its end, `FEATURE`, its `FeatureModule`, when
  the generic save of a form is not enough; `copy.yml` its copy, a `CommandCopy`
  of three groups in this order: `facts`, then `command` (`slash`, `setup`, `stats`,
  `panel`: the fixed places Keiko's shared screens fill), then `ui` (`cards`, `texts`,
  `records`, `responses`, `errors`, `buttons`, `announcement`: what the command draws
  itself), each block read by one primitive and addressed as the file nests it
  (`<key>.ui.responses.<name>`, `<key>.command.stats.title`); snake_case keys relative
  to the command, `$name` for a fact the code
  passes, declared in its `facts:` or in `app/core/global.yml`; the block per primitive
  in `docs/commands.md`) and, when it has a form,
  `form.yml` its form, every text a `{en-us, pt-br}` node, both found by convention
  beside the package (no path to declare). The two
  files are the two halves of the document a command made from data will store.
  `app/commands/__init__.py`
  lists them in `COMMANDS`, in load and dispatch order. A command folder never imports
  another one.
- `app/core/` — shared, never names a command. `commands.py` is the registry (`Command`,
  `Hooks`, the event contexts, `PARENT_GROUPS`, `commands()`, `command_for`, `opener`,
  `setup_features`, `setup_feature`, `copy_file`, `form_file`), the only way a shared
  layer reaches a command;
  `groups.py` holds the parent slash groups (moderations, notifications, integrations)
  and attaches each command's subgroup under its `group`; `dispatch.py` runs the
  commands' hooks, reminders and webhooks; `events.py`, `errors.py`, `jobs.py` and
  `monitoring.py` are the shared listeners, error handlers, loops and metrics endpoint;
  `conditions.py` is the `when` grammar, its evaluator and the `overrides` it picks
  (pure, read by the form engine and the copy primitives alike); `facts.py` and
  `global.yml` are the facts and the context (`global.facts.<name>`,
  `${context.<name>}`: below, "User-facing text"); `tokens.py` is the one syntax every
  text writes a value in, read in one pass;
  then `cache.py` (Redis, `read_cog_data`), `i18n.py` (the copy loader),
  `analytics.py`, `analytics_writer.py`, `trace.py`, `journey.py`, `servers.py`,
  `cogs.py`, `dates.py`, `calendar.py`, `members.py`, `utils.py` (`ml(key, locale)`)...
- `app/forms/` — the form engine. `form/` is the engine: what a form YAML may declare
  (`form_yaml.py`), the frozen session (`form_state.py`), events, effects, the screen
  model (`components.py`), what each click does
  (`form.py`), the manager (`manager.py`), one file per YAML action (`actions/`) and
  everything about one response (`responses/`). `features/` holds the `FeatureModule`
  contract, `GenericCogFeature` and the shared subscription base of Twitch and YouTube.
  `discord/` is the only place in `app/forms/` that imports `discord`.
  `vocabulary.yml` holds the engine's defaults (the report footer, the summary
  sentence) and the shared texts a form names with `${ref.<name>}`; a form writes only
  what differs, and no form uses a YAML anchor or alias.
- `app/ui/` — generic Discord UI outside the forms: records browser, pagination, report
  browser, confirm action, buttons, selects, embeds and the card frame.
- `app/integrations/` — the outside world, one client per service
  (`<service>/client.py`, `<service>/webhook.py` for twitch, youtube and reminders);
  `http.py` is the Flask app, its webhook routes and the bridge to the bot loop.
  Webhooks reach a command only through the registry.
- `app/data/<database>/<collection>.py` — one async repository per MongoDB collection,
  awaited through `data()`.
- `app/migrations/` — the versioned database: `schema/NNNN_slug.py` (collections and
  indexes), `data/NNNN_slug.py` (data, inside `with begin(db) as tx:`), `snapshot.json`,
  the runner and its CLI (`python -m app.migrations status|migrate [--dry-run]|verify|snapshot`).
  Where data lives: `docs/data-model.md`; how a migration is written and run:
  `docs/migrations.md`.
- `app/languages/{buttons,commands,errors,texts,locales}.yml` — the shared copy
  only (generic buttons, `commands.responses.*`, `commands.records.*`, shared errors
  and texts; the standard `/report` footer is `fallbacks.footer` of
  `app/forms/vocabulary.yml`), each file on its own schema and
  each text a `{en-us, pt-br}` node under the file's namespace; a command's own copy
  is its `copy.yml`. A text missing a locale, a key defined twice, a YAML anchor and a
  file its schema refuses fail at load, in a `copy.yml` and a `form.yml` alike.
- `app/analytics/catalog.yml` — every analytics event name.
- `scripts/` — developer tooling (`python -m scripts.keiko ...`, `make logs`,
  `make repair-birthdays`, `make soak`, `make command`); never imported by `app/`, left
  out of the image.
- `tests/` — pytest suite; `conftest.py` loads real i18n and mocks Mongo/Redis/Discord.
  `tests/forms/` (boundary, invariants, compiler, engine, adapter) is the contract suite
  of the form engine; `tests/test_structure.py` (import contracts, and shared layers
  that name no command), `tests/test_commands.py` (every command whole) and
  `tests/core/test_i18n.py` (every copy file loads, no key twice, every text in both
  locales, no anchor) guard the tree.

## Adding a command

`make command name=<x>` scaffolds the folder, its `copy.yml` and its test
(`form=yes` adds its `form.yml`);
then add it to `COMMANDS`. `docs/commands.md` is the guide: the folder, the `Command`
fields, the hooks, where its data goes, and the two commands to copy from.

## Before planning or writing code

- An agent starting a change reads `docs/agent-guide.md` first: what to read for each kind
  of change, where things live, the gate. `docs/index.html` lists every doc by group; a
  new doc goes in `GROUPS` (`scripts/keiko/yamldoc/index.py`), then `make docs`.
- Investigate first. Trace the real execution path of the command you are touching and
  search for existing capabilities (actions, components, validators, transforms, formatters,
  services, localization keys) before proposing anything new. Never claim something doesn't
  exist without searching for it.
- Read `docs/form-configuration.md` before touching any form or YAML-driven command — it
  maps the engine, its registries, and its extension points.
- Read `docs/analytics.md` before adding any metric, event or log call — event names live
  in `app/analytics/catalog.yml` and nowhere else, instrumentation belongs in the shared
  seams (never in a command handler), and the Discord log channels get one message per
  unit of work, not one per log line.
- Decision order, mandatory: **1)** YAML-only change → **2)** reuse an existing Python
  capability → **3)** add a new generic, reusable primitive → **4)** architecture exception,
  which must be explicitly declared and justified — never introduced silently.
- Do not reinvent or duplicate existing behavior: no `if command_name == ...` branches, no
  copied-and-renamed handlers, no parallel state management, no near-duplicate components.
- Follow `.claude/rules/code-style.md` — maintainer-reviewed code patterns (no narrative
  comments, where constants live, data-layer shape, what belongs in `app/ui/`, generic
  orchestration extracted to generic views, generic helpers in `app/core/` not in a
  command's service, UI tunables global in `ViewConstants`, a command folder that never
  imports another, shared layers that reach a command only through the registry).

## Planning contract

Every implementation plan must follow @.claude/rules/implementation-planning.md
(path: `.claude/rules/implementation-planning.md`), including its required
"Implementation decision" summary block and all its required sections,
including "Regression and blast-radius analysis" for changes to shared code.

## Tests and regressions

The test system is layered (unit, YAML contracts, offline behavioral
scenarios, golden books): see `docs/testing-strategy.md` and
`docs/form-scenario-testing.md`. Before adding or changing a test, follow
`@.claude/rules/testing.md` (path: `.claude/rules/testing.md`): where a new test goes
and at which layer, one test per behaviour, a regression in its command's or
component's group file, Keiko driven only through the harness's public actions and
flows, copy read by key, the ledger entry of every past bug, `make test-fast` during a
round and `make test` plus `make test-guard` before a PR. Three standing rules:

- A reported bug gets a failing automated test BEFORE the fix, and that test
  stays in the suite permanently — follow `@.claude/rules/bug-fix-protocol.md`
  (path: `.claude/rules/bug-fix-protocol.md`).
- A test that moves, merges or is renamed keeps its "Broke as" docstring and takes
  its entry of `scripts/keiko/testguard/ledger.yml` along; an entry is never deleted.
- A change to shared code (`app/core`, `app/forms`, `app/ui`, the loaders, the
  i18n helpers) is not done until the consumer contracts pass:
  `pytest -m "shared_contract"` plus the impact map in the strategy doc.

## Architecture reviews

To audit code against these conventions (current branch, a file, a directory, or the
whole project), use the `keiko-architecture-review` skill
(`.claude/skills/keiko-architecture-review/SKILL.md`). It is review-only: it reports
findings and never edits code.

## User-facing text and Discord UI

Before changing any user-visible string or Discord component (commands, embeds, forms,
modals, buttons, errors, notifications, placeholders, footers), read
`.claude/skills/keiko-writing-style/SKILL.md` — it is the source of truth for Keiko's
personality, writing style, localization, and UI conventions. Every string must exist in
both `en-us` and `pt-br`; never hardcode user-facing text in Python; write a value the
code fills as `$name`, in the copy block of the primitive that draws it, and declare it
as a fact; reuse existing generic button labels and `commands.responses.*` state
messages before creating new keys. Every value a text does not hold is a token of one
syntax, in every YAML file: `$count` (a fact, the name read whole), `${prefix}ping`
(braces glue it to text), `${answer.<key>}` (`:<format>`, `|<fallback>`),
`${plural.<key>|<one>|<many>}`, `${context.<name>}`, `${ref.<name>}` and `$$` for a
dollar; `{name}` is only an admin's template variable. The loader and the form compiler
refuse a typo, naming the closest name (`docs/commands.md`, "The token syntax";
code-style rule 29).

A text that changes with the state is written in the YAML, never picked in code: the
block keeps its default fields and an `overrides` list, each item a `when` rule (the
forms' grammar, on facts addressed `<owner>.facts.<name>`) and the fields it replaces;
the first item whose rule holds wins. The code passes facts (its command's
`command_facts(...)`, or `facts(GLOBAL, ...)` in a shared layer), never a variant name.
A slash command or a parent group named in a text is `${context.commands.<key>}` or
`${context.groups.<group>}`, never typed in (`docs/commands.md`, "Overrides and facts";
code-style rule 28).
