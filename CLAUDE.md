# CLAUDE.md

Keiko Bot is a Discord bot written in Python (discord.py) providing server moderation,
stream notifications (Twitch/YouTube), birthday reminders, and integrations.

Core architectural principle:

> **Python implements reusable capabilities. YAML combines those capabilities to create
> commands, forms, and interaction flows.**

## Commands

```bash
make setup        # create venv and install dependencies
make run          # start bot with Docker services (Redis, MongoDB)
make docker-up    # start only Redis and MongoDB containers
make test         # python -m pytest tests/ app/ -x -q
make clean        # clean cache and venv
```

## Repo map

- `app/cogs/` — slash commands, grouped by feature; bodies delegate to services.
- `app/services/` — business logic; one module per feature command exposing
  `async manager(interaction, guild_id)`, plus shared helpers (`utils.py`, `moderations.py`).
- `app/data/` — MongoDB access (pymongo); `app/services/cache.py` — Redis caching.
- `app/views/` + `app/components/` — the generic form/manager UI engine (`form.py`,
  `manager.py`, `summary_card.py`; buttons, modals, selects, embeds).
- `app/languages/form/<command_key>.yml` — one bilingual YAML per feature command declaring
  its entire interaction flow (`steps:`).
- `app/languages/{buttons,commands,errors,messages,locales}/<ns>.<locale>.yml` — paired
  `en-us`/`pt-br` files, resolved at runtime via `ml(key, locale)` (`app/services/utils.py`).
- `app/integrations/`, `app/webhooks/`, `app/api/` — third-party clients, webhook handlers,
  Flask API.
- `tests/` — pytest suite; `conftest.py` loads real i18n and mocks Mongo/Redis/Discord.
  `tests/test_reusable_configuration.py` is the architectural contract suite.

## Before planning or writing code

- Investigate first. Trace the real execution path of the command you are touching and
  search for existing capabilities (actions, components, validators, transforms, formatters,
  services, localization keys) before proposing anything new. Never claim something doesn't
  exist without searching for it.
- Read `docs/form-configuration.md` before touching any form or YAML-driven command — it
  maps the engine, its registries, and its extension points.
- Decision order, mandatory: **1)** YAML-only change → **2)** reuse an existing Python
  capability → **3)** add a new generic, reusable primitive → **4)** architecture exception,
  which must be explicitly declared and justified — never introduced silently.
- Do not reinvent or duplicate existing behavior: no `if command_name == ...` branches, no
  copied-and-renamed handlers, no parallel state management, no near-duplicate components.

## Planning contract

Every implementation plan must follow @.claude/rules/implementation-planning.md
(path: `.claude/rules/implementation-planning.md`), including its required
"Implementation decision" summary block and all ten sections.

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
