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

Inside a form nobody writes that call. `decide` (`app/settings/form/form.py`)
returns the events of each decision in `Decision.analytics`, and
`observability.emit` (`app/settings/discord/observability.py`) is the one
place that hands them to `analytics.emit` with `guild_id`, `user_id`,
`feature`, `source` and `session_id` from the session. An engine handler names
only what is specific to it.

## 2. Where instrumentation lives

> **Instrumentation lives in the shared seams the engine already has. A command
> handler never calls the analytics service.**

| Seam | File | Events it produces |
|---|---|---|
| `observability.emit`, from `Decision.analytics` | `app/settings/discord/observability.py` | every `setup.*` and `feature.*_opened` event a form produces: `feature.setup_opened`, `feature.manager_opened`, `setup.step_viewed`, `setup.step_completed`, `setup.step_back`, `setup.validation_failed`, `setup.required_missing`, `setup.completed`, `setup.discarded`, `setup.discard_recovered`, `setup.abandoned` |
| `record_event`, called by the feature commit | `app/settings/features/generic.py` | every lifecycle event a form causes (see below) |
| `insert_cog_event` | `app/services/cogs.py` | the same lifecycle events for state changes made outside a form (a feature disabled by a service) |
| `run_feature_command` | `app/components/buttons.py` | `command.invoked` from any entry button |
| `Events.on_interaction` / `on_guild_join` / `on_guild_remove` | `app/cogs/events.py` | `command.invoked`, `guild.joined`, `guild.removed` |
| `Errors.on_app_command_error` | `app/cogs/errors.py` | `command.failed` |
| `GreetingsView.send` | `app/views/greetings.py` | `guild.greeting_sent` |

The engine decides *which* event a decision produces (`Engine.show`,
`Engine.refuse`, `_on_back`, `_on_commit_succeeded`, `_on_discard_confirmed`,
`_on_expired` in `form.py`); the adapter decides *how* it is emitted, once,
after the session is stored. `ms_on_step` and the friction counters are
measured by the adapter (`Friction`), never by the engine, because the engine
has no clock.

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
  emitted_at: app/settings/discord/observability.py — emit, from Decision.analytics (Engine.refuse)
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

### The envelope

`_build_envelope` wraps the properties the catalog allows in the same fields for
every event: `event`, `event_id` (a random hex id of its own, so a copy written
twice can be told apart), `v` (the event's catalog `version`), `ts`, `guild_id`,
`user_id`, `actor`, `feature`, `source`, `session_id`, `result`, `props`, `env`
and `app_version` (the release that emitted it, from `APP_VERSION`, `dev` when
the deploy names none). An `event` class stores the envelope as it is.

`version` versions an event's **properties**: it moves when a property is added,
removed or changes meaning. A field added to the envelope is not a property of
any event, so `event_id` and `app_version` left every `version` where it was.
Neither carries anything about a person: one is random, the other is a release
tag.

## 5. Storage

Four collections, all in the `guild` database; the indexes of the first three
are registered in `app/data/indexes.py`:

| Collection | Shape | Retention |
|---|---|---|
| `analytics_events` | one document per low-volume event | TTL 90 days on `ts`; each month also goes out as a file (below) |
| `analytics_guild_month` | bucket per guild per month: `days.<dd>.<metric>` counters | TTL 400 days on `updated_at` |
| `analytics_guild_profile` | one document per guild, the derived state, plus its current size | TTL 365 days **after** `removed_at` |
| `analytics_archives` | one document per month already posted as a file: `_id` the month (`2026-09`), `posted_at`, `events`, `files` | kept, twelve a year |

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

### The current size of a guild

`guild.joined` carries the size bucket of the day Keiko joined, and the profile
kept none, so no report could compare large servers with small ones. Once a day
(`Archive.measure_guild_sizes`, `app/cogs/archive.py`, at
`ANALYTICS_ARCHIVE_HOUR:ANALYTICS_ARCHIVE_MINUTE` UTC) every guild Keiko is in
that has a profile gets `size_bucket` (the same `bucket_size` vocabulary as
`guild.joined`: `<50`, `50-500`, `500-5k`, `5k+`) and `size_measured_at` on it,
in one bulk write off the loop. It is a measurement, not an event: nothing goes
to `analytics_events` or the queue, and the profile is written through the same
`build_profile_operation` the sink uses, with `upsert=False`. It never creates a
profile: a profile is still born from a guild's first event, so the reports keep
counting the guilds they counted, and a guild `/admin forget` erased stays
erased. A guild whose member count Discord does not report is left as it was.

### The monthly archive

Raw events live 90 days; after that only the daily counters remain, so a funnel
could not be rebuilt once its month was gone. The `Archive` cog
(`app/cogs/archive.py`, `app/services/archive.py`) runs a pass every day at the
same hour and posts, oldest first, every complete month no pass has posted whose
first day the raw events still reach (`archive.months_to_archive`: a month that
started less than `ANALYTICS_EVENTS_TTL_SECONDS` ago), on the logs files
channel, where the daily log export goes: one message in Keiko's voice
(`messages.admin-logs.monthly-events`) with the file attached, then marks the
month in `analytics_archives`. A day the bot was down, a pass that failed, or a
month that failed alone (it never holds the months after it) is caught up by the
next pass while the month's first day is inside the 90 days; a month that
started earlier has already lost its first events and is left. A month without
events posts nothing.

A month goes out at least once, not exactly once: when a pass fails after a file
went out (a later part refused, or the month's mark not written), the next pass
posts the whole month again. Both copies hold the same events, but each hashes
the sessions under its own key (below), so read one copy, never the two together.

Keiko keeps for good only what names no server and no member; anything that
does keeps a TTL. So the archive leaves without identity
(`archive.without_identity`): of each event it keeps `_id`, `event`, `event_id`
(random), `v`, `ts`, `actor`, `feature`, `source`, `session_id` (hashed, see
below), `result`, `props`, `env` and `app_version`
(`Commands.ANALYTICS_ARCHIVE_FIELDS`), and drops `guild_id`, `user_id` and any
other field, so a Discord id added to the envelope later (a channel, a message,
an interaction, an owner) never reaches the file; it also drops every property
whose name ends in `_id`. The catalog declares no property that names a person
or a server: they are closed vocabularies (step keys, error keys, sources,
commands, a chosen option), buckets (size, duration), counts, booleans and YAML
keys. The step, the buckets and the counts stay, so a funnel is rebuilt from the
file as it was from the collection, only without telling servers apart.

The session id leaves only as a keyed hash: the daily log exports on the same
channel carry the stored one next to the guild and user ids, so the archive
writes `blake2b(session_id, key, digest_size=8)` instead, under a key drawn for
each monthly pass (`secrets.token_bytes`) and never stored. A session still
groups its events inside a month's file, but no line matches a stored session id
or another pass's hash. The time stays exact, so a line can still be matched by
`ts` to the log export of its day while that export is kept.

Though it names no one, the file is sealed to an age public key
(`app/services/sealing.py`, SSM `/keiko/backup/age_public_key`,
`BACKUP_AGE_PUBLIC_KEY` locally), and only the private key, which the bot never
holds, opens it. Without a valid public key nothing is read or posted, the log
channel says why, and every month still inside the 90 days is posted by the
first pass after the key exists.

The file is `keiko_events_<YYYY-MM>.jsonl.gz.age`: gzipped relaxed Extended
JSON Lines, one event per line, oldest first, with its `_id`, its `ts` as a date
and its catalog version `v`, so each line is read by the version it was emitted
with. The logs tool ingests only `.log` and `.jsonl.gz` from that channel, so it
never takes the archive for log records. A month over the channel's upload limit
once sealed (`sealing.plaintext_limit`) is split in halves of the month, as many
times as it takes, into `keiko_events_<YYYY-MM>_<n>-of-<total>.jsonl.gz.age`.

To work on a month again, open it and restore it next to the live data, then
point the reports at the copy:

```bash
age -d -i key.txt keiko_events_2026-09.jsonl.gz.age | gunzip | mongoimport --db keiko_archive --collection analytics_events
```

### Redis

Only the hot window, under `guild:{guild_id}:analytics:*`, and **every key has
an `EXPIRE`** (`increment_redis_key_with_expiration`). A counter without a TTL
in a shared keyspace is how a free tier dies.

### Keiko's own block links record

Not an event, but kept beside them: every blocked link is also counted in
`guild.block_links_totals` (`count_blocked_links`, `app/data/block_links.py`),
in the worker thread that writes the record and the server's counters. It
names no server and no member, so it is kept for good: `{_id: "total",
blocked}` and one `{_id: "site:<host>", host, blocked}` per site, added with
`$inc`. A site is the full host as it was blocked (`www.` dropped, subdomains
kept: `cdn.spam-site.com` and `spam-site.com` are two sites), never a path or a
link, and it is a value, never a field name, since a host has dots. "Which links
Keiko blocks most" is `find({"host": {"$exists": true}}).sort("blocked", -1)`.
The server's own counters, a Redis hash with per-site and per-member counts,
expire 400 days after its last block and go when Keiko leaves; this record
stays.

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

`/admin forget <guild_id>` erases every analytics record of a guild. The monthly
events archive holds nothing to erase, since it names no guild; the daily log
export does, and a file already posted is out of its reach.

## 7. Failure and kill switch

| Situation | What happens |
|---|---|
| Mongo down | the batch is dropped, a warning is logged, commands keep working |
| Redis down | counters are skipped, dashboards fall back to Mongo |
| Queue full | the newest event is dropped and counted |
| Event not in the catalog | dropped and counted |
| Exception inside `emit` | swallowed at the boundary, never propagates |
| Bot stops: a deploy's SIGTERM, `/admin shutdown`, a run that fails | both queues are written before the process ends (`app/lifecycle.py`); a start Discord refused writes them again after its five-minute wait, unless a SIGTERM ends the wait first |
| SIGKILL, an OOM kill or a hard crash of the interpreter | up to a few seconds of queued events are lost, by design |

`ANALYTICS_ENABLED=false` turns `emit` into a no-op without a deploy.
`/admin pipeline` shows emitted, flushed, dropped, unknown and queue depth.

---

# Operational metrics: is it up, is it late, which release

`PrometheusCog` (`app/cogs/prometheus.py`) serves the default Prometheus
registry on port 8000 once the bot is ready, so a metric defined anywhere in the
process is scraped. Keiko's own:

| Metric | Labels | Recorded by |
|---|---|---|
| `keiko_event_loop_lag_seconds` (a histogram, buckets `Commands.HEARTBEAT_LAG_BUCKETS`) | `le` | every heartbeat tick, `metrics.record_loop_lag` |
| `keiko_build_info` | `version` | the heartbeat cog when it loads, `metrics.record_build_info` |
| `keiko_interactions_total` | `outcome` (`in_time`, `deferred`, `failed`), `code` (Discord's error code, such as `10062` or `40060`, or empty) | the form adapter (`open_feature`, `handle`, a click on a lost session), `run_feature_command` (a feature a button opens) and `Errors.on_app_command_error` |
| `keiko_webhook_refusals_total` | `route` (the URL rule, never the path a request typed), `status` (the 4xx it was answered with) | the refusal hook of `app/webhooks/__init__.py` |
| `keiko_form_*` | see `observability.py` | the form adapter |

`PrometheusCog` adds the library's own, among them `discord_connected{shard}` (1
while the gateway is up) and `discord_event_on_interaction_total{shard,
interaction, command}`, every interaction the gateway delivered: slash commands,
context menus, buttons, selects, modals and autocomplete.

**No label is ever a guild or a user id.** A label takes one series per value, so
an id is both a privacy leak and an unbounded number of series; every label above
is a closed list (`tests/test_metrics.py`). `app/services/metrics.py` is the
generic home for what every layer records, so a cog never imports the form
adapter to record a number. The latency of each outside dependency (Mongo,
Redis, Discord, Twitch, YouTube, reminders, Notion, translate) arrives with the
shared HTTP client, together with its first caller.

Each interaction is counted once: a form command when it opens (a command that
raises is counted by the error handler instead), a feature a `/setup` or greeting
button opens when it raises (`run_feature_command`, which has no error handler
behind it), a click when it ends (`failed` when an effect raised, or anything
else, with the code Discord answered). The 1.5 second clock that defers a late
answer decides `deferred`, or `failed` when Discord refused the deferral itself.

## Alerts to create in Grafana

Nothing in the repository creates them; these are the expressions, with a first
threshold to tune against real traffic:

| Alert | Expression | For | What it means |
|---|---|---|---|
| Interactions failing | `sum(rate(keiko_interactions_total{outcome="failed"}[15m])) / sum(rate(discord_event_on_interaction_total{interaction!="autocomplete"}[15m])) > 0.05` | 15m | more than one interaction in twenty failed; the code label says why (`10062`: answered too late, `40060`: answered twice) |
| Event loop late | `sum(increase(keiko_event_loop_lag_seconds_count[10m])) - sum(increase(keiko_event_loop_lag_seconds_bucket{le="5.0"}[10m])) > 0` | 0m | a heartbeat tick woke up more than five seconds after its time in the last ten minutes: something blocked the loop |
| Gateway down | `max(discord_connected) == 0` | 5m | the process is up but not connected to Discord |
| Webhook sender refused | `sum by (route) (increase(keiko_webhook_refusals_total{status=~"40[13]"}[1h])) > 0` | 0m | a request was refused for its signature or its credentials in the last hour: one forged request, or a secret that no longer matches, which stops every real notice of that route without a word |
| Webhook refusal flood | `sum by (route, status) (increase(keiko_webhook_refusals_total[15m])) > 20` | 0m | a route refusing in bulk: someone is flooding it |

`discord_event_on_interaction_total` without autocomplete is the denominator:
it counts every interaction Discord delivered, whatever handled it, and an
autocomplete is never answered by anything that counts a failure, so leaving it
in would only dilute the ratio. `keiko_interactions_total` counts what the form
adapter, the buttons that open a feature and the command error handler saw, so
the ratio reads as "of everything people clicked or typed, how much failed". The
two counters reset together when the process restarts, so `rate()` keeps the
ratio honest across deploys. When the process itself is gone there is nothing
left to scrape: that is the heartbeat monitor's job, not Grafana's.

The two refusal alerts answer different questions. Twitch, YouTube and the
reminders API send a handful of requests an hour, so a secret that stopped
matching never reaches twenty refusals in fifteen minutes: the first alert fires
on a single 401 or 403, because before this release each of those posted an
error to the channel, and now nothing else would say the notices stopped. The
flood alert is for the other case, a stranger sending garbage.

The lag is a histogram because a gauge keeps only the last tick: after a freeze,
discord.py runs the missed ticks back to back until it is on schedule again, so
a second later the gauge reads an on-time tick and the freeze is gone. The
histogram keeps every tick, and the alert asks whether any tick in the window
was more than five seconds late (`le="5.0"` is how the client names that bucket).

## The heartbeat

`app/cogs/heartbeat.py` ticks every `Commands.HEARTBEAT_SECONDS`. Each tick is
counted with how late it woke up against the time it was scheduled for, the loop
lag a blocked event loop shows first, then pings `HEARTBEAT_URL` through
`asyncio.to_thread`. A monitor that stops hearing the ping is how a stopped,
frozen or disconnected bot gets noticed: a frozen loop never runs the tick, and
while the gateway is down (`on_disconnect`, until `on_ready` or `on_resumed`) the
tick records the lag and skips the ping.

The lag is read against the schedule, not against the previous tick. discord.py
anchors a relative loop to its schedule: during a tick, `next_iteration` is this
tick's time plus the interval, and a late tick does not move the next one. So
`now - (next_iteration - interval)` is how late this tick is, while the gap
between two ticks minus the interval is only how much later it is than the last
one, which reads zero for a loop that is always three seconds behind.

`HEARTBEAT_URL` is optional: empty turns the ping off (the local default), and in
production it is the SSM parameter `/keiko/heartbeat/url`, read without failing
the boot when it was never created or the bot may not read it. A failed ping is a
warning under a silent trace: stored in `guild.logs`, never posted, and it names
the error type only, because the URL carries the monitor's token. For the same
reason `urllib3` logs at INFO at most (`app/logger.py`): at DEBUG it writes every
request line, path included, and `DEBUG=true` sends DEBUG records to the log file
uploaded to Discord every day.

## The release

`app_version()` (`app/config.py`) reads `APP_VERSION`, which the deploy sets to
the release tag, and is the one place that does; it reads `dev` anywhere else.
The version is on every analytics envelope, every `guild.logs` document, the
`Version` field of every trace and journey message, the startup message, and
`keiko_build_info`.

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

Every document names the release that wrote it (`app_version`), so a line from
before a deploy and one from after it never read alike. `tools/keiko/logs`
indexes the same column; its schema version moved to 3, so a local index built
before is rebuilt from the channel on the next `sync`.

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

`asyncio.to_thread` is the one way through for the legacy synchronous data
calls, and `app/settings/` never reaches them at all (`tests/forms/test_boundary.py`
refuses `requests`, `pymongo` and `time.sleep` there). Production had
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
| Slash commands | `keiko_command` (`app/decorators.py`) | one message per invocation, handed to the journey when it opens a form |
| Command errors | `Errors.on_app_command_error` (`app/cogs/errors.py`) | quiet: the error keeps its own message in the error channel, and the warnings of a refused answer or a slow record stay in `guild.logs`; the handler adds that one message to the trace the failed command posts itself |
| Form events | `Runtime._apply`, `Runtime.expire_stale` (`app/settings/discord/callbacks.py`) | quiet: never a message of their own, even on failure; the journey tells the story |
| Webhooks | `webhook_trace`, opened and closed around each request in `app/webhooks/__init__.py` | silent unless the request failed, continued by its first job; `/healthcheck` opens none; a request refused with a 4xx never posts its trace, nor does a 503 without an error (the sender is asked to deliver again later) |
| Deferred work | `schedule_webhook_job` → `Trace.handover` + `trace.run_traced` | the first job continues the request's message; a second job in the same request gets its own, under the same rule |
| Confirmations | `ConfirmActionView(trace_name=)` (`app/views/confirm_action.py`) | one message for what the confirmation did |
| Listeners | `with_error_context` (`app/decorators.py`) | silent unless it fails or reports an event |
| Heartbeat | `Heartbeat.beat` (`app/cogs/heartbeat.py`) | silent unless it fails; a failed ping is a warning, so it stays in `guild.logs` and the monitor is what alerts |

`silent_when_clean` is what keeps `on_message` from flooding the channel: a
routine check that succeeds stays in the log file, and only surfaces in Discord
if it errors. A webhook request is silent the same way: a request that did its
job, or only warned (an unknown reminder title), stays in `guild.logs`, and one
that failed posts its trace. Before, every request posted, the `/healthcheck` an
uptime monitor calls every few seconds included.

A refusal is not a failure. A webhook request answered with a 4xx (a forged
signature, bad credentials, an unknown id, a body that is too large or not JSON)
is logged at warning by its route, and the refusal hook reads the status from the
response, whoever wrote it, marks the trace `quiet`, logs one stored line
(`<route> refused with <status>`) and counts
`keiko_webhook_refusals_total{route,status}`. Only routes the blueprint matched
are counted: a hook of the blueprint never runs for a path it does not know, which
Flask answers with its own 404 and logs nowhere. Without this, anyone could flood
the admin channels with forged requests, bury the real errors and push the bot
towards Discord's limit of invalid requests. A refusal is still in `guild.logs`,
and a route refusing everything shows up as that counter, which is where to
alert. A route that raises answers 500, and its error and its trace both post; a
route that answers a 5xx without raising is logged by the same hook as an error
(`<route> answered <status>`), so it posts the same way. A 503 is the exception:
it is how a route asks the sender to deliver again later (the YouTube route
answers it while the Data API does not show a new video yet, so the hub tries
again), so the hook leaves it alone, and a 503 posts only when its route logged
an error itself. A route that is really down must raise, log an error or answer
another 5xx.

`quiet` is stronger: a quiet trace never posts, clean or not. Form events use it
because the journey is the surface a person reads; before, every click posted a
`▶️ Command Run` of raw engine lines (`form block_links … rev=17 cursor=confirm
EditRequested -> awaiting effects=[Render]`). The lines still reach `guild.logs`
with the session id, and an error record still gets its own message in the
error channel.

Every trace opened for a person carries `is_admin` (`is_guild_admin`,
`app/services/utils.py`), rendered as the `Admin` field. A unit of work names
how it ended with `trace.settle(result)`; `Trace.finish` keeps that result
unless the work failed, so `/birthday` reads `registered`, `asked`, `replaced`
or `refused` instead of `success`, with one line per outcome and never the date.

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
swallowed with its clean trace, except one: a Redis or Mongo outage met by the
settings cache (`app/services/cache.py`) is news about the process, not about
the message that ran into it, so the cache logs it outside any trace. It is a
message of its own on the log channel, once per store per
`DBConfigs.COG_CACHE_WARN_SECONDS`, and its `guild.logs` record carries no trace
id.

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
readable on its own — and hand the fan-out to `schedule_webhook_job`.

That fix posted **two** messages per Twitch live: the request closed its trace
naming the streamer, and the job closed another with the fan-out. So
`schedule_webhook_job` calls `Trace.handover()` on the request's trace, on the
Flask thread and before Flask returns: the successor keeps the request's name,
source, ids and start time, receives its lines, and the request is marked
`superseded`. `run_traced(..., trace=successor)` finishes it on the bot loop, so
one event is one `🔔 Webhook Event` message that names the streamer, lists the
fan-out and spans the whole event. A second job in the same request (several
birthdays in one `/reminder` call) gets a `job` trace of its own, published under
the request's rule (`Trace.publishing`), and a job that cannot be scheduled leaves
the request's message intact.

`trace_scope` is not what deferred work wants: it joins the surrounding trace so
a fan-out does not fragment its timeline, which is right inside one unit of work
and wrong across two. `run_traced` always owns its trace, and swallows the
failure it records — there is no caller left to raise to.

Errors always keep a message of their own in the error channel, so they never
wait on a trace that may never close — and the trace timeline is posted too, as
the context for that error.

An error that names a form session gets a ✅ reaction once the same person uses
the same feature in the same server without a failure, in that session or a new
one (`journey.recovered` with `journey.recovery_key`, called from
`observability.log_effects`): the person carried on, even by opening the command
again. An error nobody got past keeps none, and the handler forgets a pending
error after `Commands.ANALYTICS_RECOVERY_WINDOW_SECONDS`.

## The journey: one session, one message, edited until it ends

A trace covers one interaction. A configuration attempt spans many, and the
interesting part — the steps, the rejected value, the abandonment — happens in
button clicks, whose traces are quiet. So a session gets a **journey**: the same
`Trace` structure kept open and re-rendered instead of closed
(`app/services/journey.py`).

A saved session reads as a summary:

```
✅ Setup Saved
`/moderations block links`
User @rukasu · Guild `1` · Source `slash` · Admin `yes` · 1m26s · saved
`00:18:10` `/moderations block links` started
`00:18:10` setup opened
`00:19:08` ⚠️ `Link or Website` rejected — link-not-recognized (try 1)
`00:19:36` 🟢 feature enabled
`00:19:36` ✅ saved: Link Settings, Permissions, Your Links · 1 item · 9 steps, 1-5m
• session 3cf778 | 2026-09-14 00:18:10
```

A session that went wrong keeps every step:

```
❌ Command Error
`/moderations block links`
User @rukasu · Guild `1` · Source `slash` · Admin `yes` · 52s · failure
`00:18:10` `/moderations block links` started
`00:18:10` setup opened
`00:18:10` step: 🚫 Block Links (intro)
`00:18:16` step: Link Settings (card)
`00:18:53` step: Permissions (multi_pick)
`00:18:57` step: ✅ Alright? (review)
`00:19:02` ❌ setup failed at `✅ Alright?`: ServerSelectionTimeoutError
• session 9a1c04 | 2026-09-14 00:18:10
```

**A save is read for what it configured.** Journey lines carry a `kind`, and the
step lines (`setup.step_viewed`, `setup.step_back`) are `step`. When a session
ends `saved` or `edited`, `journey.record` drops them before adding the final
line, so the outcome is never the line the 20-line cap cuts. The summary comes
from `setup.completed` props the engine computes: `configured_steps` (step keys,
rendered as their titles) and `item_count`, never a value. A failure, a discard
or an expiry keeps every step, because that is when the path matters. `rebuild`
applies the same rule, so Refresh shows the same message.

A failed commit emits `feature.commit_failed` with the commit kind, the step and
the exception type (its message is free text and stays in the error record), and
the journey closes as `❌ failure` with that line.

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
(`analytics_reports.setup_sessions(feature, guild_id=, since=)`), not on every
render. It is one query for that guild's setup events of the feature, bounded
by the 24-hour window it reports; it used to be seven queries that brought every
guild's setup events of the feature for the whole 90 days and filtered them in
Python. The read runs in a worker thread whenever a loop runs, so the form never
waits on it: the footnote is set when it lands, which is before the debounced
first render unless the read is slower than that, and then the next render
carries it.

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
`journey.open_journey(inherit=trace)` does that hand-over once; copying the lines
a second time is why every session message used to begin with `started` twice.

### Closing an abandoned session

The events cog runs `Runtime.sweep` every `ViewConstants.FORM_SWEEP_SECONDS`
(`Events.sweep_forms`). A pass hands every due session to `decide` as `Expired`
through `Runtime.expire_stale`, under a quiet trace; the decision emits
`setup.abandoned`, and the adapter closes the journey and, while Discord still
honours the interaction token, takes the controls off the message with the
expired copy. It is guarded on the journey still being open, so a child session
expiring after its parent finished cannot report a second abandonment, and an
expiry after a save changes nothing.

The same pass forgets closed sessions past their deadline, with their adapter
state, so the store no longer grows for the life of the process. Before the
sweep, nothing called `expire_stale` in production: abandoned sessions stayed
`⏳ in progress` until someone clicked them.

A session's deadline counts from its last accepted event (`FormSession.touched`,
applied by `decide`), like the discord.py view timeout the old engine had; it was
first fixed at creation, which killed a session thirty minutes after it opened
however recently the admin had clicked. A parent waiting on a child still in use
is not due. Taking the controls off needs the latest interaction's token, which
Discord honours for fifteen minutes (`DiscordLimits.INTERACTION_TOKEN_SECONDS`);
a session idle long enough to expire is usually past that window, so the pass
closes the journey without calling Discord (every such edit used to fail with
`401 Invalid Webhook Token`), and the next click on the message closes it
through its own, fresh interaction.

No view owns a timeout any more: the session store owns the deadline
(`ViewConstants.LONG_TIMEOUT_SECONDS`), and `tests/forms/discord` pins that an
expired form finalizes its message once and that a click after expiry
finalizes it again without opening anything.

> With `ANALYTICS_ENABLED=false` the journey goes quiet too — it renders product
> events, so without them there is nothing to render. The log falls back to the
> per-interaction trace.

## The session id is the trace id

The `session_id` that groups a configuration attempt in analytics is the same
identity the log uses to group a journey. One concept, two consumers:
`FormSession` (`app/settings/form/form_state.py`) lives in the session store for
the whole attempt; a child session reports under its parent's id.

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
that shape and `feature_config_states` just reads the collection. A feature that owns its
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
