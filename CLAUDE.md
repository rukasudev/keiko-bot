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
make test         # python -m pytest tests/ app/ -x -q
make logs ARGS="errors --since 7d"         # the log reader in scripts/keiko/logs
make repair-birthdays                      # dry run of scripts/keiko/birthdays/repair.py; ARGS="--apply" writes
make soak         # the memory soak, through scripts/keiko/soak.py
make clean        # clean cache and venv
```

## Repo map

- `app/cogs/` — slash commands, grouped by feature; bodies delegate to services.
- `app/services/` — command modules only: one module per feature command exposing
  `async manager(interaction, guild_id)`.
- `app/core/` — the shared modules every command builds on: `cache.py`, `analytics.py`,
  `trace.py`, `journey.py`, `utils.py`, `servers.py`, `dates.py`, `members.py`...
- `app/data/` — MongoDB access (pymongo); `app/core/cache.py` — Redis caching.
- `app/migrations/` — the versioned database: `schema/NNNN_slug.py` (collections and
  indexes), `data/NNNN_slug.py` (data, inside `with begin(db) as tx:`), `snapshot.json`,
  the runner and its CLI (`python -m app.migrations status|migrate [--dry-run]|verify|snapshot`).
  Where data lives: `docs/data-model.md`; how a migration is written and run:
  `docs/migrations.md`.
- `app/settings/` — what an admin configures, in three parts. `form/` is the engine:
  what a form YAML may declare (`form_yaml.py`), the frozen session (`form_state.py`),
  events, effects, the `when` evaluator (`conditions.py`), the screen model
  (`components.py`), what each click does (`form.py`), the manager (`manager.py`),
  one file per YAML action (`actions/`) and everything about one response
  (`responses/`). `features/` holds one module per feature: document, commit, asides.
  `discord/` is the only place that imports `discord`.
- `app/views/` + `app/components/` — generic Discord UI outside the forms (records
  browser, pagination, report browser; buttons, selects, embeds).
- `app/languages/form/<command_key>.yml` — one bilingual YAML per feature command declaring
  its entire interaction flow (`steps:`).
- `app/languages/{buttons,commands,errors,messages,locales}/<ns>.<locale>.yml` — paired
  `en-us`/`pt-br` files, resolved at runtime via `ml(key, locale)` (`app/core/utils.py`).
- `app/integrations/`, `app/webhooks/`, `app/api/` — third-party clients, webhook handlers,
  Flask API.
- `scripts/` — developer tooling (`python -m scripts.keiko ...`, `make logs`,
  `make repair-birthdays`, `make soak`); never imported by `app/`, left out of the image.
- `tests/` — pytest suite; `conftest.py` loads real i18n and mocks Mongo/Redis/Discord.
  `tests/forms/` (boundary, invariants, compiler, engine, adapter) is the architectural
  contract suite of the platform.

## Before planning or writing code

- An agent starting a change reads `docs/agent-guide.md` first: what to read for each kind
  of change, where things live, the gate.
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
  comments, where constants live, data-layer shape, what belongs in `app/views/`, generic
  orchestration extracted to generic views, generic helpers in `utils.py` not in feature
  services, UI tunables global in `ViewConstants`).

## Planning contract

Every implementation plan must follow @.claude/rules/implementation-planning.md
(path: `.claude/rules/implementation-planning.md`), including its required
"Implementation decision" summary block and all its required sections,
including "Regression and blast-radius analysis" for changes to shared code.

## Tests and regressions

The test system is layered (unit, YAML contracts, offline behavioral
scenarios): see `docs/testing-strategy.md` and `docs/form-scenario-testing.md`.
Two standing rules:

- A reported bug gets a failing automated test BEFORE the fix, and that test
  stays in the suite permanently — follow `@.claude/rules/bug-fix-protocol.md`
  (path: `.claude/rules/bug-fix-protocol.md`).
- A change to shared code (form engine, manager, components, formatters,
  loaders, i18n helpers) is not done until the consumer contracts pass:
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
both `en-us` and `pt-br`; never hardcode user-facing text in Python; reuse existing generic
button labels and `commands.command-events.*` state messages before creating new keys.
