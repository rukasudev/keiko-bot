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
| Which tests to run for shared code | `docs/testing-strategy.md`, the impact map |
| A user-visible behaviour change | `docs/ux-changes.md`: the header and the last rows only, to copy the row shape and take the next id |

## Where things live

- Slash commands: `app/cogs/`; their logic: `app/services/<feature>.py`.
- Forms: one YAML per feature in `app/languages/form/`; the engine in `app/settings/form/`; one module per feature in `app/settings/features/`; Discord only in `app/settings/discord/`.
- Copy: `app/languages/{buttons,commands,errors,messages}/<ns>.{en-us,pt-br}.yml`, read with `ml(key, locale)`.
- Data: `app/data/` (flat functions); cache: `app/services/cache.py`; server settings: `app/services/moderations.py`.
- /configurar: `app/services/setup.py`; statistics: `app/services/feature_health.py`.
- Tests: `tests/forms/` (platform contracts), `tests/behavioral/{scenarios,contracts,regressions,golden}/`.

## The gate

Run from the worktree, with the sandbox off (`requests` reads a certifi `.pem` at import):

```
./.venv/bin/python -m pytest tests/ app/ -q -p no:cacheprovider
./.venv/bin/python -m pytest -m shared_contract -q -p no:cacheprovider
PATH=$PWD/.venv/bin:$PATH make lint
```

Goldens: `pytest tests/behavioral/golden --update-golden`, only once the code is final, in their own commit, after reading every diff.

## Rules that are easy to miss

- A bug gets a failing test first, and the test asserts what a user sees.
- Every string exists in `en-us` and `pt-br`; Keiko is "ele" in Portuguese.
- `app/settings/` never names a command or a screen; `tests/forms/test_boundary.py` enforces it.
- No narrative comments; one-sentence docstrings on public names.
- Commits: small, English, imperative; no co-author or AI attribution lines.
- Never read `.env` or `*.pem`, never write to a real database, never push.
- Docker: always `--context colima`; never change the active context, and never touch the containers named `mongo-server` or `redis-server`.
