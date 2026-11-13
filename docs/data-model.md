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
| `keiko` | `subscriptions` | external account Keiko follows (`twitch:{login}`, `youtube:{channel}`, `stream_elements:{name}`) | `state`: the last live, the hub confirmation, the external channel id | `audit.notifications_twitch`, part of `audit.reminders` | database PR 1 |
| `keiko` | `schedules` | thing set for a time | a reminder on the reminders API, or a periodic job with its cursor | the birthday and YouTube `reminder_id`s, `guild.analytics_archives`, `guild.logs_files` | database PR 1 |
| `guild` | `settings` | server | `options`: language, time zone, prefix; `state`: owner, Keiko present, joined, left | `guild.moderations` without the feature flags | database PR 1 |
| `guild` | `cogs` | server and cog | `enabled`, `schema_version`, `options` (what the form saved), `state` (what Keiko learned) | the seven `guild.{cog}` collections | database PR 1 |
| `guild` | `members` | server and member | `options`: the birthday; `state`: what Keiko keeps about the member | `reminders.birthdays` | database PR 1 |
| `guild` | `messages` | message Keiko posted and will edit | the cog, the subject, the channel, the message id | `notifications.notifications_twitch` | database PR 1 |
| `events` | `changes` | change | what changed in a config, in the server settings or in Keiko's presence; written at once, kept forever | the seven `events.{cog}`, `events.settings` | database PR 2 |
| `events` | `activity` | thing a command did | a blocked link, an announced live; written in the batch, 90 days | `guild.blocked_links`, part of `guild.analytics_events` | database PR 2 |
| `events` | `interactions` | thing someone asked for | a command, a button, a setup, a failure; written in the batch, 90 days | the rest of `guild.analytics_events`, `audit.errors` | database PR 2 |
| `analytics` | `counts` | server and month | how many of each event type, day by day | `guild.analytics_guild_month` | database PR 2 |
| `analytics` | `lifetime` | server | setups, value delivered, errors, size | `guild.analytics_guild_profile` | database PR 2 |
| `analytics` | `keiko` | Keiko-wide counter | totals across every server | `guild.block_links_totals` | database PR 2 |
| `traces` | `runs` | unit of work that failed or took over 2 s | the trace, written in the batch | new | database PR 2 |
| `migrations` | `changelog`, `lock` | applied file; the one lock | the ledger and the lock of `docs/migrations.md` | the same | database PR 1 |

Only the analytics sink writes to `analytics`. Log lines (`guild.logs`, 30 days)
do not move: they leave Mongo for a log service in a later pull request.

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
  options: {birthday: {...}},          the date the member or an admin saved
  state: {display_name: "Ana", celebrated_year: 2026},
  updated_at
}
```

`app/data/guild/cogs.py` hands the app the flat feature document it always read,
`{guild_id, enabled, **options, state: {...}}`, and splits it back on a write, so a
feature module never names `options`. What Keiko learns on its own goes through
`set_cog_state(guild_id, cog, fields)`. The birthday config keeps the form's keys
(`channel`, `notification_time`, `mention_everyone`, `default_message`); its time zone
is the server's.

`keiko.settings` is one document, `_id: "keiko"`:
`options {presence: {status, activity, description}, prefix, owner_id, operator: {admin_guild_id, admin_*_channel_id...}}`,
the operator ids stored as numbers. `keiko.integrations` is one document per
integration, `_id` its name, `options` its switch and settings; the backup leaves it
out.

The member's display name sits next to the birthday, so a panel lists names
without asking Discord for every member. Keiko refreshes the copy; it is never the
truth about the member.

Every event has the same envelope. The collection says who caused it, and with
that its retention and the moment it is written.

```
events.changes | events.activity | events.interactions
{
  ts, type: "link.blocked",            the type is declared in app/analytics/catalog.yml
  guild_id, cog: "block_links",
  actor: {kind: "user", id},           user, bot or job
  trace_id, session_id,
  props: {link, host, rule},
  v: 1
}
```

`analytics.counts` is one document per server and month, with the day's counts
inside it: `{_id: "{guild}|2026-11", guild_id, month, days: {"10": {"link.blocked|block_links": 4}}}`.

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
the feature module and its `app/data` functions.
