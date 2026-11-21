# Agent guide

One page for an agent starting a change. Read this first, then only the sections the
change touches.

## What to read, by kind of change

| Change | Read |
|---|---|
| Any change | `CLAUDE.md`, `.claude/rules/code-style.md` |
| A plan | `.claude/rules/implementation-planning.md` |
| A bug | `.claude/rules/bug-fix-protocol.md` |
| A new or changed test | `.claude/rules/testing.md` (where it goes, how it drives Keiko, what it asserts), then `docs/form-scenario-testing.md` for the harness's actions |
| A new command, or a change to one command's folder | `docs/commands.md` |
| A form, a card, a panel, the engine | `docs/form-configuration.md` §3 (schema and step types), §5 (extension points) and §6 (feature modules); §7 for the Discord adapter |
| Text a user sees | `.claude/skills/keiko-writing-style/SKILL.md`, the copy block of the primitive that draws it in `docs/commands.md`, and "The token syntax" there for any value the text does not hold |
| Any YAML key (a form, a copy, a shared copy file, the vocabulary, the catalog) | `docs/yaml-reference.html`, the reference generated from the schemas (`make docs` after a change) |
| A metric, an event or a log line | `docs/analytics.md` |
| Data a command stores, a collection or an index | `docs/data-model.md`, then `docs/migrations.md` |
| Which tests to run for shared code | `docs/testing-strategy.md`, the impact map |
| A user-visible behaviour change | `docs/ux-changes.md`: the header and the last rows only, to copy the row shape and take the next id |
| A new doc in `docs/`, or a doc's title or first paragraph | `docs/index.html`, the index of every doc: a new doc goes in a group of `GROUPS` (`scripts/keiko/yamldoc/index.py`), then `make docs` (`tests/test_docs_index.py` fails on a stale index) |

## Where things live

- A command: its folder `app/commands/<command>/`: `__init__.py` (`COMMAND = Command(...)`), `cog.py` (slash commands, thin), `service.py` (its rule, `async manager(interaction)`, and, at its end, its `FeatureModule` as `FEATURE` when the generic save of its form is not enough), `copy.yml` (its copy) and, for a command with a form, `form.yml` (its form), both found beside the package by convention; the ordered list in `app/commands/__init__.py`; the guide in `docs/commands.md`.
- The registry: `app/core/commands.py` (`Command`, `Hooks`, `commands()`, `command_for`, `opener`, `setup_features`, `setup_feature`, `definition`, `PARENT_GROUPS`), the only way a shared layer reaches a command; the parent slash groups in `app/core/groups.py`; `app/core/dispatch.py` runs the hooks for the shared listeners of `app/core/events.py`, the reminders and the webhooks.
- Forms: the engine in `app/forms/form/`; the `FeatureModule` contract and `GenericCogFeature` in `app/forms/features/`; Discord only in `app/forms/discord/`; each form is its command's `form.yml`, found through the registry (`form_file(key)`); a form writes only what differs from the defaults of `app/forms/vocabulary.yml` (the report footer, the summary sentence, the intro's title on the summary) and names its shared texts with `${ref.<name>}`, never a YAML anchor or alias.
- Copy: a command's own strings in its `copy.yml`, a `CommandCopy` of three groups in this order, `facts`, `command` (the fixed places the shared screens fill: `slash`, `setup`, `stats`, `panel`) and `ui` (what the command draws itself, one block per primitive: `cards`, `texts` for what no primitive draws, `records` for `RecordsBrowser`, `responses` for `response_embed`, `errors` for an error, `buttons`, `announcement`: `docs/commands.md`), each addressed as the file nests it (`<key>.ui.responses.<name>`), every text a `{en-us, pt-br}` node under a snake_case key relative to the command and loaded under its `key` (`ml("block_links.command.stats.empty", locale)`), a value the code fills written `$name`; the fixed places a shared layer reads of every /setup command (`command.setup.label`, `command.setup.description`, `command.stats.title`, `command.stats.delivered.one`, `command.stats.delivered.other`, `command.slash.subgroup`, `command.slash.name`) through `command_text(command, path, locale)`; copy that two commands or a shared layer read in `app/languages/{buttons,commands,errors,texts,locales}.yml`, bilingual the same way, each file on its own schema. `app/core/i18n.py` loads every file at start-up and refuses a text missing a locale, a key defined twice, a YAML anchor or alias and a file its schema refuses, and the form source reads each `form.yml` with the same `strict_yaml`; read with `ml(key, locale)` or, for a typed block, `i18n.block(key, Model)`. `copy.yml` and `form.yml` are the two halves of the document a command made from data will store (`{copy, form}`), so both share one format and one validator.
- Shared modules: `app/core/` (cache, analytics, trace, journey, utils, dates, members...); generic Discord UI in `app/ui/`; outside services in `app/integrations/` (`reminders/`, `twitch/` and `youtube/` as `client.py` plus `webhook.py`; the others one module each) and the Flask app in `app/integrations/http.py`.
- Data: `app/data/<database>/<collection>.py`, one async repository class per collection (`guild/settings.py`, `guild/cogs.py`, `keiko/settings.py`, `keiko/integrations.py`), built over one `AsyncMongoClient` by `app.data.build` at startup and awaited through `data()`: `await data().guild_cogs.find_cog_by_guild_id(guild_id, key)`; tests install them over the in-memory fake, and `deps.mongo_client` is the same store, sync, to seed and assert; cache: `app/core/cache.py`; server settings, join and leave: `app/core/servers.py`.
- Database structure and data changes: `app/migrations/schema/` and `app/migrations/data/` (`NNNN_slug.py`, each folder numbered on its own); which collection holds what: `docs/data-model.md`.
- /configurar and the greeting: `app/commands/setup/`; statistics: `app/core/feature_health.py`.
- Developer tooling: `scripts/keiko/` (`make logs`, `make repair-birthdays`, `make soak`, `make command`); never imported by `app/`, left out of the image.
- Tests: `tests/forms/` (form engine contracts), `tests/behavioral/{scenarios,contracts,regressions,golden}/`, the tree's guards `tests/test_structure.py`, `tests/test_commands.py`, `tests/core/test_i18n.py`, and the YAML guards `tests/test_yaml_reference.py` (the reference is current, every field described) and `tests/test_yaml_layout.py` (every file in schema order, an `# open:` line above each open block).

## The gate

Run from the worktree, with the sandbox off (`requests` reads a certifi `.pem` at import):

```
./.venv/bin/python -m pytest tests/ -q -p no:cacheprovider
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
- A value a text does not hold is a token of one syntax, in every YAML file: `$count`, `${prefix}ping`, `${answer.<key>}`, `${plural.<key>|<one>|<many>}`, `${context.<name>}`, `${ref.<name>}`, `$$` for a dollar; `{name}` is only an admin's template variable. One reader (`app/core/tokens.py`), never a `str.replace("$...")` (code-style rule 29).
- `app/forms/` never names a command or a screen; `tests/forms/test_boundary.py` and `tests/test_structure.py` enforce it.
- No shared layer names a command (`app/core`, `app/forms`, `app/ui`, `app/integrations`, `app/data`): it takes the command's key as a parameter, and the command's rule lives in its folder, which may be wholly specific (code-style rule 22, `tests/test_structure.py`).
- A command never imports another command, and a shared layer never imports `app.commands`: it asks the registry (rules 23-24, `tests/test_structure.py`).
- No narrative comments; one-sentence docstrings on public names.
- Commits: small, English, imperative; no co-author or AI attribution lines.
- Never read `.env` or `*.pem`, never write to a real database, never push.
- Docker: always `--context colima`; never change the active context, and never touch the containers named `mongo-server` or `redis-server`.
