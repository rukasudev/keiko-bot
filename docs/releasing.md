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
