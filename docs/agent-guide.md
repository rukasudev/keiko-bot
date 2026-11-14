# Agent guide

One page for an agent starting a change. Read this first, then only the sections the
change touches.

## What to read, by kind of change

| Change | Read |
|---|---|
| Any change | `CLAUDE.md`, `.claude/rules/code-style.md` |
| A plan | `.claude/rules/implementation-planning.md` |
| A bug | `.claude/rules/bug-fix-protocol.md` |
| A form, a card, a panel, the engine | `docs/form-configuration.md` §3 (schema and step kinds), §5 (extension points) and §6 (feature modules); §7 for the Discord adapter |
| Text a user sees | `.claude/skills/keiko-writing-style/SKILL.md` |
| A metric, an event or a log line | `docs/analytics.md` |
| Data a command stores, a collection or an index | `docs/data-model.md`, then `docs/migrations.md` |
| Which tests to run for shared code | `docs/testing-strategy.md`, the impact map |
| A user-visible behaviour change | `docs/ux-changes.md`: the header and the last rows only, to copy the row shape and take the next id |

## Where things live

- Slash commands: `app/cogs/`; their logic: `app/services/<feature>.py`.
- Forms: one YAML per feature in `app/languages/form/`; the engine in `app/settings/form/`; one module per feature in `app/settings/features/`; Discord only in `app/settings/discord/`.
- Copy: `app/languages/{buttons,commands,errors,messages}/<ns>.{en-us,pt-br}.yml`, read with `ml(key, locale)`.
- Shared modules: `app/core/` (cache, analytics, trace, journey, utils, dates, members...); `app/services/` holds only command modules.
- Data: `app/data/<database>/<collection>.py`, one async repository class per collection (`guild/settings.py`, `guild/cogs.py`, `keiko/settings.py`, `keiko/integrations.py`), built over one `AsyncMongoClient` by `app.data.build` at startup and awaited through `data()`: `await data().guild_cogs.find_cog_by_guild_id(guild_id, key)`; tests install them over the in-memory fake, and `deps.mongo_client` is the same store, sync, to seed and assert; cache: `app/core/cache.py`; server settings, join and leave: `app/core/servers.py`.
- Database structure and data changes: `app/migrations/schema/` and `app/migrations/data/` (`NNNN_slug.py`, each folder numbered on its own); which collection holds what: `docs/data-model.md`.
- /configurar: `app/services/setup.py`; statistics: `app/core/feature_health.py`.
- Developer tooling: `scripts/keiko/` (`make logs`, `make repair-birthdays`, `make soak`); never imported by `app/`, left out of the image.
- Tests: `tests/forms/` (platform contracts), `tests/behavioral/{scenarios,contracts,regressions,golden}/`.

## The gate

Run from the worktree, with the sandbox off (`requests` reads a certifi `.pem` at import):

```
./.venv/bin/python -m pytest tests/ app/ -q -p no:cacheprovider
./.venv/bin/python -m pytest -m shared_contract -q -p no:cacheprovider
PATH=$PWD/.venv/bin:$PATH make lint
```

The tests that need a real Mongo (`-m mongo`: the migrations and the query plans) run
against a throwaway replica set on colima, never against `mongo-server`:

```
docker --context colima run -d --rm --name keiko-mig --ulimit nofile=64000 -p 27030:27017 mongo:7.0 --replSet rs0 --bind_ip_all
docker --context colima exec keiko-mig mongosh --quiet --eval 'rs.initiate({_id:"rs0",members:[{_id:0,host:"localhost:27017"}]})'
MONGO_URL="mongodb://localhost:27030/?directConnection=true" MONGO_TEST_URL="mongodb://localhost:27030/?directConnection=true" ./.venv/bin/python -m pytest -m mongo -q -p no:cacheprovider
docker --context colima stop keiko-mig
```

Goldens: `pytest tests/behavioral/golden --update-golden`, only once the code is final, in their own commit, after reading every diff.

## Rules that are easy to miss

- A bug gets a failing test first, and the test asserts what a user sees.
- Every string exists in `en-us` and `pt-br`; Keiko is "ele" in Portuguese.
- `app/settings/` never names a command or a screen; `tests/forms/test_boundary.py` enforces it.
- No generic layer names a command (`app/data`, `app/core`, `app/views`, `app/components`, the platform, `app/cogs/events.py`): it takes the feature key as a parameter, and the command's rule lives in `app/services/<feature>.py`, which may be wholly specific (code-style rule 22, `tests/test_generic_layers.py`).
- No narrative comments; one-sentence docstrings on public names.
- Commits: small, English, imperative; no co-author or AI attribution lines.
- Never read `.env` or `*.pem`, never write to a real database, never push.
- Docker: always `--context colima`; never change the active context, and never touch the containers named `mongo-server` or `redis-server`.
