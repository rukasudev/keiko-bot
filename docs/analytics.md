# Analytics and observability reference

Keiko records four different things, and confusing them is what makes
instrumentation rot. This document maps each one to where it lives, and shows
how to add a metric to a new feature (usually: you do not have to).

| Kind | Question it answers | Where it lives | Retention | Lossy |
|---|---|---|---|---|
| **Operational** | Is the system healthy? | Prometheus → Grafana (`app/cogs/prometheus.py`) | Grafana's | yes |
| **Product analytics** | How do people use Keiko? | `guild.analytics_*` (this document) | 90d raw, counters 13mo | **yes, by design** |
| **Audit** | Who changed what, when? | `events.<cog_key>` (`insert_cog_event`) | permanent | **no** |
| **Debug** | Why did it break? | `guild.logs` + the daily file on the logs channel | 30d hot, file permanent | yes |

Boundary rules:

- Latency, memory and gateway events are **operational**. They never become
  product events.
- Product analytics is **never a source of truth**. If an event is lost,
  nothing breaks.
- Audit is **never lossy and never batched** — it answers "who turned my
  feature off", so it stays a synchronous write.
- A traceback is debug. The **type** of the exception is a metric.

---

## 1. The one function handlers call

```python
from app.services import analytics

analytics.emit("value.delivered", guild_id=guild.id, feature="welcome_messages")
```

`emit` never raises, never blocks and never retries. It validates the name
against `app/analytics/catalog.yml`, drops anything the catalog does not
declare, strips free text, and puts a small envelope on a thread-safe queue.
`Analytics` (`app/cogs/analytics.py`) drains that queue every few seconds.

Inside the form engine there is a shorter path that fills the session context
for you:

```python
self.emit_event("setup.step_viewed", interaction, step_key=..., step_action=...)
```

`emit_event` comes from `SessionAwareView` (`app/views/form_state.py`), mixed
into `Form` and `Manager`. It supplies `guild_id`, `user_id`, `feature`,
`source` and `session_id` so a call site only names what is specific to it.

## 2. Where instrumentation lives

> **Instrumentation lives in the shared seams the engine already has. A command
> handler never calls the analytics service.**

| Seam | File | Events it produces |
|---|---|---|
| `Form._update_form_step` | `app/views/form.py` | `setup.step_viewed` |
| `Form._go_back` | `app/views/form.py` | `setup.step_back` |
| `Form._finish` | `app/views/form.py` | `setup.completed` |
| `CustomModal.on_submit` failure branch | `app/components/modals.py` | `setup.validation_failed` |
| `on_done` of the configuration card | `app/views/summary_card.py` | `setup.required_missing` |
| `request_discard_confirmation` | `app/views/confirm_action.py` | `setup.discarded`, `setup.discard_recovered` |
| `send_command_form_message` / `send_command_manager_message` | `app/services/moderations.py` | `feature.setup_opened`, `feature.manager_opened` |
| `insert_cog_event` | `app/services/cogs.py` | every lifecycle event (see below) |
| `run_feature_command` | `app/components/buttons.py` | `command.invoked` from any entry button |
| `Events.on_interaction` / `on_guild_join` / `on_guild_remove` | `app/cogs/events.py` | `command.invoked`, `guild.joined`, `guild.removed` |
| `Errors.on_app_command_error` | `app/cogs/errors.py` | `command.failed` |
| `GreetingsView.send` | `app/views/greetings.py` | `guild.greeting_sent` |

### `insert_cog_event` is the audit + analytics facade

Every state change already passed through it, so `LIFECYCLE_ANALYTICS_EVENTS`
maps the audit key to its product event and both are written from one call:

| Audit event | Product event |
|---|---|
| `enabled` | `feature.enabled` |
| `edited` | `config.changed` |
| `paused` / `unpaused` | `feature.paused` / `feature.unpaused` |
| `disabled` | `feature.disabled` |
| `added` / `removed` | `feature.item_added` / `feature.item_removed` |

There are no new call sites for any of these.

## 3. Adding a metric to a new feature

**In most cases you do nothing.** A new YAML command inherits
`feature.setup_opened`, `setup.step_viewed`, `setup.validation_failed`,
`setup.required_missing`, `setup.step_back`, `setup.completed`,
`setup.discarded`, `config.changed`, the whole lifecycle and `command.invoked`,
because all of that lives in the engine. That is the test that the architecture
is right.

Work only exists when the feature delivers value in a way only it knows about:

1. Find the line where value is actually delivered — the `channel.send`, the
   `member.add_roles`, the `message.delete`.
2. Add one line: `analytics.record_value(guild.id, constants.MY_FEATURE_KEY)`.
   For a delivery Discord refused, `analytics.record_permission_failure(...)`.
3. If you genuinely need a new event, **add it to `app/analytics/catalog.yml`
   first**. `emit` drops undeclared names, and the contract suite fails the
   build for an emit that is not in the catalog — and for a catalog entry
   nobody emits.
4. Run `make test`.

## 4. The catalog

`app/analytics/catalog.yml` is the single source of truth for event names,
their properties, and their retention. It also declares the closed
vocabularies (`sources`, `results`).

```yaml
setup.validation_failed:
  version: 1
  class: [event, counter]
  description: A YAML validator refused the value submitted in a modal.
  emitted_at: app/components/modals.py — CustomModal.on_submit, failure branch
  actor: user
  required: [feature, session_id, step_key, validation, error_key]
  optional: [attempt_n]
  retention_days: 90
  cardinality_risk: low
```

`class` decides storage:

- `event` — one document in `guild.analytics_events` (TTL 90 days);
- `counter` — folded into `guild.analytics_guild_month`, **never a document**;
- `audit` — also written to `events.<cog_key>`.

Properties not listed under `required`/`optional` are dropped and counted in
`analytics.stats()["undeclared_props"]`, visible in `/admin pipeline`.

## 5. Storage

Three collections, all in the `guild` database, all registered in
`app/data/indexes.py`:

| Collection | Shape | Retention |
|---|---|---|
| `analytics_events` | one document per low-volume event | TTL 90 days on `ts` |
| `analytics_guild_month` | bucket per guild per month: `days.<dd>.<metric>` counters | TTL 400 days on `updated_at` |
| `analytics_guild_profile` | one document per guild, the derived state | TTL 365 days **after** `removed_at` |

The reason for the split is the free tier. A delivery event per welcome message
at a thousand guilds is hundreds of thousands of documents a month; as a
counter it is twelve documents per guild per year. Nobody needs the document of
the forty-thousandth welcome message — they need the count.

`metric_key` replaces dots in an event name (`value.delivered` →
`value_delivered`) because Mongo reads a dot in an update path as nesting.

The guild profile is what every dashboard reads, so a screen costs one document
instead of a collection scan. It is also the frozen snapshot `guild.removed`
reports — which is why that event is emitted **before**
`remove_all_cache_by_guild` runs in `on_guild_remove`.

### Redis

Only the hot window, under `guild:{guild_id}:analytics:*`, and **every key has
an `EXPIRE`** (`increment_redis_key_with_expiration`). A counter without a TTL
in a shared keyspace is how a free tier dies.

## 6. Privacy

Never collected, as a hard rule: message content, full URLs, any value typed
into a modal, file names, birthday dates, tokens, tracebacks, channel or user
names.

The rule is **structural, not a checklist**: the engine reports a step by its
`step_key` and `step_action`, both closed vocabularies that come from the YAML,
and never the answer. `analytics.records_raw_choice(step_action)` is the only
gate through which a chosen value may be recorded, and it only opens for steps
whose options are written in the YAML.

`tests/behavioral/scenarios/test_analytics_funnel_flow.py` types a secret into
a real flow and asserts it appears in no event. That makes the privacy rule
executable instead of documented.

`/admin forget <guild_id>` erases every analytics record of a guild.

## 7. Failure and kill switch

| Situation | What happens |
|---|---|
| Mongo down | the batch is dropped, a warning is logged, commands keep working |
| Redis down | counters are skipped, dashboards fall back to Mongo |
| Queue full | the newest event is dropped and counted |
| Event not in the catalog | dropped and counted |
| Exception inside `emit` | swallowed at the boundary, never propagates |
| Bot restarts | up to a few seconds of queued events are lost, by design |

`ANALYTICS_ENABLED=false` turns `emit` into a no-op without a deploy.
`/admin pipeline` shows emitted, flushed, dropped, unknown and queue depth.

---

# Debug logs: queryable, and older than the container

The Discord channels render logs for a person to read, and rendering costs
information: `format_traceback_message` keeps the last 15 frames and 3000
characters, so the frame that actually explains a failure is often the one that
was cut. The log file kept everything and then deleted it — the container has no
volume, so a deploy takes the current day with it.

Three pieces close that:

| Piece | Where | Holds |
|---|---|---|
| `StoredLogsHandler` | `app/logger.py` | every record, into a queue |
| `guild.logs` | Mongo, 30-day TTL | the hot window, with the full traceback |
| daily `.jsonl.gz` | the logs channel | the archive, one file per day |

## Everything on the timeline travels through `logging`

`TraceFoldingHandler` (`app/logger.py`) turns log records into timeline lines,
and `DiscordLogsHandler` suppresses the separate embed while a trace is open. So
a `logger.info` inside a trace becomes a line *and* reaches the daily file and
`guild.logs` from one call.

Writing straight to `trace.add()` skips that bus. It did, for command
invocation, and the result was `guild.logs` holding boot records and nothing
about the commands people ran. `trace_scope(..., opening=...)` is the supported
way to open a trace with a first line: it logs once, when the trace is created,
so a nested scope cannot repeat it.

## What is localized on the admin surfaces, and what is not

Declared exception, with a boundary, because the two halves were decided a turn
apart and the inconsistency was the real problem:

- **A sentence in Keiko's voice is localized.** The daily export message
  ("Here is my structured log for…") lives in `app/languages/messages/` and is
  resolved through `ml()`. It reads like Keiko talking, so it follows every rule
  that applies to Keiko talking, in both locales.
- **A structural label is English, in Python.** `TraceTitles.OUTCOME_LABELS`,
  and the embed field names beside them — `User`, `Guild`, `Duration`,
  `Interaction ID` — are a taxonomy for whoever is on call, not copy. They have
  always been hardcoded here; adding a locale file for them would localize half
  of one embed and leave the other half in English.

`docs/form-configuration.md` §7 lists user-facing strings in Python as a
documented exception. This is one, named and bounded rather than inherited by
analogy — and it is no longer only described here: the carve-out now lives in
`.claude/skills/keiko-writing-style/SKILL.md` ("Where these rules do NOT apply"),
which names the two surfaces it covers, the `/admin` commands and the log
channels, and says everything else stays under the full rules. That is where the
boundary moves if it ever moves again; this section explains why it exists.

## What the log channel calls an event

`TraceTitles` (`app/constants.py`) maps an event to one emoji and one label.
What happened wins; where it came from is the fallback. The same kind of event
always renders the same way, which is the point.

Deliberately separate from `commands.command-events.*`: that copy is shipped in
two locales and read by server admins, this one is read by whoever is on call,
and the two are free to move independently.

## What reaches the channel, and what only reaches the file

The handlers hang off the **root** logger, so every library in the process
writes into them. That is deliberate for storage — a gateway stall or a driver
timeout has to stay queryable — and wrong for Discord, where the channel is read
by a person asking what Keiko did.

`is_foreign` (`app/logger.py`) draws that line by origin: a record whose code
lives under `app/` is Keiko's, anything else is a library talking about itself.
It stays in `guild.logs` and the daily archive; it does not become an embed.

The cost of not having it was concrete. The webhook API is a development server
bound to `0.0.0.0` and `werkzeug` logs at ERROR, so a port scanner sending TLS
bytes to a plain HTTP port produced 984 error embeds — 467 in one day — and the
bot rate-limited itself posting them, 1790 times on that channel in three days.
A stranger on the internet could degrade Keiko by sending it garbage.

> The scanner is still reaching the port. Closing it is an infrastructure
> change, not a code one: bind the API to localhost behind a proxy, or close
> 5000 in the VPS panel. Filtering only stops it from costing the bot anything.

## Never block the event loop

Everything runs on one loop: the gateway heartbeat, every interaction, every
listener, every job. `requests`, `pymongo` and `time.sleep` are synchronous, so
a coroutine that reaches them directly stops the bot — and Discord gives an
interaction three seconds, so a blocking call between the click and the
acknowledgement is a failed interaction the person sees.

`off_loop` (`app/services/blocking.py`) is the one way through. Production had
52 `heartbeat blocked for more than 20 seconds` warnings in a day, every
traceback ending in a `find_one` reached from `on_message`, and a `10062 Unknown
interaction` on a guild whose youtuber had just been saved by two blocking
`requests.post` calls.

## Why not `analytics.emit`

Because the catalog drops undeclared events and `sanitize_props` strips free
text — and a log message and a traceback *are* free text. The two share the
rails (a bounded queue drained by `AnalyticsCog`) and nothing else.

## The rule that is easy to break

Nothing in `app/services/debug_logs.py` or its callers may call `logger.*`. The
sink runs underneath `logging`: a warning about a failed write is itself a
write, which fails, and warns again. `flush(on_error=...)` hands the exception
back instead, and `report_flush_failure` prints to stderr — outside the logging
tree, where it cannot feed itself. `test_debug_logs.py` pins this.

## Reading them

Inside 30 days, query `guild.logs` directly; `session_id` returns every line of
one interaction. Older than that, the daily files are the source, and
`python -m tools.keiko logs sync` indexes them into a local SQLite with
full-text search:

```bash
python -m tools.keiko logs sync --incremental   # index new daily files
python -m tools.keiko logs errors --since 7d    # repeated failures, grouped
python -m tools.keiko logs query "ConnectionError" --traceback
python -m tools.keiko logs query --session a1b2c3
```

`tools/` never ships in the deploy image and `app/` never imports it
(`tests/tools/test_tools_boundary.py`).


---

# Logs: one unit of work, one message

The Discord log channels are a **human interface, not a log sink**. They get
one message per unit of work.

## The trace

`app/services/trace.py` holds a `Trace` in a `contextvars.ContextVar`. Every
`logger.*` call underneath an open trace becomes a line of its timeline instead
of a separate Discord message; when the trace closes, `DiscordLogsHandler`
posts the assembled result. This is why none of the ~113 existing log calls had
to change: the context variable reaches them where they already are.

Traces are opened at the boundaries:

| Boundary | Where | Behavior |
|---|---|---|
| Slash commands | `keiko_command` (`app/decorators.py`) | one message per invocation |
| Webhooks | `app/webhooks/__init__.py` before/teardown request | one message per request |
| Deferred work | `schedule_webhook_job` → `trace.run_traced` | one message per job |
| Listeners | `with_error_context` (`app/decorators.py`) | silent unless it fails or reports an event |

`silent_when_clean` is what keeps `on_message` from flooding the channel: a
routine check that succeeds stays in the log file, and only surfaces in Discord
if it errors.

### Silent is not the same as clean

That rule swallowed the two lines the log channel exists for. `on_guild_remove`
never fails, so its trace was clean, so the "Left Guild" message was dropped on
the way to Discord — while the record sat in `guild.logs` and the `guild.removed`
analytics event was written normally. Every symptom pointed at a listener that
had stopped running, and the listener was fine.

A line carries the type it was logged with, and the types in
`LogTypes.REPORTED_EVENT_TYPES` are the ones that exist to be read. A silent
trace that recorded one is published, titled and coloured by that type
(`➡️ Joined Guild`, `🚪 Left Guild`) instead of by `👂 Listener Event`, and it
adopts the `guild_id` the record carried — a listener receives a
`discord.Guild`, which has no `.guild` for the decorator to read, so the message
could not otherwise say which guild left.

The flood protection is untouched: `on_message`, `on_member_join` and
`on_raw_message_edit` log nothing at all on a successful run, which is why only
the two guild events ever went missing. A warning inside a listener is still
swallowed — no path logs one today, and lifting that is a separate decision.

### Work that outlives the request needs a trace of its own

A task inherits the context it was created in, so a coroutine scheduled from a
webhook keeps pointing at that webhook's trace — which the teardown closed, and
posted, before the coroutine ran. Its lines were appended to a message nobody
would look at again.

That is the whole story of "the log does not say which streamer went live": the
`stream.offline` branch logged one line synchronously inside the request and the
`stream.online` branch logged nothing at all, so one message named the streamer
and the other arrived with an empty timeline. Both branches now name their
subject inside the request — the message that is guaranteed to arrive has to be
readable on its own — and hand the fan-out to `schedule_webhook_job`, which runs
it through `run_traced` under a `job` trace of its own.

`trace_scope` is not what deferred work wants: it joins the surrounding trace so
a fan-out does not fragment its timeline, which is right inside one unit of work
and wrong across two. `run_traced` always owns its trace, and swallows the
failure it records — there is no caller left to raise to.

Errors always keep a message of their own in the error channel, so they never
wait on a trace that may never close — and the trace timeline is posted too, as
the context for that error.

## The journey: one session, one message, edited until it ends

A trace covers one interaction. A configuration attempt spans many, and the
interesting part — the steps, the rejected value, the abandonment — happens in
button clicks, which open no trace. So a session gets a **journey**: the same
`Trace` structure kept open and re-rendered instead of closed
(`app/services/journey.py`).

```
🧭 moderations block links
<@151…748> · guild `1` · via `greeting_button` · 3m12s · ⌛ abandoned
────────────────────
`18:55:15` command invoked
`18:55:16` setup opened
`18:55:22` step: link_settings (configuration_card)
`18:55:40` ⚠️ `custom_link` rejected — link-not-recognized (try 2)
`18:56:02` step: custom_links (composition)
`18:58:27` ⌛ expired at `custom_links`
────────────────────
**Last 24h · block_links · this guild**
`19:42` 🚫 discarded at `custom_link`
`19:42` ⌛ expired at `mode`
• session 160ed3 | 2026-08-14 18:58:27
```

**Lines come from the events that are already emitted.** `journey.record` is
registered through `analytics.register_observer`, the same pattern as
`trace.register_sink`: one `emit`, several consumers. Nothing is instrumented
twice, so a line in the log can never disagree with a number in a dashboard.

Outcomes: `✅ saved` · `🔧 edited` · `🚫 discarded` · `⏸️ paused` · `▶️ resumed` ·
`🛑 disabled` · `⌛ abandoned` · `❌ failure` · `⏳ in progress`.

An edit lists **which fields changed and nothing else** — never a value, before
or after. The values already live in `guild.<cog_key>`; the log has no reason to
hold a second copy.

The 24h history is read **once, when the journey opens**
(`analytics_reports.setup_sessions`), not on every render.

### The message is not rewritten on every step

Discord's message-edit bucket is **per channel**, and every session in the bot
writes to the same log channel. Rewriting on each step would saturate that
channel long before any single session looked busy — a per-session debounce
does not help, because the contention is not per session. This project has hit
that wall before: `logger.py` still filters `"We are being rate limited."`.

So a session is published:

| When | Why |
|---|---|
| it opens | you know something started |
| it ends | the outcome is what you actually read |
| a failure happens | the one thing not worth waiting for |

That is **2 calls for a normal session** instead of one per step (measured: 8
before, 2 after). The middle of the story is one click away.

### The refresh button

Every line of a journey came from an event that is already stored, so
`journey.rebuild(session_id)` reconstructs the whole story from
`guild.analytics_events` with a single read — no new storage was needed for
this.

`JourneyRefreshButton` (`app/components/buttons.py`) is a `DynamicItem` whose
custom id carries the session, registered once in `DiscordBot.setup_hook`. It
therefore keeps working across restarts, and it **repairs a stale message**: a
session interrupted by a restart is stuck showing `⏳ in progress`, and
refreshing recomputes the outcome from storage — including `⌛ abandoned` once
the view's lifetime has passed.

`publish_journey` is scheduled, never awaited by the caller: a form step must
not spend part of Discord's three-second window on a round trip of ours.
Renders are debounced (`ANALYTICS_JOURNEY_DEBOUNCE_SECONDS`) and coalesced.
Losing a frame costs a frame, never the command.

The interaction that opens a session hands its lines to the journey and marks
itself `superseded`, so an invocation posts **one** message rather than two.

### Closing an abandoned session

`Form.on_timeout` and `Manager.on_timeout` emit `setup.abandoned` and close the
journey. Nothing is said to the user — a view timing out already just stops
responding; this is bookkeeping. It is guarded on the journey still being open,
so a sub-form expiring after its parent finished cannot report a second
abandonment, and a timeout after a save changes nothing.

This is the one place a pinned contract was broken on purpose:
`test_view_timeouts.py` used to assert that no view overrides `on_timeout`. It
now asserts that exactly `Form` and `Manager` do, that nothing else does, and
that a timeout never sends anything.

> With `ANALYTICS_ENABLED=false` the journey goes quiet too — it renders product
> events, so without them there is nothing to render. The log falls back to the
> per-interaction trace.

## The session id is the trace id

The `session_id` that groups a configuration attempt in analytics is the same
identity the log uses to group a journey. One concept, two consumers:
`FormSession` (`app/views/form_state.py`) lives on the view, which already
lives for the whole attempt — no session store, no extra timeout.

## Contracts

`tests/behavioral/contracts/test_logger_trace.py` pins the behavior that
replaced the old handler: seven records in one trace produce one message, the
timeline preserves order, errors still get their own message, a clean silent
trace never reaches Discord, the timeline is capped, and a sink that explodes
never breaks the work being traced.

It also pins three fixes that came with it: the handler formats its own record
(it used to depend on another handler having run first), it guards against an
interaction without a guild (a DM used to crash it), and it schedules sends
with `run_coroutine_threadsafe` when called off the bot's loop — webhooks run
on the Flask thread, where `create_task` is not safe and was the reason log
messages arrived out of order.

## Reading the results

Analytics that waits to be asked for does not get read. The surfaces are
ordered by how little effort they need from you:

### 1. The weekly digest — arrives on its own

`app/services/admin_digest.py`, posted to the admin channel every Monday by a
second `@tasks.loop(time=...)` in the `Analytics` cog. It leads with what needs
a decision and keeps the rest short:

```
📊 Keiko — week of 08/08 – 14/08
82 guilds (+3 joined, -1 left) · 14 delivered value this week

⚠️ Needs attention
• block_links: 4 setups died at `custom_link` (repeated validation failure)
• reminders_birthday: 1 setup died before the first step (left without a recorded problem)
• 2 guilds where Discord refused an action

📉 Settings nobody uses
• welcome_messages / `welcome_custom_image` — 0 of 31 guilds
• block_links / `mode` — 0 of 9 guilds — all on the default `block_all`

⏱️ Slowest steps
• block_links / `custom_link` — median 48s

✅ Delivered value
• 210 links blocked · 47 welcomes sent · 8 birthdays celebrated
```

The window is a rolling 7 days with no stored watermark, so a restart never
skips or duplicates a week. A quiet week still gets a message — a digest that
sometimes fails to arrive makes you doubt it every week.

Two numbers in the headline are easy to get wrong, and were:

- **the guild count comes from the bot** (`len(bot.guilds)`, passed in by the
  cog). Counting analytics profiles counts the guilds that *did something since
  analytics was deployed* — on the first week that read 12 out of 73, because a
  profile is created by the first event a guild produces;
- **"delivered value" is the window, not the lifetime.** A profile keeps
  `last_value_at` for as long as it exists, so counting profiles quietly turns
  "this week" into "at some point". It is read from the daily buckets instead.

On the first run after a deploy the window also over-claims itself: the title
says seven days, and the counters only exist from the day the collector started.

Tunables live in `Commands.ANALYTICS_DIGEST_*`.

### 2. `/admin insights` — one command, eight reports

Built on `ReportBrowser` (`app/views/report_browser.py`), a generic primitive:
a select swaps which section is on screen and **edits** the message rather than
sending another one. Sections are built lazily, so an expensive report only
runs when you ask for it.

| Section | Answers |
|---|---|
| 🛑 Where setups die | the step people give up on, and what failed there |
| 🧱 Friction | validations that reject people the most |
| 🧊 Abandoned features | configured and never delivered anything |
| 🧮 Settings nobody uses | fill rate per field, from stored configuration |
| ⏱️ Slowest steps | median time per step, and what people picked |
| 📉 Funnel | every feature side by side |
| 🚪 Churn | removed vs retained, always with N |
| 🩺 Pipeline health | the analytics pipeline itself |

Two commands stay separate because they take an argument and one of them
deletes data: `/admin guild <id>` and `/admin forget <id>`.

### 3. The trace message — context where you already look

A repeated attempt adds a footnote to the log message of the run itself
(`analytics.count_attempt` + `Trace.footnote`), so "why did they run it again"
is answered in place instead of by correlating two messages by hand.

### Two reading rules the surfaces enforce for you

- **N is always shown.** With fewer than a hundred guilds a percentage without
  its population is noise, and the churn section says so explicitly below 30.
- **Correlation is not cause.** Discord does not report why a bot was removed,
  and a slow step is not proof of confusion. Both screens carry that caveat in
  their own footer.

## Settings nobody uses — the report that needs no event

`analytics_reports.config_usage(feature)` answers "which configuration options
does nobody touch" by reading what is already stored:

1. `parse_form_yaml_to_dict(feature)` enumerates every configurable key, and the
   `defaults:` its step declares for them;
2. `config_state.feature_config_states(feature)` reads every guild's stored
   configuration **in the shape the form names it**;
3. each field gets a fill rate, and a field whose **YAML declares its options**
   also gets the distribution of chosen values.

Because it reads stored state rather than events, it covers every guild
configured long before analytics existed — it works retroactively, today.

### Step 2 is a registry, not a collection read

A YAML-driven cog stores exactly the keys its form names, so its document *is*
that shape and `config_state` just reads the collection. A feature that owns its
persistence is the exception, and it is not hypothetical: birthdays store the
channel as `channel_id`, fold three settings into a nested `default_message`,
and keep the birthdays themselves in the `reminders` database. Walking the raw
document found none of those keys, so the first weekly digest reported five
settings as used by nobody while every guild was using them — including the list
of birthdays, at 100%.

`app/services/config_state.py` maps such a feature to the translation the
**manager already renders** (`birthday_manager_cog_data`), imported by name at
call time so the report can depend on a feature service without the service
importing the report back. One mapping, two consumers: a report cannot disagree
with the screen. A new feature with its own storage adds one line there — or,
better, keeps the form's keys and needs nothing.

### A default is not silence

A setting the YAML gives a `default:` is in effect in every guild whether or not
a document stores it — nobody stores `block_links / mode`, and all of them run
`block_all`. `configurable_fields` carries that default, and both surfaces print
`— all on the default \`block_all\`` next to a zero, because the section is read
as a list of settings to delete.

The privacy line is the same one the engine uses, and it is structural:
`analytics.records_raw_choice(node)` returns true only when the YAML itself
declares where the value comes from (`options:`, `designs:`, a boolean toggle).
A typed answer, a channel id and a role id never qualify, whatever the step
type is called. `test_no_free_text_or_id_field_of_any_form_is_ever_tabulated`
sweeps every real form to keep that true as forms change.
