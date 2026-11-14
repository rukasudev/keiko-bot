# Data model

Where Keiko keeps each kind of data, and where a new command's data goes. Read it
before you add a collection, a field every document carries, an index or a Redis
key. How the change then reaches the database is in `docs/migrations.md`.

## The rules

- **The database is the scope.** `keiko` is Keiko as a whole, with no server as
  its owner. `guild` is what belongs to one server. `events` is what happened.
  `analytics` is numbers. `traces` is for debugging. `migrations` is the runner's.
- **The collection is the access pattern.** Split when the retention changes, when
  the moment of the write changes, or when who reads it changes. Never split by
  command name: `guild.members`, not `guild.birthdays`.
- **The field says who writes it.** `options` is what a person chose. `state` is
  what Keiko keeps on its own. A person never writes `state`, and Keiko never
  rewrites `options`.
- **The event says who caused it.** `events.changes`: someone changed something.
  `events.activity`: a command acted. `events.interactions`: someone asked for
  something.
- **Hot data lives apart from cold data.** What changes on every message (XP,
  giveaway entries) gets its own collection and batched writes, away from the
  documents that are mostly read.
- **`_id` is the natural key.** `"{guild}"`, `"{guild}:{cog}"`, `"{guild}:{user}"`,
  `"twitch:{login}"`. A lookup by `_id` needs no other index, and a repeated write
  cannot create a second document.
- **One fact, one place.** What happens becomes one event, once. Counts and
  summaries come from it through the analytics sink. No feature increments a
  counter.
- **Redis never holds the truth.** Only four key prefixes, each by role, and every
  key has a TTL: `cache:` (a copy of a document), `claim:` (I already did this),
  `throttle:` (do not try again yet), `window:` (a count over a short period).
  Losing Redis loses speed, never data.
- **Every in-memory structure has a ceiling.** A cache in the process is an LRU of
  a declared size. What does not fit goes back to the database.

## The layout

The new database ships in two pull requests. The code reads a collection once the
pull request in the "arrives" column is merged, and the collections in the "today"
column until then. The old ones stay one release after the copy (expand, then
contract) and a later schema file drops them.

| database | collection | one document per | holds | today | arrives |
|---|---|---|---|---|---|
| `keiko` | `settings` | Keiko (one document) | `options`: presence, the prefix of a new server, the operator server and channels | `configs.data`, `configs.admin` | database PR 1 |
| `keiko` | `integrations` | integration | `options`: on or off, the settings it needs | `configs.integrations` | database PR 1 |
| `keiko` | `subscriptions` | external account Keiko follows (`twitch:{login}`, `youtube:{handle}`) | `state`: the last live, the hub confirmation | `audit.notifications_twitch`, part of `audit.reminders` | database PR 1 |
| `keiko` | `schedules` | thing set for a time | a reminder on the reminders API; later a periodic job with its cursor | the birthday and YouTube `reminder_id`s | database PR 1 |
| `guild` | `settings` | server | `options`: language, time zone, prefix; `state`: owner, Keiko present, joined, left | `guild.moderations` without the feature flags | database PR 1 |
| `guild` | `cogs` | server and cog | `enabled`, `options` (what the form saved), `state` (what Keiko learned, `schema_version` included) | the seven `guild.{cog}` collections | database PR 1 |
| `guild` | `members` | server and member | `options`: the birthday; `state`: what Keiko keeps about the member | `reminders.birthdays` | database PR 1 |
| `guild` | `messages` | message Keiko posted and will edit | the cog, the subject, the channel, the message id | `notifications.notifications_twitch` | database PR 1 |
| `events` | `changes` | change | what changed in a config, in the server settings or in Keiko's presence; written at once, kept forever | the seven `events.{cog}`, `events.settings` | database PR 2 |
| `events` | `activity` | thing a command did | a blocked link, a delivery Discord refused, a greeting; written in the batch, 90 days | `guild.blocked_links`, part of `guild.analytics_events` | database PR 2 |
| `events` | `interactions` | thing someone asked for | a command, a button, a setup, a failure; written in the batch, 90 days | the rest of `guild.analytics_events`, `audit.errors` | database PR 2 |
| `analytics` | `counts` | server and month | how many of each event type, day by day | `guild.analytics_guild_month` | database PR 2 |
| `analytics` | `lifetime` | server | setups, value delivered, errors, size | `guild.analytics_guild_profile` | database PR 2 |
| `analytics` | `keiko` | Keiko-wide counter | totals across every server | `guild.block_links_totals` | database PR 2 |
| `traces` | `runs` | unit of work that failed or took over 2 s | the trace, written in the batch | new | database PR 2 |
| `migrations` | `changelog`, `lock` | applied file; the one lock | the ledger and the lock of `docs/migrations.md` | the same | database PR 1 |

Only the analytics sink writes to `analytics`. Log lines (`guild.logs`, 30 days)
do not move: they leave Mongo for a log service in a later pull request.
`guild.analytics_archives` and `guild.logs_files` stay where they are until database
PR 2 moves them to `keiko.schedules`. The Stream Elements channel id is not a
subscription: it stays in the feature's `guild.cogs` document, under `state`.

## The documents

`guild.settings` and `guild.cogs` share one shape: what a person chose apart from
what Keiko keeps.

```
guild.settings
{
  _id: "921659827523051530",
  options: {language: "pt-br", timezone: "America/Sao_Paulo", prefix: "ke!"},
  state: {owner_id, present: true, joined_at, left_at},
  updated_at
}

guild.cogs
{
  _id: "921659827523051530:stream_elements_commands",
  guild_id, cog: "stream_elements_commands",
  enabled: true,                       the only answer to "is it on?"
  options: {streamer: "shroud"},       what the form saved
  state: {channel_id: "5f...", schema_version: 1},   what Keiko found out on its own
  created_at, updated_at,
  updated_by: "287394712398"           the member who last saved; Keiko's own writes leave it
}

guild.members
{
  _id: "921659827523051530:287394712398",
  guild_id, user_id,
  options: {                           what the member chose, one key per cog
    reminders_birthday: {date: "05-12", month: 5, day: 12, message, image, self_edit_count}
  },
  state: {                             what Keiko learned: the name, then one key per cog
    display_name: "Ana",
    reminders_birthday: {last_celebrated_year: 2026}
  },
  created_at, updated_at
}

keiko.schedules
{
  _id: "birthday:921659827523051530:05-12",   or "youtube_renewal:{handle}"
  kind: "birthday",                    or "youtube_renewal"
  reminder_id: "4001",                 always text; null while the reminders API refused it
  due_at,                              when the reminder fires next; written, not read yet
  expires_at,                          renewals only: the TTL drops one nothing renewed
  created_at, updated_at
}
```

Each collection has one async repository in `app/data/<database>/<collection>.py`,
reached as `data().<database>_<collection>` (`data().guild_cogs`,
`data().keiko_schedules`); its methods are the collection's generic operations.

`app/data/guild/cogs.py` hands the app the flat feature document it always read,
`{guild_id, enabled, **options, state: {...}}`, and splits it back on a write, so a
feature module never names `options`. What Keiko learns on its own goes through
`data().guild_cogs.set_cog_state(guild_id, cog, fields)`. The birthday config keeps the form's keys
(`channel`, `notification_time`, `mention_everyone`, `default_message`); its time zone
is the server's.

`app/data/guild/members.py` does the same per member: `find_member_option(guild_id,
user_id, cog)` returns `{guild_id, user_id, **options[cog], state: {display_name,
**state[cog]}}`, and the file never names a cog. What a birthday is (its defaults, the
merge that keeps `self_edit_count`, the year celebrated) lives in
`app/services/reminders_birthdays.py`. `app/data/keiko/schedules.py` takes the kind:
`schedule_id(kind, *subject)`, `find_schedules_by_reminder(reminder_id, kind)`,
`find_schedules_missing_reminder(kind, limit)`; each kind's ids live in the service
that owns it (`birthday_schedule_id`, `youtube_renewal_id`).

`keiko.settings` is one document, `_id: "keiko"`:
`options {presence: {status, activity, description}, prefix, owner_id, operator: {admin_guild_id, admin_*_channel_id...}}`,
the operator ids stored as numbers. `keiko.integrations` is one document per
integration, `_id` its name, `options` its switch and settings; the backup leaves it
out.

The member's display name sits next to the birthday. `/birthday`, the admin add and
the birthday post refresh the copy; it is read only where the member cannot be
fetched, and it is never the truth about the member. Removing a birthday keeps
`state`, so a member removed and added again is not celebrated twice in a year.

`due_at` is written but not read yet. Keiko sets it when it saves a birthday
schedule and when a reminder fires, and no code decides anything by it: the
reminders API still fires each reminder. Database PR 2 is the first to read it,
with the `kind, due_at` index, to run the periodic jobs. The schedules `data/0004`
copied hold `due_at: null` until Keiko writes them again.

One schedule per server and date holds the reminder every birthday on that date
shares, so a date cannot get a second reminder. A reminder from before each server
had its own may still sit on several servers' schedules; it is deleted only when the
last of them goes.

Every event has the same envelope. The collection says who caused it, and with
that its retention and the moment it is written.

```
events.changes | events.activity | events.interactions
{
  _id, ts,                             ts in UTC
  type: "link.blocked",                declared in app/analytics/catalog.yml, with its collection
  guild_id, cog: "block_links",        text, or null for an event of no server or no cog
  actor: {kind: "user", id},           user, bot or job; the id of the member, or of Keiko
  trace_id, session_id,                the trace that recorded it, the form session
  props: {host, link, rule, mode, reason, match, deleted, channel_id},
  v: 1                                 the catalog version of the props
}
```

One call writes every event: `analytics.record(type, guild_id=, cog=, actor=,
props=, session_id=)` (`app/core/analytics.py`, `docs/analytics.md` section 1).
A change is written at once and kept; activity and interactions are written in
the analytics sink's batch and expire 90 days after `ts`; a type whose
collection is `none` (`value.delivered`) has no document and is only counted.
`app/data/events/{changes,activity,interactions}.py` read and delete them by
server, cog, type, time and actor, every read with its Mongo limit, and never
name a command: a command's prop paths (`props.host` for the sites block links
counts) stay in its service.

`analytics.counts` is one document per server and month, with the day's counts
inside it: `{_id: "{guild}|2026-11", guild_id, month, days: {"10": {"link_blocked|block_links": 4}}}`
(the type loses its dots, which an update path would read as nesting).

## Redis

Redis holds four roles, each one a key prefix, and every key has a TTL. Nothing
in it is the truth: a lost key costs a read of Mongo, a repeated call or a count
that starts again.

| Role | Means | Key | TTL |
|---|---|---|---|
| `cache:` | a copy of a document or an answer | `cache:cog:{guild_id}:{cog}` (`DBConfigs.CACHE_COG_KEY`) | 5 min, 1 min when the server has none |
| | | `cache:stream_elements:commands:{channel_id}` (`Commands.CACHE_STREAM_ELEMENTS_COMMANDS`) | 24 h, 5 min with no command |
| `claim:` | this was done once already | `claim:twitch:live:{streamer}:{started_at}` (`Commands.REDIS_TWITCH_LIVE_CLAIM`) | 24 h |
| | | `claim:youtube:video:{video_id}` (`Commands.REDIS_YOUTUBE_VIDEO_CLAIM`) | 7 d |
| `throttle:` | do not try again yet | `throttle:stream_elements:channel:{streamer}` (`Commands.REDIS_STREAM_ELEMENTS_CHANNEL_THROTTLE`) | 5 min |
| | | `throttle:stream_elements:warned:{streamer}` (`Commands.REDIS_STREAM_ELEMENTS_WARNED_THROTTLE`) | 30 min |
| `window:` | a count over a fixed period | `window:attempts:{guild_id}:{feature}` (`Commands.REDIS_ATTEMPTS_WINDOW`) | 24 h from the first count |

- A key template lives in `app/constants.py`, named `REDIS_*` or `CACHE_*`, and
  starts with its role. Every write goes through `app/core/cache.py`.
- A write sets its TTL in the same command or the same pipeline: `setex`, a claim
  with `SET NX EX`, a window with `SET NX EX` then `INCRBY`. A window's TTL is set
  by its first count only, so it closes on time however busy the server is.
- A new key family is one of the four roles. A count that must last belongs in
  `analytics`, through an event, never in Redis.
- `python -m app.migrations redis-check` fails on any key without a TTL and warns on
  one outside the four roles that has a TTL; the release runs it after
  `redis-cleanup` (`docs/releasing.md`).

## A new command: seven questions

Ask them in order. Each "yes" says where that part of the data lives. Most
commands answer yes to two or three of them.

| # | question | if yes |
|---|---|---|
| 1 | Is it something an admin configures? | `guild.cogs`, in `options`, through the command's form YAML |
| 2 | Does Keiko remember something about a member that changes rarely? | `guild.members`, one field per feature |
| 3 | Does it change on every message? | a collection of its own for the hot data, written in batches |
| 4 | Is it a message Keiko posted and will edit? | `guild.messages` |
| 5 | Does it happen at a set time? | `keiko.schedules` |
| 6 | Is it an external account Keiko follows? | `keiko.subscriptions` |
| 7 | Did something happen? | `events.changes`, `events.activity` or `events.interactions`, with its type in the catalog; the number comes from it, in `analytics` |

Then, for every new collection or query: a schema file declares the collection and
its indexes (`docs/migrations.md`), and the query-plan test proves every query of
`app/data/` uses an index.

### Examples

| command | config | data | event | schedule and memory |
|---|---|---|---|---|
| XP and levels | `guild.cogs`: XP per message, ignored channels, roles per level | `guild.levels` (hot): xp, level; index `{guild_id, xp: -1}` for the ranking | `activity`: `level.reached` | the cooldown and the XP summed in memory, one batched `$inc` about every 10 s |
| Server blacklist | `guild.cogs`: words, members, the action | none | `activity`: `message.blocked` | the rules compiled in memory, as Block Links does |
| Keiko blacklist | none | `keiko.blocklist`: `user:{id}` or `guild:{id}`, the reason, until when | `changes`: who blocked | the whole list in memory, reloaded every minute |
| Role by time | `guild.cogs` | `guild.members`: the temporary roles | `activity`: role given, role taken | `keiko.schedules`: take the role on a date |
| Warnings | `guild.cogs` | `guild.members`: the warning count | `activity`: a warning, with the reason | `keiko.schedules` when a warning expires |
| Giveaways | `guild.cogs` | `guild.messages`: the post; `guild.entries` (hot): who entered | `activity`: the winner drawn | `keiko.schedules`: the end |
| Tickets | `guild.cogs` | `guild.messages`: the panel; `guild.tickets`: the open tickets | `activity`: opened, closed | none |

Notifications for a new streaming platform follow Twitch. Take Kick:

- the admin's choices (which Kick channels, which Discord channel, the message)
  are one `guild.cogs` document per server, in `options`;
- each Kick channel Keiko follows is one `keiko.subscriptions` entry,
  `kick:{slug}`, with `state` holding the last live it saw. Many servers can follow
  the same channel and it is still one entry;
- the announcement Keiko edits when the live ends is a `guild.messages` document;
- "announced once" is a `claim:` key in Redis with a TTL;
- the announcement itself is an `events.activity` event, and its count comes from
  it.

No step needs a collection named after the command, and none needs Python outside
the feature module and the `app/data` repositories it awaits.
