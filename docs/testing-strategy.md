# Keiko testing strategy

How Keiko is tested, layer by layer, and how bugs become permanent coverage.
Companion doc: `docs/form-scenario-testing.md` (how to write behavioral
scenarios and golden paths). Architecture reference:
`docs/form-configuration.md`.

## The layers

| Layer | Scope | Where | Runs |
|---|---|---|---|
| 1. Unit | pure logic: formatters, date utils, services | `tests/test_*.py`, `app/**/*_test.py` | every run |
| 2. Platform contracts | the form platform against its own contract: every shipped YAML compiles, the definition schema refuses bad rules, `decide` on one session and one event, the invariants I1 to I9, the boundary lines, the feature modules, the adapter choreography | `tests/forms/` (`form/`, `features/`, `discord/`, `test_invariants.py`, `test_boundary.py`) | every run |
| 3. Behavioral scenarios | full admin flows through the real platform, offline | `tests/behavioral/scenarios/`, `contracts/`, `regressions/` | every run |
| 3b. Golden transcripts | every canonical admin path of every form, byte for byte, in both locales where copy differs | `tests/behavioral/golden/` (`docs/form-scenario-testing.md`, "Golden transcripts") | every run |
| 4. Discord adapter fakes | the fake interaction surface itself | `tests/behavioral/harness/` + `test_harness.py`, `test_golden_harness.py` | every run |
| 5. Live smoke | what no simulator can represent | manual, private test guild | on demand only |

Everything through layer 4 is offline, deterministic, needs **no Discord
token and no network**, and runs in `make test`. `make lint` (ruff and
`mypy --strict` over `app/settings/` and `tests/forms/`) is part of the gate
for any change under `app/settings/`.

## Why a project-owned harness (research, accessed 2026-07-26)

The repo pins `discord.py` 2.7.1 and uses Components V2 (`LayoutView`,
`Container`, `TextDisplay`, `MediaGallery`) and `ui.Label`, stable since
2.6, plus `ui.FileUpload`, stable since 2.7. No external test framework
covers that surface:

`app/services/cdn.py` also reaches past that library's public surface: it calls
`bot.http.request(Route("POST", "/attachments/refresh-urls"))`, an endpoint
discord.py does not wrap, to re-sign an attachment link Keiko already uploaded.
It is the one place that does so, and `tests/behavioral/regressions/test_welcome_design_previews.py`
fakes the response, so a discord.py bump or an API change shows up there first.

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
welcome banner rendering (`create_banner`, `draw_banner` and
`cdn.upload_asset` are stubbed by an autouse fixture in `tests/conftest.py`,
so no test fetches an image unless it puts the real ones back on purpose).

## Running

```bash
make test                                     # everything (unit + platform + behavioral)
make lint                                     # ruff + mypy --strict on app/settings
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
| `app/settings/form/` (`form.py`, `form_state.py`, `form_state.py`, `conditions.py`, `components.py`, `responses.py`, `summary.py`) | `pytest tests/forms -q` + entire `tests/behavioral` — the goldens will show any change an admin can see |
| `app/settings/form/form_yaml.py` | `pytest tests/forms/form -q` (every shipped form must still compile, every fixture must still be refused) + `pytest tests/behavioral/golden -q` |
| `app/settings/form/actions/` (a step kind, a card section, the manager panel in `manager.py`, the review in `review.py`) | `pytest tests/forms -q` + the scenarios of the forms that use the kind + `pytest tests/behavioral/golden -q` + `tests/behavioral/contracts/test_components_v2_limits.py` for cards and the panel |
| `app/settings/form/` (validators, transforms, formatters, copy, links, dates) | `pytest tests/forms -q` + validation scenarios (`test_block_links_flow.py`, `test_reminders_birthday_flow.py`, `test_subscription_orchestration.py`) + `pytest -m "shared_contract"` |
| `app/settings/features/` (a feature module, `feature.py`) | `pytest tests/forms/features -q` + that feature's scenarios + `tests/behavioral/contracts/test_manager_form_consumers.py` (every consumer's saved document still renders) + the persistence assertions of the scenarios |
| `app/settings/discord/` (`callbacks.py`, `transitions.py`, `views.py`, `interactions.py`, `interactions.py`) | `pytest tests/forms/discord -q` (dedup, stale clicks, the per-session lock, expiry, send-then-delete, modal first) + `pytest tests/behavioral/golden -q` + `tests/behavioral/regressions/test_view_lifecycle_regressions.py` |
| `app/settings/discord/observability.py`, `Decision.analytics` in `form.py` | `tests/behavioral/scenarios/test_analytics_funnel_flow.py` — including the assertion that nothing typed into a modal reaches an event — + `tests/behavioral/contracts/test_analytics_catalog.py` + `tests/behavioral/scenarios/test_journey_flow.py` + `tests/forms/discord/test_discord_adapter.py` (a clean click posts nothing; a failed commit posts no engine lines and puts a failure line on the story) |
| `app/services/metrics.py`, `_answer_in_time` / `_answered` and the counts in `Runtime.handle` (`callbacks.py`), `run_feature_command` (`app/components/buttons.py`), the error handler's count | `tests/test_metrics.py` (closed labels, never an id; every lag tick is kept) + the five `counted` tests in `tests/forms/discord/test_discord_adapter.py` (one interaction, one outcome; a click or a button-opened feature that raises is counted failed and still raises) + `tests/behavioral/regressions/test_failed_commands_are_recorded.py` |
| `InMemorySessionStore`, `FormSession.touched`, `DiscordLimits.INTERACTION_TOKEN_SECONDS`, `Runtime.expire_stale`, `Runtime.sweep`, `Events.sweep_forms`, `ViewConstants.LONG_TIMEOUT_SECONDS`, `ViewConstants.FORM_SWEEP_SECONDS` | `tests/behavioral/contracts/test_form_expiry_sweep.py` (the loop runs and survives a failing pass; a pass closes the story as abandoned without engine messages and forgets ended sessions) + `tests/forms/discord/test_discord_adapter.py` (no edit with a dead interaction token, the newest click's webhook inside the window, a live child keeps its parent) + `tests/forms/form/test_form.py` (an accepted event moves the deadline, a rejected one does not) + `pytest tests/forms/discord -q -k expir` + `tests/behavioral/scenarios/test_journey_flow.py` — an expired form loses its buttons and says so once, and never after a save |
| `app/components/buttons.py` (the generic buttons outside the forms), `app/components/embed.py` | entire `tests/behavioral` |
| `app/languages/form/*.yml` | `pytest tests/forms/form -q` + that form's scenarios + `pytest tests/behavioral/golden -q` (a copy change moves the form's goldens: list it in `docs/ux-changes.md`, then re-record with `--update-golden`) |
| anything an admin can see: a screen, a button order, an embed, a card, a transition (send/edit/delete) | `pytest tests/behavioral/golden -q` — the goldens are the UX contract; a diff means an entry in `docs/ux-changes.md` before re-recording |
| `docs/ux-changes.md` (merged as a union, `.gitattributes`) | `test_every_ux_change_has_an_id_of_its_own` in `tests/behavioral/golden/test_golden_transcripts.py` — two branches that took the same id merge into one id meaning two changes |
| `app/languages/{buttons,commands,errors}/` | `tests/behavioral/contracts/test_form_start_baseline.py` + one pt-br and one en-us scenario |
| `commands.*.yml` slash `desc:`/`name:` entries | `tests/behavioral/contracts/test_slash_command_copy.py` (Discord's 100/32-char sync limits — violations only surface at bot startup) |
| `gateway_intents` (`app/bot.py`) | `tests/test_bot_intents.py` — no presences, what the code reads stays requested, and nothing in `app/` reads a member's presence |
| context menus (`app_commands.ContextMenu` registrations, their callbacks and decorators) | `tests/behavioral/contracts/test_context_menu_registration.py` (the tree is only built at startup) |
| `app/data/indexes.py`, new collections | boot the bot once: `ensure_indexes` runs in `create_app` and the offline mock treats `create_index` as a no-op; offline, `test_moderations_and_every_feature_collection_hold_one_document_per_guild` and `test_a_collection_with_duplicate_guilds_never_stops_the_other_indexes` in `tests/behavioral/regressions/test_one_record_of_whether_a_feature_is_on.py` (every index is tried on its own, so a collection that still holds duplicate guilds only loses its own unique index) |
| `located` and the `audit.reminders` TTL index (`app/data/indexes.py`), `insert_renewal_reminder` and `stamp_hub_confirmation` (`app/data/reminder.py`), `Commands.YOUTUBE_RENEWAL_ROW_SECONDS` | `tests/test_renewal_rows_expire.py` — a renewal row written now expires once nothing has renewed it for `YOUTUBE_RENEWAL_ROW_SECONDS`, every renewal the hub takes moves its expiry, a row without `expires_at` (v0.9.0's) never gets one, the TTL index is created in the `audit` database, and an index names its database before the first dot — + the YouTube and reminder webhook regressions below |
| `is_feature_on` (`app/services/cogs.py`), `set_feature_enabled`, `leave_guild`, `pause_all_moderations_by_guild`, `update_moderations_by_guild` (`app/services/moderations.py`), the lifecycle commits of a feature module (setup, pause, unpause, disable, the birthday module's own), `BirthdayFeature.open` | `tests/behavioral/regressions/test_one_record_of_whether_a_feature_is_on.py` (the saved document decides whether a feature is on and every reader asks the same function, a document saved before `enabled` follows the old flag, the old flag is still written for v0.9.0, a failed flag write still drops the cache, a paused birthday feature opens its panel with Unpause and an edit keeps it paused, a guild Keiko left has nothing on for one Redis call when Redis is down) + `tests/behavioral/contracts/test_setup_dashboard.py` + `tests/behavioral/contracts/test_setup_permissions.py` + `tests/behavioral/regressions/test_guild_lifecycle_logging.py` + `tests/forms/features -q` + the reminders_birthday goldens |
| `app/services/analytics.py`, `app/analytics/catalog.yml`, `app/services/analytics_sink.py` | `tests/behavioral/contracts/test_analytics_catalog.py` (the catalog and the code must agree in both directions) + `tests/test_analytics_storage.py` (what becomes a document and what stays a counter) |
| `app/logger.py`, `app/services/trace.py`, anything opening a trace | `tests/behavioral/contracts/test_logger_trace.py` — one unit of work is one Discord message, ordered, and a broken sink never breaks the work — plus `tests/behavioral/contracts/test_debug_logs.py`, which runs both handlers on the same logger: adding a sink must not degrade the embed, and both must read one identity |
| `app/services/debug_logs.py`, `app/services/logs_archive.py`, `app/data/logs.py`, `StoredLogsHandler` | `tests/behavioral/contracts/test_debug_logs.py` — recording never blocks or raises, persisting never logs (a `logger.*` call in this path is an unbounded write storm), the whole traceback survives, and the daily export stays inside its day |
| `app/lifecycle.py`, `__main__.py`, `Analytics.cog_unload` (`app/cogs/analytics.py`), `DBConfigs.FATAL_START_*`, `DBConfigs.READY_PHRASE` | `tests/behavioral/contracts/test_graceful_stop.py` — a real SIGTERM closes the bot on its own loop and both queues are written before the process exits with 0, with no window around installing the handler or the bot returning, and a second SIGTERM at any line the first one's handler runs never blocks it; the bot says it is ready once on the console and in `guild.logs`, however often it reconnects, and never posts it to Discord; a bot that fails to run writes them and exits with 1, after the fatal-start backoff when Discord refused the start (a bad token, a missing intent, a gateway close Discord never lifts), writing what the wait logged; a SIGTERM during that wait exits with 0 at once, and one during the write that follows it lets the write finish and exits with 0; a refused start is one CRITICAL console line that begins with the phrase the deploy check looks for; a failure while a stop was asked exits with 0; a bot that closes itself writes them and keeps the process; a SIGTERM after that ends it at once, and one during the writes or while a failure is logged waits for them |
| `.github/workflows/ci.yml`, `.github/workflows/release.yml`, `Dockerfile`, `requirements.txt`, the Python targets in `pyproject.toml` | `tests/test_release_workflow.py` — the image, CI, the release check and the lint targets name one Python version, the suite runs on the discord.py release the image installs, a pre-release never builds or deploys, and the deploy stops the bot with a grace period before removing it, publishes 5000 and 8000 on localhost only, caps memory and swap and passes `APP_VERSION`; its check, run as the step's own script under `bash -e` with a fake `docker` and `sleep`, reads the container's log once per poll, fails at once on a restart or on `DBConfigs.FATAL_START_PHRASE`, says so when `docker logs` itself fails, passes only once `DBConfigs.READY_PHRASE` is in the log and the bot has stayed up `SETTLE_SECONDS` and still runs with no restart, and gives up at a deadline shorter than the fatal-start backoff |
| `app/data/*_async.py`, any `asyncio.to_thread` seam, or any new `requests` / `pymongo` / `time.sleep` call reached from a coroutine | `tests/behavioral/regressions/test_event_loop_is_never_blocked.py` — the test counts how many times the loop got control back while the call ran, because a blocked loop stops the whole bot and spends Discord's three-second interaction budget — + `tests/forms/test_boundary.py` (nothing under `app/settings/` may block) |
| `app/services/images.py`, `app/services/cdn.py`, `draw_banner`, `create_banner` or `generate_design_previews` in `app/services/welcome_messages.py`, `app/assets/welcome/` | `tests/behavioral/regressions/test_welcome_design_previews.py` (the gallery downloads nothing from outside Discord, the example stays light and is uploaded once, previews are drawn together, a server without an icon or with an unreachable or expired background still gets a banner, a welcome carries its banner as an attachment and a channel that refuses files gets it by link, a hanging download gives up, the image cache stays bounded) + `tests/behavioral/regressions/test_image_downloads_are_bounded.py` (a picture is refused by its type, its size or its pixels before it is held or decoded, a large JPEG is decoded at a reduced size, avatars are asked at the size the banner draws them, Pillow's process-wide cap is set before the bot starts, and the cache fits in a tenth of the deploy's memory) + `test_drawing_a_welcome_banner_never_freezes_the_bot` in `test_event_loop_is_never_blocked.py` + `tests/test_welcome_messages.py` + the welcome_messages goldens |
| `connect_redis` in `app/__init__.py` or `DBConfigs.REDIS_SOCKET_TIMEOUT_SECONDS` | `test_a_stalled_redis_gives_up_instead_of_holding_a_thread` in `test_event_loop_is_never_blocked.py` |
| `get_cog_data_or_populate`, `remove_cog_cache_by_guild`, `remove_all_cache_by_guild` (`app/services/cache.py`), `DBConfigs.COG_CACHE_*`, `write_document` / `update_document` / `commit_disable` (`app/settings/features/feature.py`) | `tests/behavioral/contracts/test_config_cache.py` (nothing saved is cached briefly and reads as a miss to v0.9.0; a Redis outage answers from Mongo for one Redis call and one log line per window, invalidations included, and the invalidations it skipped are sent as soon as Redis answers; an outage's log line is a message of its own on the log channel, even from a listener; a Mongo outage serves what this process last read from Mongo, however long ago, from a bounded memory, and fails at once for anything else; a read that raced an invalidation made by this process neither keeps nor leaves cached what it read, even when its Redis write lands and then times out or an older delete is still in flight; an invalidation never fails a save) + `tests/behavioral/regressions/test_a_save_is_never_undone_by_the_cache.py` (a read racing a save or a Disable never outlives it, a cache entry lives minutes) + `tests/behavioral/regressions/test_event_loop_is_never_blocked.py` |
| `remove_all_cache_by_guild`, `increment_redis_hash`, `get_redis_hash_counters`, `get_redis_counters`, `delete_redis_keys` (`app/services/cache.py`), `record_blocked_links`, `get_blocked_links_stats`, `forget_counters` (`app/services/block_links.py`), `count_blocked_links` (`app/data/block_links.py`), the `BlockLinks` cog (`app/cogs/block_links.py`), `Commands.REDIS_BLOCK_LINKS_COUNTERS`, `Commands.BLOCK_LINKS_COUNTERS_TTL_SECONDS` | `tests/behavioral/regressions/test_block_links_counters.py` — a blocked link adds to one hash per server in one round trip that keeps it `BLOCK_LINKS_COUNTERS_TTL_SECONDS` (400 days) past the server's last block, and to Keiko's own record in `guild.block_links_totals` (the total and each site, no server, no member), off the loop; the Stats add the loose keys v0.9.0 wrote, found by the names the hash and the 90-day records know, so no number drops at the deploy; neither the Stats nor leaving a server ever walk the keyspace (`SCAN`, `KEYS`); the settings cache forgets only its own keys, and leaving deletes the server's counters by name (its hash and total first, so a failed read never keeps them) and keeps Keiko's record — + `tests/behavioral/regressions/test_block_links_answer.py` (the answer pings what the admin wrote, a failed delete names websites, never links) + the leave test of `tests/behavioral/contracts/test_config_cache.py` |
| `DiscordLogsHandler` session errors, `journey.recovered` or `observability.log_effects` | `tests/behavioral/regressions/test_recovered_session_errors.py` + `tests/behavioral/contracts/test_journey.py` + `tests/behavioral/regressions/test_foreign_logs_stay_out_of_discord.py` |
| `is_foreign`, `is_muted`, `NOISE_MARKERS` (`app/logger.py`) | `tests/behavioral/regressions/test_foreign_logs_stay_out_of_discord.py` — a library writing about itself stays in `guild.logs` and out of the admin channel, and Keiko's own records still reach it |
| `Errors.on_app_command_error` (`app/cogs/errors.py`), the command tree's error handler, `Commands.COMMAND_FAILURE_STORE_SECONDS`, `insert_error_by_command_async` (`app/data/cogs.py`) | `tests/behavioral/regressions/test_failed_commands_are_recorded.py` — a failed command reaches the error channel with its traceback and `command.failed` before the person is answered, and `audit.errors` when Mongo takes it within the bound; the person is answered even when the answer fails, while the record is still being written and when recording raises; a burst of failures during a Mongo stall holds no thread of the default executor; the handler adds one message, the error, and a failure while recording reaches the error channel too — + `test_storing_a_failed_command_never_freezes_the_bot` |
| `app/services/reminders.py`, `app/integrations/reminder_webhook.py` | `tests/behavioral/contracts/test_reminders_api_contract.py` — reminders-api takes date_tz and time_tz as separate fields; folding the hour into date_tz is refused, and the refusal looks like an ordinary empty response |
| `app/services/reminders_birthdays.py`, the birthdays cog loop | `tests/behavioral/contracts/test_birthday_reminder_reconciliation.py` — a birthday saved without a reminder is found again and finished, a still-failing one is left for the next pass, and the loop survives a pass that raises |
| `app/cogs/heartbeat.py`, `metrics.record_loop_lag`, `Commands.HEARTBEAT_*`, `HEARTBEAT_URL`, `AppConfig.get_optional_parameter`, the `urllib3` level in `app/logger.py` | `tests/behavioral/contracts/test_heartbeat.py` (the lag is read against the tick's schedule, two late ticks in a row both read late, a freeze of the real loop stays on the metric after the catch-up, a ping only with a URL and a connected gateway, a failing ping stays out of Discord and never logs the URL, not even at DEBUG, the build info) + `test_the_heartbeat_ping_never_freezes_the_bot` + `tests/test_config.py` (a missing or unreadable parameter leaves the heartbeat off) |
| `tools/keiko/birthdays/repair.py` | `tests/tools/test_birthday_repair.py` — the tool names the database it is about to write to and refuses a prod run that resolved to localhost |
| `TERMINAL_OUTCOMES`, `ACTIONS`, `OUTCOME_ICONS`, `LINE_KINDS`, `CONDENSED_OUTCOMES`, `FAILURE_EVENTS` (`app/services/journey.py`) | `tests/behavioral/contracts/test_journey.py` — adding an item is a step, not an ending; the title vocabulary has one owner; a saved or edited session condenses to its milestones and a summary, live and on Refresh; a failure keeps every step; the opening line is copied once |
| `trace_scope`, `app/decorators.py`, `ExecuteCommandButton` | `tests/behavioral/contracts/test_trace_logging_bus.py` — everything a trace shows must travel through `logging`, once, or it renders in Discord and exists nowhere else |
| `webhook_trace`, `open_webhook_trace`, `settle_by_status` (`app/webhooks/__init__.py`), `Trace.publishing` | `tests/behavioral/regressions/test_quiet_webhooks.py` — the healthcheck opens no trace, a request and its jobs post only when they failed, a warning stays in `guild.logs`, a route that raises or answers a 5xx other than 503 still posts its error and its trace, a 503 without an error (deliver again later) posts nothing and stores no error, a 4xx posts nothing and is counted by route and status, and every line still reaches `guild.logs` — + `test_the_work_a_trace_hands_over_keeps_its_publishing_rule` in `tests/behavioral/contracts/test_logger_trace.py` + `test_webhook_background_trace.py` |
| `build_trace_embed`, `TraceTitles` | `tests/behavioral/contracts/test_trace_embed_format.py` — one emoji per kind of event, metadata in fields, timeline inside the 1024 field limit |
| `app_version()` (`app/config.py`), the analytics envelope, `build_document`, `build_trace_embed` | `test_every_stored_event_names_its_release_and_an_id_of_its_own` (`tests/test_analytics_storage.py`) + `test_every_stored_line_names_the_release_that_wrote_it` and `test_every_field_the_sink_builds_is_a_column_the_index_stores` (`test_debug_logs.py`) + `test_the_header_names_the_release_that_wrote_the_message` (`test_trace_embed_format.py`) + `tests/test_config.py` |
| `feature_config_states` (`app/services/cogs.py`), a service's `config_states`, `feature_keys()` (`app/settings/features/__init__.py`) | `tests/test_config_usage.py` + `tests/behavioral/regressions/test_analytics_report_truthfulness.py` — a feature declares how its configuration is read by defining `config_states`, it must answer in the keys the form declares, and it must never report a value the card only drew |
| `trace.run_traced`, `Trace.handover`, `app/webhooks/jobs.py` | `tests/behavioral/regressions/test_webhook_background_trace.py` — the first job continues the request's message (one event, one message), a second job gets its own, a job that cannot be scheduled leaves the request's message, deferred work never writes into a posted trace, and the request names its subject before scheduling |
| `app/webhooks/reminder.py`, `ReminderWebhook.verify_basic_auth`, `app/webhooks/birthday_handler.py`, the reminder lookups of `app/data/birthdays.py` and `app/data/reminder.py`, `ask_the_hub` (`app/services/notifications_youtube_video.py`), `reminder_time` (`app/integrations/reminder_webhook.py`), `stamp_hub_confirmation` and `mark_lapse_reported` (`app/data/reminder.py`), `count_servers_following`, `zone_or_utc` and `nearest_mm_dd_occurrence` (`app/services/dates.py`), any import at the top of a route module | `tests/behavioral/regressions/test_reminder_webhook_trusts_only_the_reminders_api.py` (on the production log bus) — no credentials, no action; an id Keiko did not create does nothing and never reaches reminders-api; a renewal acts on what Keiko stored; a renewal YouTube or the hub cannot take now (a timeout, a hub 5xx or 429) is moved an hour ahead by Keiko and does not fail the callback, one whose channel is gone or that the hub refuses for good (4xx) is an error with its context and moves four days ahead, every move is computed in reminders-api's zone and sends the hour, and only reminders-api refusing the move fails it; a renewal the hub takes stamps the youtuber's record, and one that keeps failing tells the error channel once, half a day before that lease ends, again only after the hub has taken it since; a renewal whose youtuber no server follows, paused or not, deletes itself whether its id was stored as a number or as text, the one place a renewal is deleted; a reminder that fails is an error and the next one in the callback still runs, then the callback answers 500, and if reminders-api sends it again (assumed, not verified) every reminder kind survives it (a birthday celebrates each member once a year, a renewal subscribes and reschedules again); the routes still import before the databases connect — + `tests/behavioral/regressions/test_birthday_celebrated_once_per_guild.py` — a reminder celebrates only its own items, once the bot is ready, each member once per guild and per year of the birthday celebrated (the date nearest to the delivery, early or late), claimed only once the message is drawn; a guild Discord refuses or whose channel is gone is counted without stopping the next, and so is a member whose message cannot be drawn or sent, both as errors with their context, while a real `KeyError` is never read as a missing server — + `app/services/dates_test.py` + `tests/behavioral/contracts/test_reminders_api_contract.py` |
| `app/webhooks/youtube.py`, `YoutubeClient` (`verify_hub_signature`, `callback_token_matches`, the Data API reads and `YoutubeAPIError`, `subscribe_to_new_video_event`, `unsubscribe_from_new_video_event`), `parse_video_notice`, `prepare_video_announcement`, `announce_video`, `ask_the_hub` and `send_to_hub`, `subscribe_youtube_new_video` and `unsubscribe_youtube_new_video` (the YouTube feature's settings hooks), `channel_of_topic`, `claim_redis_key` / `release_redis_key` (`app/services/cache.py`), `DestinationNotFound` (`app/exceptions.py`), `MAX_CONTENT_LENGTH` in `app/api/config.py`, `YOUTUBE_HUB_SECRET` in `AppConfig`, `resubscribe_followed_channels` and the notifications cog's startup loop | `tests/behavioral/regressions/test_youtube_webhook_trusts_only_the_hub.py` (on the production log bus) — an unsigned or wrongly signed notice posts nothing, a hostile body is handled inside a time budget, a video is announced once and only from its own channel, a notice that fails before the announcement is handed to the bot loop is left for the hub to deliver again, the announcement waits until the bot is ready, links to the video, and one unreachable server does not stop the others while an unexpected failure is an error with its context, a body over the cap is a 413 in both environments, Keiko's requests carry a callback with the channel's token and only a subscribe or an unsubscribe of an exact feed topic on that callback is confirmed (a topic that is not the channel's own feed is refused even there), the hub's own denial is an error that names the channel and never its reason, a stranger's GET (with no token, a made-up one or another channel's) is refused and leaves Keiko's own verification confirmable, a notice without a token is a 410 and one on another channel's callback is refused like a bad signature, a refusal names its mode and channel (only an id of YouTube's shape, `UC` and 22 more) and nothing a stranger wrote, neither the callback token nor the API key reaches the logs, the error a Data API failure raises holds neither a cause nor a context, and the answer of a request to the hub keeps no exception, since its request's body holds the hub secret and the token, an error answer from the Data API raises instead of reading as "not found" and a renewal it stops is tried again an hour later, a real `KeyError` in a delivery is an error rather than a missing server, every call to YouTube has a timeout, every followed channel is subscribed again on its tokened callback at start, only the ones the hub accepted count as renewed and each of those is stamped, and without a hub secret the bot still boots, refuses every notice and asks the hub for nothing — + `tests/behavioral/regressions/test_youtube_settings_never_wait_on_youtube_to_let_go.py` — removing a youtuber and disabling YouTube complete whatever YouTube answers, the hub's unsubscribe being best-effort, an error with its context when it fails, and never sent while another server, paused or not, still follows; the unsubscribe never deletes the renewal, so an edit that fails after it keeps its youtuber renewed; a failed lookup fails setup, adding and editing as a timeout does and saves nothing, while a hub that cannot take a new youtuber gets Keiko's renewal an hour later and one that refuses it is an error with its context — + `test_renewing_the_youtube_subscriptions_at_start_never_freezes_the_bot` in `test_event_loop_is_never_blocked.py` |
| `fill_placeholders` (`app/services/utils.py`), `parse_streamer_message` of the Twitch and YouTube notifiers | `TestFillPlaceholders` in `app/services/utils_test.py` + `tests/test_notifications_youtube_video.py` + `tests/test_notifications_twitch.py` — one pass, only the names given, capitals are text, the link is added whenever its placeholder is missing, and a width or an attribute in braces stays literal and small |
| `LogTypes.REPORTED_EVENT_TYPES`, `Trace.reported_event`, `TraceFoldingHandler.emit` | `tests/behavioral/regressions/test_guild_lifecycle_logging.py` + `tests/behavioral/contracts/test_trace_embed_format.py` — a listener that reported an event publishes, a routine one stays silent, and title and colour come from the same pair |
| `tools/keiko/logs/*` | `tests/tools/` — both file shapes on the Discord logs channel still parse, re-syncing adds nothing, and `app/` never imports `tools/` |
| `app/views/report_browser.py`, any command composing it | `tests/behavioral/contracts/test_report_browser.py` — choosing a section edits the message instead of sending another, and sections build lazily |
| `app/views/records.py`, `MemberPicker` | `tests/behavioral/contracts/test_records_view.py` + `tests/behavioral/scenarios/test_block_links_records_flow.py` |
| `app/views/setup.py`, `app/cogs/base/setup.py`, the greeting's `DashboardButton`, `buttons.setup.*` and `commands.setup.embed.*` copy | `tests/behavioral/contracts/test_setup_dashboard.py` — a Components V2 card with Keiko's picture, one row per feature with its button beside it, Set up or Manage from the saved state, inside Discord's limits in both locales |
| `app/services/setup.py` (`permission_report`, `feature_problems`, `guild_history`, `history_fields`), `SETUP_FEATURES` permission declarations, `parse_history_data(label_for=)` | `tests/behavioral/contracts/test_setup_permissions.py` (fine when everything is granted, a missing channel or server permission, a deleted channel, a role above Keiko for auto roles, block links exemptions need nothing, nothing to check) + `tests/behavioral/contracts/test_setup_history.py` (every feature, newest first, each line named) |
| `app/services/help.py`, `app/cogs/base/help.py` | the /help command on a guild, and `tests/behavioral/contracts/test_setup_dashboard.py` (the Commands button opens it) |
| `app/settings/discord/layout.py` | `pytest tests/behavioral/golden -q` (the manager panel draws through it) + `tests/behavioral/contracts/test_setup_dashboard.py` |
| `Trace(quiet=, is_admin=)`, `trace.settle`, `Trace.finish`, `ConfirmActionView(trace_name=)`, `app/cogs/birthdays.py` | `tests/behavioral/contracts/test_trace_logging_bus.py` + `tests/behavioral/contracts/test_trace_embed_format.py` + `tests/behavioral/regressions/test_birthday_personal_outcomes.py` — a quiet trace posts nothing but stores its lines, a named result survives unless the work failed, the Admin field renders only when known, and /birthday names its outcome, never the date |
| `app/services/admin_digest.py`, `app/cogs/analytics.py` loops | `tests/test_admin_digest.py` — the digest must survive an empty install and stay readable on a quiet week |
| `app/cogs/archive.py`, `app/services/archive.py`, `app/data/archive.py`, `iter_events_between` and `build_profile_operation(upsert=)` (`app/data/analytics.py`), `gzip_lines`, `part_names`, `build_file`, `send_files` (`app/services/logs_archive.py`), `Commands.ANALYTICS_ARCHIVE_*`, `archive.without_identity`, `archive.months_to_archive` | `tests/test_monthly_events_archive.py` — each complete month's events, and only them, go out once in one file with one message, sealed so only the private key opens them, with no server, member or other Discord id (only the fields of `Commands.ANALYTICS_ARCHIVE_FIELDS`, no `*_id` property) and sessions hashed under a key of each pass (no stored session id, no hash shared by two passes), and come back with their types; every month no pass posted whose first day the events still reach goes out oldest first, and one that fails never holds the next; without a valid public key nothing is read or posted and the log says why; a month without events posts nothing; a month over the upload limit once sealed goes in halves under it; a missing channel and a failing pass say so and never stop the loop; the logs tool never takes the archive for a log file; a guild with a profile gets its current size bucket on it without losing the rest, and no profile is ever created, a forgotten guild included; neither job holds the loop — + `tests/test_sealing.py` + `tests/behavioral/contracts/test_debug_logs.py` (the daily export still stays inside its day) + `tests/test_logs_files_retention.py` (the names the archive gives stay a kept file) |
| `app/services/sealing.py` (`age_recipient`, `seal`, `plaintext_limit`), `DBConfigs.AGE_*`, `BACKUP_AGE_PUBLIC_KEY` (`app/config.py`), the `pyrage` pin | `tests/test_sealing.py` — a sealed payload opens with the private key and no other, a missing, blank or malformed public key reads as no key, the largest payload `plaintext_limit` allows fits the upload limit once sealed, and the key is read from SSM, optional, or from the environment — + the archive and backup suites |
| `app/cogs/backup.py`, `app/services/backup.py`, `app/data/backup.py`, `DBConfigs.BACKUP_*`, `Commands.BACKUP_HOUR` / `BACKUP_MINUTE` / `BACKUP_RETENTION_DAYS` / `DAILY_LOGS_RETENTION_DAYS` / `LOGS_FILES_DELETES_PER_PASS`, `app/services/logs_files.py`, `app/data/logs_files.py` | `tests/test_daily_backup.py` — one sealed zip with every collection of Keiko's own databases (only the named ones of `configs`), documents restored with their `_id` and types once opened with the private key, nothing from another database and no credentials, error audit or debug logs, what is posted is never a readable zip and opens with that key and no other, a dump over the upload limit packed one collection at a time into sealed files under it with settings (renewal rows included) first and the collections that age out last, a collection too large alone named in the pass's one summary message and a warning, a failing pass logged as an error without stopping the loop, a missing channel or a missing or invalid public key building nothing and saying so, the logs tool never ingesting the backup — + `tests/test_logs_files_retention.py` (each kind of this bot's files goes past its age and stays inside it: backups 30 days, the daily logs, export and text file, 90, the monthly archive for good; every name the backup, the export, the archive and the rotating handler give is known by its kind, a `shared_contract("logs_files")` test, so a renamed file fails `-m shared_contract`; other authors, other names and messages mixing kinds stay at any age; old backups go only on a day a backup went out, old logs every day; a pass attempts at most its cap, failed deletes included, oldest first, and says how many are left; once a pass leaves nothing, the next walks only inside the longest age, yet a log young when the mark was set still goes once old and a day without a backup never moves the mark; a file already gone counts as deleted; failures are grouped by error and counted as left; a history read or mark write that fails says the pass stopped and after how many; what a pass left is tried again) + `tests/test_sealing.py` + `tests/behavioral/regressions/test_the_daily_backup_never_freezes_the_bot.py` (the backup, and the read and move of how far back the channel is clear) |
| `analytics.records_raw_choice`, a new field or step kind in a form YAML | `tests/test_config_usage.py` + `test_no_free_text_or_id_field_of_any_form_is_ever_tabulated` — a new field cannot opt itself into having its values recorded |
| `app/services/journey.py`, `analytics.register_observer`, `open_journey`/`close_journey` in the adapter | `tests/behavioral/contracts/test_journey.py` + `tests/behavioral/scenarios/test_journey_flow.py` — one session is one message, finalizing is idempotent, and an edit lists field names only |
| `journey.recent_attempts`, the footnote read of `open_journey`, `analytics_reports.setup_sessions` | `tests/behavioral/regressions/test_a_panel_reads_one_server.py` — the footnote reads only this guild's events of the last 24 hours and the read never holds the loop — + `tests/test_analytics_dropoff.py` (the all-guild reports keep their sessions) |
| `app/settings/form/lookups.py`, `FeatureModule.prefetch(lookup, context)`, `TextStep.lookup_answers`, `normalize` | `tests/forms/form/test_lookups.py` + `tests/forms/features/test_features.py` (the Twitch, YouTube and StreamElements lookups, an outage leaves no count) + `test_a_modal_that_needs_a_lookup_is_answered_before_the_lookup_runs` + `tests/behavioral/scenarios/test_subscription_orchestration.py` |
| `app/services/stream_elements.py` (`check_message`, `create_response_embed`), `AppConfig.PREFIX` read by the listener and by the adapter (`Context.prefix`, `OpenContext.prefix`) | `tests/behavioral/regressions/test_stream_elements_reply.py` — a command with the configured prefix is answered whatever the asker's avatar, no other prefix is, the footer follows the server's language, and the reply, the form and "My version" name the configured prefix, whatever it is — + the stream_elements_commands goldens |
| `get_reply_in_cache_or_populate`, `get_commands_in_cache_or_populate`, the error context of `check_message` (`app/services/stream_elements.py`), `Commands.REDIS_STREAM_ELEMENTS_COMMANDS`, `STREAM_ELEMENTS_*_CACHE_SECONDS` | `tests/behavioral/regressions/test_stream_elements_commands_cache.py` — the streamer's commands are fetched once a day per StreamElements channel and a streamer with none once every few minutes, a failure is never cached, a command known or not is answered from that cache, each asker's placeholders are filled when the reply is sent, the list keeps `!name`, an error names how long the typed command was and never its text, and v0.9.0's cache key is left alone — + `tests/forms/features/test_features.py` (the panel's count) |
| `Events.on_message` | `tests/behavioral/regressions/test_direct_messages.py` (a DM reaches no admin channel and no feature) + `tests/behavioral/regressions/test_stream_elements_reply.py` + `tests/behavioral/regressions/test_a_prefixed_message_gets_one_answer.py` |
| `Errors.on_command_error` (`app/cogs/errors.py`), `app/services/prefix_features.py`, the `answers_prefix` mark on `Commands.SETUP_FEATURES`, `messages.prefix-hint.*` | `tests/behavioral/regressions/test_a_prefixed_message_gets_one_answer.py` — a prefixed message gets only the feature's answer where a prefix feature is on, one hint in the server's language naming the configured prefix and each marked feature's command where none is, and the slash hint from the language files in a DM — + `test_a_prefixed_message_no_command_matches_never_freezes_the_bot` |
| `Edit.part`, `edit_by_field`, `edit_by_item`, `PanelPart`, `manage.groups`/`panel_groups`/`part_targets`, `review.render` | `tests/forms/form/test_decide.py` (a part edit opens only its section or select, saves from the manager, returns to the review, cancels on Back, one block per item) + `tests/behavioral/scenarios/test_edit_flow.py` + `tests/behavioral/contracts/test_components_v2_limits.py` (every manager panel and setup review) + `pytest tests/behavioral/golden -q` |
| `AsideAction.confirm`, `Runtime._aside` | `test_an_aside_that_asks_first_spends_its_cooldown_only_when_confirmed` in `tests/forms/discord/test_discord_adapter.py` + `tests/test_default_roles.py` |
| `EffectFailed`, `Executor.run`, `Runtime._run`/`handle`, `journey.recovery_key` | `test_a_finalize_that_fails_inside_a_commit_is_reported_once` + `tests/behavioral/regressions/test_recovered_session_errors.py` (a check when the same person uses the same feature again, none for another person, forgotten after the window) |
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
`app/settings/` also needs `make lint` green. The final report must include
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

Irreducibly manual / live-only: drift of the real Discord API and of
the discord.py releases (the fakes encode our model of Discord — a scheduled
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
