# Keiko testing strategy

How Keiko is tested, layer by layer, and how bugs become permanent coverage.
Companion doc: `docs/form-scenario-testing.md` (how to write behavioral
scenarios). Architecture reference: `docs/form-configuration.md`.

## The layers

| Layer | Scope | Where | Runs |
|---|---|---|---|
| 1. Unit | pure logic: formatters, date utils, services | `tests/test_*.py`, `app/**/*_test.py` | every run |
| 2. YAML contracts | every real form YAML resolves against the engine registries | `tests/behavioral/test_form_yaml_contracts.py` | every run |
| 3. Behavioral scenarios | full user flows through the real engine, offline | `tests/behavioral/scenarios/`, `contracts/`, `regressions/` | every run |
| 4. Discord adapter | the fake interaction surface itself | `tests/behavioral/harness/` + `test_harness.py` | every run |
| 5. Live smoke | what no simulator can represent | manual, private test guild | on demand only |

Everything through layer 4 is offline, deterministic, needs **no Discord
token and no network**, and runs in `make test`.

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
(`tests/behavioral/harness/`, ~7 modules) driving the **real** production
code through its real entry seams.

## What is real vs. fake in a behavioral scenario

Real: form YAML (`parse_form_yaml_to_dict`), the `Form`/`Manager` engine,
every component view (buttons, selects, modals, cards), YAML validation
and transforms, i18n (`ml()` with the real language files), services and
the `app/data` layer, the persisted document shape.

Fake (at architectural boundaries only): the Discord transport
(`FakeInteraction`/`FakeResponse`/`FakeFollowup` — strict state machines
that raise on out-of-order API use), MongoDB/Redis (the existing mocks in
`tests/mocks/database.py`), and the global `bot` (MagicMock via the
existing conftest injection; `pre_finish_step` short-circuits on
`config.is_dev()`).

## Running

```bash
make test                                     # everything (unit + behavioral)
pytest tests/behavioral -q                    # behavioral suite only
pytest -m "shared_contract" -q                # shared-component contracts
pytest tests/behavioral/regressions -q        # regression scenarios
pytest tests/behavioral -k invalid_day -vv    # one scenario
pytest tests/ app/ -q                         # full suite, no -x
```

Failures always embed the full interaction transcript — no prints needed.

## Regression workflow (mandatory)

A reported bug follows this path, in order:

1. **Reproduce** — express the reported behavior as an automated failing
   test at the most realistic appropriate layer (unit / YAML contract /
   behavioral scenario / shared-component contract).
2. **Confirm the failure** — run it BEFORE fixing and check it fails for
   the reported reason, not a setup problem.
3. **Identify the regression surface** — does the bug live in shared code?
   List the consumers (commands/forms) of that code.
4. **Fix generically** — fix the shared implementation or the YAML
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
| `app/views/form.py`, `app/views/form_state.py` | entire `tests/behavioral` |
| `app/views/manager.py`, `app/services/moderations.py`, `parse_settings_*` / `format_values_by_style` (`app/services/utils.py`) | `pytest -m "shared_contract"` + regressions |
| `app/views/manager_panel.py`, `icon:` in a form YAML, `resolve_form_settings_groups` | `tests/behavioral/contracts/test_manager_panel.py` (saved values, per-row icon, sections, the global Edit fallback and Discord's Components V2 limits) + `tests/behavioral/regressions/test_manager_event_embeds.py` |
| `app/views/panel_transitions.py`, any `interaction.response.edit_message` reached from the manager panel | `tests/behavioral/contracts/test_panel_transitions.py` — a message's Components V2 flag is fixed at send time, so crossing between a container panel and an embed screen must replace the message, never edit it |
| `app/views/summary_card.py` | birthday scenarios + harness self-tests + CV2 limit contracts |
| `app/views/edit.py`, `app/views/remove.py`, lifecycle callbacks | manager lifecycle + edit scenarios (`tests/behavioral/scenarios/test_manager_lifecycle_flow.py`, `test_edit_flow.py`) |
| `app/components/` (buttons, selects, modals) | entire `tests/behavioral` |
| `app/languages/form/*.yml` | YAML contracts + that form's scenarios |
| `app/languages/{buttons,commands,errors}/` | baseline + one pt-br and one en-us scenario |
| `commands.*.yml` slash `desc:`/`name:` entries | `tests/behavioral/contracts/test_slash_command_copy.py` (Discord's 100/32-char sync limits — violations only surface at bot startup) |
| context menus (`app_commands.ContextMenu` registrations, their callbacks and decorators) | `tests/behavioral/contracts/test_context_menu_registration.py` (the tree is only built at startup) |
| `app/data/indexes.py`, new collections | boot the bot once: `ensure_indexes` runs in `create_app` and the offline mock treats `create_index` as a no-op |
| `app/services/analytics.py`, `app/analytics/catalog.yml`, `app/services/analytics_sink.py` | `tests/behavioral/contracts/test_analytics_catalog.py` (the catalog and the code must agree in both directions) + `tests/test_analytics_storage.py` (what becomes a document and what stays a counter) |
| `app/views/form_state.py` (`SessionAwareView`, `FormSession`), any new seam emitting product events | `tests/behavioral/scenarios/test_analytics_funnel_flow.py` — including the assertion that nothing typed into a modal reaches an event |
| `app/logger.py`, `app/services/trace.py`, anything opening a trace | `tests/behavioral/contracts/test_logger_trace.py` — one unit of work is one Discord message, ordered, and a broken sink never breaks the work — plus `tests/behavioral/contracts/test_debug_logs.py`, which runs both handlers on the same logger: adding a sink must not degrade the embed, and both must read one identity |
| `app/services/debug_logs.py`, `app/services/logs_archive.py`, `app/data/logs.py`, `StoredLogsHandler` | `tests/behavioral/contracts/test_debug_logs.py` — recording never blocks or raises, persisting never logs (a `logger.*` call in this path is an unbounded write storm), the whole traceback survives, and the daily export stays inside its day |
| `app/services/reminders.py`, `app/integrations/reminder_webhook.py` | `tests/behavioral/contracts/test_reminders_api_contract.py` — reminders-api takes date_tz and time_tz as separate fields; folding the hour into date_tz is refused, and the refusal looks like an ordinary empty response |
| `app/services/reminders_birthdays.py`, the birthdays cog loop | `tests/behavioral/contracts/test_birthday_reminder_reconciliation.py` — a birthday saved without a reminder is found again and finished, a still-failing one is left for the next pass, and the loop survives a pass that raises |
| `tools/keiko/birthdays/repair.py` | `tests/tools/test_birthday_repair.py` — the tool names the database it is about to write to and refuses a prod run that resolved to localhost |
| `TERMINAL_OUTCOMES`, `ACTIONS`, `OUTCOME_ICONS` (`app/services/journey.py`) | `tests/behavioral/contracts/test_journey.py` — adding an item is a step, not an ending, and the title vocabulary has one owner |
| `trace_scope`, `app/decorators.py`, `ExecuteCommandButton` | `tests/behavioral/contracts/test_trace_logging_bus.py` — everything a trace shows must travel through `logging`, once, or it renders in Discord and exists nowhere else |
| `build_trace_embed`, `TraceTitles` | `tests/behavioral/contracts/test_trace_embed_format.py` — one emoji per kind of event, metadata in fields, timeline inside the 1024 field limit |
| `app/services/config_state.py`, any provider it names, `birthday_manager_cog_data` | `tests/test_config_usage.py` + `tests/behavioral/regressions/test_analytics_report_truthfulness.py` + `tests/behavioral/contracts/test_analytics_is_generic.py` — a provider is resolved by name at call time, must answer in the keys the form declares, and must never report a value the card only drew |
| `trace.run_traced`, `app/webhooks/jobs.py` | `tests/behavioral/regressions/test_webhook_background_trace.py` — work that outlives the request gets a message of its own instead of writing into one that was already posted, and the request still names its subject before scheduling |
| `LogTypes.REPORTED_EVENT_TYPES`, `Trace.reported_event`, `TraceFoldingHandler.emit` | `tests/behavioral/regressions/test_guild_lifecycle_logging.py` + `tests/behavioral/contracts/test_trace_embed_format.py` — a listener that reported an event publishes, a routine one stays silent, and title and colour come from the same pair |
| `tools/keiko/logs/*` | `tests/tools/` — both file shapes on the Discord logs channel still parse, re-syncing adds nothing, and `app/` never imports `tools/` |
| `app/views/report_browser.py`, any command composing it | `tests/behavioral/contracts/test_report_browser.py` — choosing a section edits the message instead of sending another, and sections build lazily |
| `app/services/admin_digest.py`, `app/cogs/analytics.py` loops | `tests/test_admin_digest.py` — the digest must survive an empty install and stay readable on a quiet week |
| `analytics.records_raw_choice`, a new field or step type in a form YAML | `tests/test_config_usage.py` + `test_no_free_text_or_id_field_of_any_form_is_ever_tabulated` — a new field cannot opt itself into having its values recorded |
| `app/services/journey.py`, `analytics.register_observer`, `Form`/`Manager` `on_timeout` | `tests/behavioral/contracts/test_journey.py` + `tests/behavioral/scenarios/test_journey_flow.py` — one session is one message, finalizing is idempotent, and an edit lists field names only |
| any new `on_timeout` on a view | `tests/behavioral/contracts/test_view_timeouts.py` — only `Form` and `Manager` may define one, and a timeout never sends anything to the user |
| `app/services/transforms.py`, `ModalValidations` | YAML contracts + validation scenarios |
| `app/data/` | persistence assertions in scenarios |

## Risk-based coverage priorities

- **High**: form engine, manager view, state management, YAML loading,
  localization resolution, persistence orchestration, reusable components.
- **Medium**: validators/transforms, edit/resume/cancel/back navigation,
  command families sharing a configuration shape.
- **Low**: isolated visual details and copy that does not affect behavior.

Do not chase exhaustive coverage; add scenarios where bugs and risk
appear. When a new command adopts a shared component with a meaningfully
new configuration shape, add it to the parametrized consumer contract
(`tests/behavioral/contracts/test_manager_form_consumers.py`).

## Feature completion criteria

A feature that touches shared infrastructure is complete only when: its
own scenarios pass; representative existing consumers still pass
(`pytest -m "shared_contract"`); relevant localization scenarios pass; and
existing saved configuration still loads and displays. The final report
must include the `## Regression protection` section defined in
`.claude/rules/bug-fix-protocol.md`.

## Known limitations (what still needs a real Discord guild)

Now covered offline (behavioral suite): the full Manager lifecycle through
its real buttons (pause/unpause/disable with the typed ConfirmationModal,
add/remove item through the real sub-form and remove Select), the
EditCommand flow, the file-upload logic (fake attachment, real re-upload
path, recorded dump channel), Twitch subscribe/unsubscribe orchestration
against the recording API mock (`is_dev` gate opened per scenario), view
timeout configuration (pinned by contract — Keiko has no custom
`on_timeout` behavior), and Discord's Components V2 hard limits (≤40
components, ≤4000 chars of text) checked against the real cards.

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
2026-07-27): `tests/test_form.py` and `tests/test_form_state.py` test
private methods against hand-written step dicts; `tests/test_birthdays_manager.py`
asserts callbacks the tests themselves inject; `tests/mocks/discord.py`'s
`MockInteraction` predates the behavioral harness and has no consumers;
`tests/generators/` document shapes have drifted from what the engine
really persists (verified by the behavioral persistence assertions —
prefer the shapes in `tests/behavioral/contracts/`). Migrate a unit test
to a behavioral scenario only when the scenario clearly supersedes it and
the change is low risk.
