# Releasing Keiko

Merging into `main` never deploys. Production changes only when a version is
published.

## Publish a version

1. Merge the pull request into `main`. The CI workflow runs the test suite on
   every pull request and on every push to `main`.
2. Publish the version from `main`:

   ```bash
   gh release create v1.0.1 --target main --generate-notes
   ```

   The Release workflow runs the suite on that commit, builds the image as
   `keiko-bot:v1.0.1` (and `keiko-bot:latest`), deploys exactly that version,
   and waits for the bot to say it is connected to Discord and ready (step 5).

A pre-release (`gh release create v1.1.0-beta.1 --prerelease`) never deploys:
the check skips it, the build is skipped with the check, and the deploy only
follows a build that succeeded (or a version run again by hand), so no image is
built either. Promoting it to a release afterwards does not deploy it; publish a
new version instead.

## What a deploy does to the running bot

1. `docker stop -t 30` sends SIGTERM. Keiko closes its Discord connection,
   writes the analytics events and log records still queued, and exits
   (`app/lifecycle.py`). Docker kills it only if it is still running after
   `STOP_GRACE_SECONDS` (30). The first release with this behaviour still
   stops a container of the version before it, which has no SIGTERM handler:
   that stop waits the full 30 seconds, ends in a kill and loses its queues
   one last time. Only the release after it stops in seconds.
2. The new container publishes 5000 (webhooks) and 8000 (Prometheus) on
   `127.0.0.1` only: nginx and Prometheus run on the VPS and reach it through
   localhost, and nothing else can.
3. It runs with `--memory` and `--memory-swap` both set to `MEMORY_LIMIT`
   (1536m), so it cannot spill into swap either. Read `docker stats keiko-bot`
   after a few days and move the value in `.github/workflows/release.yml` if
   the bot sits close to it.
4. It receives the version it runs as `APP_VERSION` (`v1.0.1`).
5. "Check the bot stayed up" proves the bot logged in rather than only that
   nothing failed yet. When discord.py delivers READY, Keiko logs one INFO line,
   `DBConfigs.READY_PHRASE` ("Keiko is connected to Discord and ready"). Every
   5 seconds the check reads `docker logs keiko-bot` once, along with the
   container's restart count. It fails at once on a restart or on a refused
   start (below), and says so when `docker logs` itself fails. It passes once
   that line is there and the container has stayed up `SETTLE_SECONDS` (30)
   without a restart, then still wants it `running 0`, so a bot that dies just
   after READY fails the deploy too. Without the line by
   `READY_DEADLINE_SECONDS` (120) it fails and prints the last 30 lines. A
   start that stalls (a Mongo that does not answer, discord.py reconnecting on
   its own) therefore fails the deploy instead of passing it.

   A bot that cannot start writes its queues and ends its process with 1, so
   the restart policy restarts it, and the check fails on the restart.

   A start Discord refuses (a bad token, a privileged intent the developer
   portal does not grant, a gateway close Discord never lifts: 4004, 4010 to
   4013, of which 4011 "sharding required" is the one a growing bot meets)
   never clears by itself. Restarting at once would identify about once per
   start, and Discord resets the token after 1,000 IDENTIFYs in a day. So the
   process logs one CRITICAL line that begins with
   `DBConfigs.FATAL_START_PHRASE` ("Discord refused to start the bot"), writes
   its queues, and waits `FATAL_START_BACKOFF_SECONDS` (5 minutes); it writes
   them again before it exits with 1. The check fails on that line at once,
   printing it and the way out; the container itself keeps retrying every few
   minutes until someone stops it. The way out is `docker stop keiko-bot`,
   which ends the wait at once, or running the previous version again (below).
   To find such a start afterwards: the same day, query `guild.logs` for
   `level` CRITICAL. Later, once the daily file that holds it is on the logs
   channel, run `python -m tools.keiko logs sync --incremental` and then
   `python -m tools.keiko logs errors --level CRITICAL --since 7d`. The errors
   view reads the local index that `logs sync` builds, and shows ERROR by
   default.

## Choose the number

Versions follow `vMAJOR.MINOR.PATCH`:

- a fix that changes nothing else bumps PATCH (`v1.0.0` → `v1.0.1`);
- a new feature, or a visible change to an existing one, bumps MINOR
  (`v1.0.1` → `v1.1.0`);
- a change that breaks what admins or stored documents rely on bumps MAJOR.

## Run a previous version again

Actions → Release → Run workflow, with the version to run (`v1.0.0`). Nothing
is rebuilt: the deploy pulls the image that version published.

## Credentials

The image carries no credential. The deploy hands the AWS keys to the
container when it starts, and the bot reads every other secret from SSM with
them (`AppConfig.get_ssm_configs`).

- Rotating `/keiko/youtube/hub_secret` changes every YouTube callback token: restart the
  bot right after, so its start subscribes every followed channel on its new callback; the
  old callbacks answer 403 until their leases end, five days at most.
- A leaked callback token is revoked only by rotating that secret.
- The nginx access log on the VPS keeps query strings, so it holds those tokens; keep the log
  private. On the way in they travel encrypted, because the callback is built from
  `WEBHOOK_URL`, which is https: Twitch EventSub already refuses any other callback.

## Backup and restore

Every day at 01:00 UTC the bot posts a backup of its own databases on the logs
files channel, and once a month, at 01:30 UTC, the analytics events of the month
just ended (a month a pass missed goes out on the next pass that can, while its
events last). Both are sealed to one age public key, so nobody who can read the
channel can read them; only the private key opens them, and the bot never holds
it.

The backup leaves out three collections on purpose: `configs.integrations`
(third-party credentials), `audit.errors` (raw exception text, which can quote a
URL with a key in it) and `guild.logs` (the debug logs, see below). A full
restore needs them from elsewhere: re-create each `configs.integrations`
document by hand, with its credentials from wherever they were issued (the bot
reads its Notion document at start and does not boot without it); `audit.errors`
and `guild.logs` start empty, and the last 90 days of logs stay readable in the
daily log files.

1. Make the key pair once, on your own machine, and keep `key.txt` offline (it is
   the only way to open any backup or archive, so keep a second copy):

   ```bash
   age-keygen -o key.txt        # prints "Public key: age1..."
   ```

2. Store the public key where the bot reads it, then restart the bot (the
   configuration is read at start). Locally, set `BACKUP_AGE_PUBLIC_KEY` instead.

   ```bash
   aws ssm put-parameter --name /keiko/backup/age_public_key --type String --value age1...
   ```

   Without it nothing is posted, and the log channel says so every day.

3. Restore a backup: download `keiko_backup_<date>.zip.age` (or each
   `_<n>-of-<total>` part, each a zip of its own), open it, and import each
   collection, one canonical Extended JSON Lines file per collection, with its
   `_id` and types:

   ```bash
   age -d -i key.txt keiko_backup_2026-10-05.zip.age > backup.zip
   unzip backup.zip -d backup                        # manifest.json counts each collection
   mongoimport --uri "$MONGO_URL" --db guild --collection moderations \
     --file backup/guild/moderations.jsonl --mode upsert
   ```

   Import into a scratch database first (`--db restore_guild`) when you only need
   to look something up.

4. Read a month of events the same way:

   ```bash
   age -d -i key.txt keiko_events_2026-09.jsonl.gz.age | gunzip \
     | mongoimport --uri "$MONGO_URL" --db keiko_archive --collection analytics_events
   ```

A new key pair only seals what is posted after the restart: keep every old
private key for as long as you keep the files it opens.

### What the logs files channel keeps

Right after the backup, the same daily pass deletes the bot's own files on that
channel once they are past their kind's age (`app/services/logs_files.py`):

| Kind | File names | Kept |
| --- | --- | --- |
| Backup | `keiko_backup_<YYYY-MM-DD>.zip.age`, or `_<n>-of-<total>` parts | 30 days (`Commands.BACKUP_RETENTION_DAYS`), and deleted only on a day a new backup went out, so failing backups never take the last copies |
| Daily log | `keiko_logs_<YYYY-MM-DD>.jsonl.gz` (the export, since 2026-08) and `keiko_log.log` (the text file, since 2024-03, still posted daily and at each start) | 90 days (`Commands.DAILY_LOGS_RETENTION_DAYS`): they carry guild, user and session ids |
| Monthly events archive | `keiko_events_<YYYY-MM>.jsonl.gz.age`, or its parts | for good: it names no server and no member |

A file counts only when the bot posted it and every file of its message fully
matches one kind's names; anything else (another author's file, any other name,
a message mixing kinds) is never deleted. A pass makes at most 100 delete
attempts (`Commands.LOGS_FILES_DELETES_PER_PASS`), failed ones included, oldest
first, so the first passes after this release, which meet years of daily logs,
clear them over days without holding Discord's rate limits, and the log channel
says how many are left. Once a pass that included the backups leaves nothing
behind, it records in `guild.logs_files` how far back the channel is clear (the
longest age before that pass), and the next passes read the channel's history
only from there, never the whole channel.

The log channel gets one line per pass, and only when there is something to
say: how many files the pass deleted and how many it left (the ones it failed to
delete included), each delete error once with how many files it hit, or, when
reading the history or writing the mark fails, that the pass stopped and after
how many deletes. A file already gone (deleted by hand, or by the other instance
during a deploy's overlap) counts as deleted. What a pass left is tried again
the next day. Download a backup or a daily log you want to keep longer.

When a kind or a file name changes (a new kind, a renamed file, a longer age),
delete the mark in the same deploy, so the next pass reads the whole channel
again; a pass never reads what is older than the mark:

```js
db.getSiblingDB("guild").logs_files.deleteOne({_id: "retention"})
```

**On the deploy that brings this retention**, before the first 01:00 UTC pass
after it: from that pass on, the daily logs older than 90 days go, 100 a day,
and nothing else holds them. Keep a copy first, with a full sync of the logs
tool (no `--incremental`, so every daily file still on the channel is indexed),
then copy the index somewhere safe; or download the attachments themselves:

```bash
python -m tools.keiko logs sync
cp ~/.keiko/logs.db /somewhere/safe/keiko-logs-$(date +%F).db
```
