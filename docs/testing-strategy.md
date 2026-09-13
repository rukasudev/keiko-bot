# Keiko testing strategy

How Keiko is tested, layer by layer, and how bugs become permanent coverage.
Companion doc: `docs/form-scenario-testing.md` (how to write behavioral
scenarios and golden paths). Architecture reference:
`docs/form-configuration.md`.

## The layers

| Layer | Scope | Where | Runs |
|---|---|---|---|
| 1. Unit | pure logic: formatters, date utils, services | `tests/test_*.py`, `app/**/*_test.py` | every run |
| 2. Platform contracts | the form platform against its own contract: every shipped YAML compiles, the definition schema refuses bad rules, `decide` on one session and one event, the invariants I1 to I9, the boundary lines, the feature modules, the adapter choreography | `tests/forms/` (`definitions/`, `engine/`, `features/`, `adapters/`, `test_invariants.py`, `test_boundary.py`) | every run |
| 3. Behavioral scenarios | full admin flows through the real platform, offline | `tests/behavioral/scenarios/`, `contracts/`, `regressions/` | every run |
| 3b. Golden transcripts | every canonical admin path of every form, byte for byte, in both locales where copy differs | `tests/behavioral/golden/` (`docs/form-scenario-testing.md`, "Golden transcripts") | every run |
| 4. Discord adapter fakes | the fake interaction surface itself | `tests/behavioral/harness/` + `test_harness.py`, `test_golden_harness.py` | every run |
| 5. Live smoke | what no simulator can represent | manual, private test guild | on demand only |

Everything through layer 4 is offline, deterministic, needs **no Discord
token and no network**, and runs in `make test`. `make lint` (ruff and
`mypy --strict` over `app/forms/` and `tests/forms/`) is part of the gate
for any change under `app/forms/`.

## Why a project-owned harness (research, accessed 2026-07-26)

The repo installs `discord.py` from **git master** (`2.8.0a`) and uses
Components V2 (`LayoutView`, `Container`, `TextDisplay`, `MediaGallery`)
plus master-only APIs (`ui.Label`, `ui.FileUpload`). No external test
framework covers that surface:

- **dpytest 0.7.0** — last release Jun/2023, Alpha; simulates message
  flows; interactions/components support is an open issue (#125, since
  Nov/2023). Sources: <https://pypi.org/project/dpytest/>,
  <https://github.com/CraftSpider/dpytest>.
- **SimCord** — no such Python Discord testing framework exists (only an
  unrelated blockchain company and a Go wrapper).
- **interactions-unittest** — targets the `discord-py-interactions`
  library, not `discord.py`.
- Components V2 only exists since discord.py 2.6
  (<https://github.com/Rapptz/discord.py/pull/10166>).

So the behavioral layer is a small project-owned harness
(`tests/behavioral/harness/`, ~8 modules) driving the **real** production
code through its real entry seam (`open_feature`).

## What is real vs. fake in a behavioral scenario

Real: the form YAML and its compilation, the engine (`decide`, the session
store, the `when` evaluator), every step kind and card section, the
renderer and the executor (the screens, modals, cards and transitions an
admin sees), validators and transforms, i18n (`ml()` with the real
language files), the feature modules, the services and the `app/data`
layer, and the persisted document shape.

Fake (at architectural boundaries only): the Discord transport
(`FakeInteraction`/`FakeResponse`/`FakeFollowup` — strict state machines
that raise on out-of-order API use), MongoDB/Redis (the existing mocks in
`tests/mocks/database.py`, with the async layer over `MockMotorClient`), the
global `bot` (MagicMock via the existing conftest injection), and the
welcome banner rendering (`create_banner` is stubbed by an autouse fixture
so no scenario fetches an image).

## Running

```bash
make test                                     # everything (unit + platform + behavioral)
make lint                                     # ruff + mypy --strict on app/forms
pytest tests/forms -q                         # platform contracts only
pytest tests/behavioral -q                    # behavioral suite only
pytest tests/behavioral/golden -q             # the UX contract
pytest -m "shared_contract" -q                # shared-component contracts
pytest tests/behavioral/regressions -q        # regression scenarios
pytest tests/behavioral -k invalid_day -vv    # one scenario
pytest tests/ app/ -q                         # full suite, no -x
```

Failures always embed the full interaction transcript — no prints needed.
`pytest` runs outside the command sandbox because `requests` reads
certifi's bundle at import.

## Regression workflow (mandatory)

A reported bug follows this path, in order:

1. **Reproduce** — express the reported behavior as an automated failing
   test at the most realistic appropriate layer (unit / platform contract /
   behavioral scenario / shared-component contract / golden path).
2. **Confirm the failure** — run it BEFORE fixing and check it fails for
   the reported reason, not a setup problem.
3. **Identify the regression surface** — does the bug live in shared code?
   List the consumers (commands/forms) of that code.
4. **Fix generically** — fix the shared implementation or the definition
   contract; never `if command_key == ...` for a shared failure.
5. **Run the related suites** — the new test plus the contract suite of
   the touched shared component, not only the new test.
6. **Preserve the test** — it moves to `tests/behavioral/regressions/`
   (or stays in the layer suite) permanently.

A bug is not fixed until a permanent automated test would fail if the
same behavior were reintroduced. See `.claude/rules/bug-fix-protocol.md`.

## Shared-component impact map

When one of these changes, run the mapped suites before calling the change
done (manual map — extend it when a new shared surface appears):

| Changed | Run |
|---|---|
| `app/forms/engine/` (`decide.py`, `session.py`, `store.py`, `rules.py`, `screen.py`, `documents.py`, `summary.py`) | `pytest tests/forms -q` + entire `tests/behavioral` — the goldens will show any change an admin can see |
| `app/forms/definitions/` (`schema.py`, `compiler.py`) | `pytest tests/forms/definitions -q` (every shipped form must still compile, every fixture must still be refused) + `pytest tests/behavioral/golden -q` |
| `app/forms/kinds/` (a step kind, a card section, the manager panel in `manage.py`, the review in `review.py`) | `pytest tests/forms -q` + the scenarios of the forms that use the kind + `pytest tests/behavioral/golden -q` + `tests/behavioral/contracts/test_components_v2_limits.py` for cards and the panel |
| `app/forms/extensions/` (validators, transforms, formatters, copy, links, dates) | `pytest tests/forms -q` + validation scenarios (`test_block_links_flow.py`, `test_reminders_birthday_flow.py`, `test_subscription_orchestration.py`) + `pytest -m "shared_contract"` |
| `app/forms/features/` (a feature module, `generic.py`, `protocol.py`) | `pytest tests/forms/features -q` + that feature's scenarios + `tests/behavioral/contracts/test_manager_form_consumers.py` (every consumer's saved document still renders) + the persistence assertions of the scenarios |
| `app/forms/adapters/discord/` (`entrypoints.py`, `executor.py`, `renderer.py`, `interactions.py`, `ids.py`) | `pytest tests/forms/adapters -q` (dedup, stale clicks, the per-session lock, expiry, send-then-delete, modal first) + `pytest tests/behavioral/golden -q` + `tests/behavioral/regressions/test_view_lifecycle_regressions.py` |
| `app/forms/adapters/discord/observability.py`, `Decision.analytics` in `decide.py` | `tests/behavioral/scenarios/test_analytics_funnel_flow.py` — including the assertion that nothing typed into a modal reaches an event — + `tests/behavioral/contracts/test_analytics_catalog.py` + `tests/behavioral/scenarios/test_journey_flow.py` |
| `InMemorySessionStore`, `Runtime.expire_stale`, `ViewConstants.LONG_TIMEOUT_SECONDS` | `pytest tests/forms/adapters -q -k expir` + `tests/behavioral/scenarios/test_journey_flow.py` — an expired form loses its buttons and says so once, and never after a save |
| `app/components/buttons.py` (the generic buttons outside the forms), `app/components/embed.py` | entire `tests/behavioral` |
| `app/languages/form/*.yml` | `pytest tests/forms/definitions -q` + that form's scenarios + `pytest tests/behavioral/golden -q` (a copy change moves the form's goldens: list it in `docs/ux-changes.md`, then re-record with `--update-golden`) |
| anything an admin can see: a screen, a button order, an embed, a card, a transition (send/edit/delete) | `pytest tests/behavioral/golden -q` — the goldens are the UX contract; a diff means an entry in `docs/ux-changes.md` before re-recording |
| `app/languages/{buttons,commands,errors}/` | `tests/behavioral/contracts/test_form_start_baseline.py` + one pt-br and one en-us scenario |
| `commands.*.yml` slash `desc:`/`name:` entries | `tests/behavioral/contracts/test_slash_command_copy.py` (Discord's 100/32-char sync limits — violations only surface at bot startup) |
| context menus (`app_commands.ContextMenu` registrations, their callbacks and decorators) | `tests/behavioral/contracts/test_context_menu_registration.py` (the tree is only built at startup) |
| `app/data/indexes.py`, new collections | boot the bot once: `ensure_indexes` runs in `create_app` and the offline mock treats `create_index` as a no-op |
| `app/services/analytics.py`, `app/analytics/catalog.yml`, `app/services/analytics_sink.py` | `tests/behavioral/contracts/test_analytics_catalog.py` (the catalog and the code must agree in both directions) + `tests/test_analytics_storage.py` (what becomes a document and what stays a counter) |
| `app/logger.py`, `app/services/trace.py`, anything opening a trace | `tests/behavioral/contracts/test_logger_trace.py` — one unit of work is one Discord message, ordered, and a broken sink never breaks the work — plus `tests/behavioral/contracts/test_debug_logs.py`, which runs both handlers on the same logger: adding a sink must not degrade the embed, and both must read one identity |
| `app/services/debug_logs.py`, `app/services/logs_archive.py`, `app/data/logs.py`, `StoredLogsHandler` | `tests/behavioral/contracts/test_debug_logs.py` — recording never blocks or raises, persisting never logs (a `logger.*` call in this path is an unbounded write storm), the whole traceback survives, and the daily export stays inside its day |
| `app/data/*_async.py`, any `asyncio.to_thread` seam, or any new `requests` / `pymongo` / `time.sleep` call reached from a coroutine | `tests/behavioral/regressions/test_event_loop_is_never_blocked.py` — the test counts how many times the loop got control back while the call ran, because a blocked loop stops the whole bot and spends Discord's three-second interaction budget — + `tests/forms/test_boundary.py` (nothing under `app/forms/` may block) |
| `is_foreign`, `is_muted`, `NOISE_MARKERS` (`app/logger.py`) | `tests/behavioral/regressions/test_foreign_logs_stay_out_of_discord.py` — a library writing about itself stays in `guild.logs` and out of the admin channel, and Keiko's own records still reach it |
| `app/services/reminders.py`, `app/integrations/reminder_webhook.py` | `tests/behavioral/contracts/test_reminders_api_contract.py` — reminders-api takes date_tz and time_tz as separate fields; folding the hour into date_tz is refused, and the refusal looks like an ordinary empty response |
| `app/services/reminders_birthdays.py`, the birthdays cog loop | `tests/behavioral/contracts/test_birthday_reminder_reconciliation.py` — a birthday saved without a reminder is found again and finished, a still-failing one is left for the next pass, and the loop survives a pass that raises |
| `tools/keiko/birthdays/repair.py` | `tests/tools/test_birthday_repair.py` — the tool names the database it is about to write to and refuses a prod run that resolved to localhost |
| `TERMINAL_OUTCOMES`, `ACTIONS`, `OUTCOME_ICONS` (`app/services/journey.py`) | `tests/behavioral/contracts/test_journey.py` — adding an item is a step, not an ending, and the title vocabulary has one owner |
| `trace_scope`, `app/decorators.py`, `ExecuteCommandButton` | `tests/behavioral/contracts/test_trace_logging_bus.py` — everything a trace shows must travel through `logging`, once, or it renders in Discord and exists nowhere else |
| `build_trace_embed`, `TraceTitles` | `tests/behavioral/contracts/test_trace_embed_format.py` — one emoji per kind of event, metadata in fields, timeline inside the 1024 field limit |
| `feature_config_states` (`app/services/cogs.py`), a service's `config_states`, `feature_keys()` (`app/forms/features/__init__.py`) | `tests/test_config_usage.py` + `tests/behavioral/regressions/test_analytics_report_truthfulness.py` — a feature declares how its configuration is read by defining `config_states`, it must answer in the keys the form declares, and it must never report a value the card only drew |
| `trace.run_traced`, `app/webhooks/jobs.py` | `tests/behavioral/regressions/test_webhook_background_trace.py` — work that outlives the request gets a message of its own instead of writing into one that was already posted, and the request still names its subject before scheduling |
| `LogTypes.REPORTED_EVENT_TYPES`, `Trace.reported_event`, `TraceFoldingHandler.emit` | `tests/behavioral/regressions/test_guild_lifecycle_logging.py` + `tests/behavioral/contracts/test_trace_embed_format.py` — a listener that reported an event publishes, a routine one stays silent, and title and colour come from the same pair |
| `tools/keiko/logs/*` | `tests/tools/` — both file shapes on the Discord logs channel still parse, re-syncing adds nothing, and `app/` never imports `tools/` |
| `app/views/report_browser.py`, any command composing it | `tests/behavioral/contracts/test_report_browser.py` — choosing a section edits the message instead of sending another, and sections build lazily |
| `app/views/records.py`, `MemberPicker` | `tests/behavioral/contracts/test_records_view.py` + `tests/behavioral/scenarios/test_block_links_records_flow.py` |
| `app/services/admin_digest.py`, `app/cogs/analytics.py` loops | `tests/test_admin_digest.py` — the digest must survive an empty install and stay readable on a quiet week |
| `analytics.records_raw_choice`, a new field or step kind in a form YAML | `tests/test_config_usage.py` + `test_no_free_text_or_id_field_of_any_form_is_ever_tabulated` — a new field cannot opt itself into having its values recorded |
| `app/services/journey.py`, `analytics.register_observer`, `open_journey`/`close_journey` in the adapter | `tests/behavioral/contracts/test_journey.py` + `tests/behavioral/scenarios/test_journey_flow.py` — one session is one message, finalizing is idempotent, and an edit lists field names only |
| `app/data/` | persistence assertions in scenarios |

## Risk-based coverage priorities

- **High**: `decide` and the session store, the compiler and the `when`
  grammar, the manager panel and its child sessions, the adapter's stale
  click and expiry handling, localization resolution, persistence through
  the feature modules, the persisted document shape.
- **Medium**: validators/transforms, edit/back/cancel navigation, command
  families sharing a configuration shape.
- **Low**: isolated visual details and copy that does not affect behavior
  (the goldens already pin them).

Do not chase exhaustive coverage; add scenarios where bugs and risk
appear. When a new command adopts the manager with a meaningfully new
configuration shape, add it to the parametrized consumer contract
(`tests/behavioral/contracts/test_manager_form_consumers.py`) and record
its golden paths.

## Feature completion criteria

A feature that touches shared infrastructure is complete only when: its
own scenarios pass; representative existing consumers still pass
(`pytest -m "shared_contract"`); the goldens pass or their deltas are
listed in `docs/ux-changes.md`; relevant localization scenarios pass; and
existing saved configuration still loads and displays. A change under
`app/forms/` also needs `make lint` green. The final report must include
the `## Regression protection` section defined in
`.claude/rules/bug-fix-protocol.md`.

## Known limitations (what still needs a real Discord guild)

Now covered offline (behavioral suite): the full manager lifecycle through
its real buttons (pause/unpause/disable with the typed confirmation modal,
add/edit/remove item through the real child session and the item picker),
the edit flow from the panel, the file-upload logic (fake attachment, real
re-upload path, recorded dump channel), Twitch subscribe/unsubscribe
orchestration against the recording API mock (`is_dev` gate opened per
scenario), session expiry (the clock passes every deadline and the message
loses its buttons once), duplicate and stale clicks, two clicks racing on
one session, and Discord's Components V2 hard limits (≤40 components,
≤4000 chars of text) checked against the real cards.

Irreducibly manual / live-only: drift of the real Discord API and
discord.py master (the fakes encode our model of Discord — a scheduled
live smoke in a dedicated test guild is the only detector); real
permission/intent enforcement and the 3-second acknowledge window; the
actual CDN file hosting; the real Twitch/YouTube EventSub contract; and
the visual judgment of how Components V2 render in Discord's client.
Note that fully automated end-to-end clicking against real Discord is not
merely hard — bots cannot click buttons and automating a user account
violates Discord's ToS, so offline click simulation is the correct
mechanism, not a workaround. Keep the live smoke small, on a **dedicated
test guild and application** (never production credentials), and never in
the default suite.

## Existing unit tests

They stay. Known weaknesses to keep in mind when reading them (audited
2026-09-13): `tests/mocks/discord.py`'s `MockInteraction` predates the
behavioral harness and has no consumers; `tests/generators/` document
shapes have drifted from what the platform really persists (verified by
the behavioral persistence assertions — prefer the shapes in
`tests/behavioral/contracts/`). Migrate a unit test to a behavioral
scenario only when the scenario clearly supersedes it and the change is
low risk. The old engine's tests of private methods (`test_form.py`,
`test_form_state.py`, `test_reusable_configuration.py`) left with the
engine; `tests/forms/` is the platform's contract suite.
