# Database migrations

Every change to MongoDB is a migration file in `app/migrations/`. A change to the
structure (a database, a collection, an index) is a file in `schema/`. A change to
the data (a field every document needs, a copy from an old collection) is a file in
`data/`. The release deploy applies the pending ones with the new image before it
stops the running container. The bot refuses to start on a database that lacks a
migration its code has.

Which collection holds what, and where a new command's data goes, is in
`docs/data-model.md`. This page is about how a change reaches the database.

```
app/migrations/
  runner.py         the plan, the checks, the lock, the transactions, verify
  helpers.py        index, ttl, one_per (schema files) and begin (data files)
  __main__.py       the CLI
  snapshot.json     the structure the schema files declare, plus the ledger's
  schema/
    0001_baseline.py
  data/
    0001_bot_defaults.py
```

## The CLI

```bash
python -m app.migrations status               # each file, per folder: applied, pending or newer
python -m app.migrations migrate --dry-run    # every check, then each data file in an aborted transaction
python -m app.migrations migrate              # apply every pending file: schema first, then data
python -m app.migrations verify               # the live database against snapshot.json
python -m app.migrations snapshot             # the live structure, as snapshot.json is written
python -m app.migrations snapshot --declared  # what the schema files declare, without Mongo
make migrate                                  # migrate against the local docker-compose Mongo
```

Each step prints one line that starts with its result (`ok`, `fail`, `dry`,
`apply`, `newer`, `extra`). A run with nothing pending prints `nothing to apply`.
The exit code is 1 when any step failed.

Only the runner reads the URL: `MONGO_URL` from the environment (or `.env` when it
is not set), and SSM's `/keiko/mongo/url` when `APPLICATION_ENVIRONMENT=prod`.

## What a run guarantees

- **Checks before any write.** Mongo answers with a writable primary. It is a
  replica set (transactions need one) at 5.0 or later. The lock is free. The
  ledger agrees with the code (below). Every pending data file's `check` passes.
  No live index has the name of a declared one with another spec (both are
  printed), nor covers the same keys under another name. No unique index to
  create would meet duplicates.
- **Order, per folder.** `schema/` and `data/` each count from `0001`, without a
  gap or a repeat, and every file is named `NNNN_slug.py`. The boot check refuses
  the same files. An applied file whose sha256 changed stops the run. A pending
  number below the last applied one in the same folder (two branches merged out of
  order) stops the run. Ledger entries above the code's last number are allowed
  only as a contiguous tail of that folder: an older image running after a newer
  one migrated.
- **Schema before data.** `migrate` applies every pending schema file first, then
  every pending data file, in order. So a data file can always count on the
  collections and indexes of its own release.
- **Atomic per data file.** A data file and its ledger entry commit in one
  transaction, or neither does. A failure stops the run there: what was applied
  before stays applied, and the deploy stops with it.
- **One run at a time.** The lock is one document, `migrations.lock`
  `{_id: "lock"}`, taken by an insert. A second runner gets a duplicate key and
  stops, naming the holder. A TTL index on `expires_at` frees the lock of a killed
  run, and each file pushes the expiry forward.
- **Idempotent.** A second run finds nothing pending and writes nothing.
- **A time budget.** A data file over `Migrations.TRANSACTION_BUDGET_SECONDS`
  (30 s) aborts its transaction instead of half-writing. Split it.

The ledger is `migrations.changelog`, one entry per applied file:

```
{_id: "schema/0001", kind: "schema", number: 1, name: "baseline",
 sha256, applied_at, seconds, counts, app_version}
{_id: "data/0001", kind: "data", number: 1, name: "bot_defaults", ...}
```

The folder is part of the id, so `schema/0001` and `data/0001` never collide.

## Writing a schema file

Create `app/migrations/schema/NNNN_slug.py`, the next number of that folder. It
declares `STRUCTURE`: database, then collection, then its indexes, written with
the helpers.

```python
"""One sentence: what this migration creates."""

from app.migrations.helpers import index, one_per, ttl

STRUCTURE = {
    "guild": {
        "logs": [
            ttl("ts", days=30),
            index("level", "ts"),
            index("guild_id", "ts"),
        ],
        "moderations": [one_per("guild_id")],
    },
    "audit": {"reminders": [ttl("expires_at")]},
}
```

- `index(*fields, unique=False, partial=None, name=None)`: a field written with a
  leading `-` is descending (`index("guild_id", "-ts")`). Without `name`, the
  index gets Mongo's default name, built from the keys.
- `ttl(field, days=None, seconds=0)`: documents expire that long after the date
  in `field`. `ttl("expires_at")` expires each document at its own date.
- `one_per(field)`: one document per value of `field`, for the documents where it
  is a string (a unique index with that partial filter).
- A collection listed with `[]` is created with no index.

Structure is create-if-not-exists only, so a schema file is safe to run on any
database. A changed spec on an existing index name is refused: a new spec needs a
new index name. A schema file has no data step and no `check`.

## Writing a data file

Create `app/migrations/data/NNNN_slug.py`, the next number of that folder. It
exposes `up(db)`, and every write happens inside one `with begin(db) as tx:` block.

```python
"""One sentence: what this migration writes."""

from app.migrations.helpers import begin

BOT_DEFAULTS = {"prefix": "ke!", "status": "online", "activity": 0, "description": "/help", "owner_id": None}


def check(db):
    return []


def up(db):
    with begin(db) as tx:
        created = tx.configs.data.update_one({}, {"$setOnInsert": BOT_DEFAULTS}, upsert=True)
        tx.count("configs_data_created", created.upserted_id is not None)
```

Read the block like a SQL transaction:

- **`begin(db)` opens it.** `tx` is the Mongo client bound to the transaction's
  session. `tx.<database>.<collection>` works as usual, and every read and write
  inside the block passes the session without the file naming it.
- **`tx.count(name, n)` records what the file did.** The counts are printed on the
  run's line and saved in the ledger entry.
- **The end of the block is the commit.** The runner checks the time budget,
  inserts the ledger entry inside the same transaction, and commits. When the
  commit's outcome is unknown (`UnknownTransactionCommitResult`), only the commit
  is retried, never the block.
- **An exception aborts.** Nothing is written, the error is printed, and the run
  stops. A failed run is safe to repeat because nothing was committed.
- **`--dry-run` aborts at the end of the block** instead of committing, and prints
  the counts it would have written.
- **One `begin` per file, exactly.** A data file that returns without entering
  `begin`, or enters it twice, fails.

`check(db)` is optional. It runs in the preflight, before anything is written, and
returns the problems that must stop the run (a value the file does not know how to
map, for example).

## The rules

- **Never edit an applied file.** Its hash is in the ledger, and a changed file
  stops every later run. Write a new one instead. Reformatting counts as an edit.
- **Literals only.** A migration file imports nothing from the app except
  `app.migrations.helpers` (`tests/test_migrations.py` refuses it). A constant
  moved or renamed later would change what an old migration means. Where a value
  must match an app constant (a TTL), a test compares the two.
- **Expand, then contract.** A release only adds: new collections, new indexes,
  new fields, copies into the new place. Dropping or renaming what the old
  version reads is a migration of the next release, once no deployed version
  reads it. During a deploy the old container still runs on the migrated
  database until it is replaced, and this is what keeps it working.
- **A data file is idempotent by its filter** (`$setOnInsert`,
  `{"field": {"$exists": False}}`), so it means the same thing on any database.
- **One concern per file**, small enough for one transaction.

Then update the snapshot and run the tests. The tests that need a real Mongo run
on a throwaway replica set on colima (the recipe is in `docs/agent-guide.md`,
"The gate"). The suite drops every database it finds and refuses a server that
holds data it did not write.

```bash
python -m app.migrations snapshot --declared > app/migrations/snapshot.json
pytest tests/test_migrations.py -q
MONGO_URL=<the replica set> MONGO_TEST_URL=<the replica set> pytest -m mongo -q
```

`-m mongo` also runs `tests/test_query_plans.py`, which explains every query the
code in `app/data/` sends and fails on a collection scan. A new query needs its
index in a schema file of the same pull request.

## The snapshot

`app/migrations/snapshot.json` holds one key per `database.collection`, and one
string per index in the helpers' notation, sorted. The ledger's own collections
are included.

```json
{
  "audit.reminders": ["ttl(expires_at)"],
  "guild.logs": ["index(guild_id, ts)", "index(level, ts)", "index(session_id, ts)", "ttl(ts, days=30)"],
  "guild.moderations": ["one_per(guild_id)"],
  "reminders.birthdays": []
}
```

`snapshot` (the live database) and `snapshot --declared` (the schema files) use
the same renderer, so CI compares them byte for byte. An index the helpers cannot
express is printed as its full spec in JSON, so nothing is hidden. A unit test
keeps the committed file equal to `snapshot --declared`.

## Dry run

`migrate --dry-run` runs every check and lists each collection and index it would
create. It then runs each pending data file in a transaction of its own that is
aborted at the end of the block: the counts are real and nothing is written. One
transaction per file keeps a release with two heavy files under Atlas's 60 s
transaction lifetime. The cost is that a file's dry run does not see what the
files before it would write, so its counts are read on the database as it is now.
The schema step is listed, not rehearsed: no collection or index is created, so a
data file that needs one a pending schema file creates runs without it. The dry
run does not take the lock, and it fails when another run holds it.

## Where it runs

- **Production**: `.github/workflows/release.yml`, deploy job, step "Run the
  migrations with the new image", between "Pull the version" and "Replace the
  running container":
  `docker run --rm <new image> python -m app.migrations status`, then
  `migrate --dry-run`, then `migrate`, with the same AWS environment the bot gets.
  A failure stops the deploy, and the old container keeps running.
- **CI**: the `mongo` job in `.github/workflows/ci.yml` starts a single-node
  replica set and runs `migrate` twice from empty. It fails when the second run
  applies anything or changes any count. Then it runs `verify`, compares
  `snapshot` with `snapshot.json`, and runs `pytest -m mongo` (the migration tests
  and the query-plan test).
- **Locally**: `make run` runs `make migrate` first.

## Rollback

Nothing undoes a migration. The rollback is restoring the `mongodump` taken before
the release, then deploying the older image. The deploy does not take the dump:
take it by hand before you publish a release that has data migrations.

```bash
mongodump --uri "<production url>" --gzip --archive=keiko-before-v1.0.1.archive
mongorestore --uri "<production url>" --gzip --archive=keiko-before-v1.0.1.archive --drop
```

An older image also starts on the migrated database without a restore. Its code
knows fewer files than the ledger holds; the runner reports the newer ones as
`newer` and changes nothing, and the boot check only asks that every file the code
knows is applied. That works because a release only adds. It is the quick way back
when the data is fine and only the code is wrong.

## A fresh database

`schema/0001_baseline` creates every collection and index the bot uses today.
`data/0001_bot_defaults` writes the `configs.data` document (prefix `ke!`, status
`online`, activity 0, description `/help`, `owner_id` null, which lets discord.py
use the application's owner) and the Notion switch in `configs.integrations`
(`enabled: false`), each only when missing. No migration writes a secret: tokens
and keys stay in SSM or `.env`.

Nothing creates `configs.admin`: its fields are the admin server and channel ids,
which have no neutral value. Insert it by hand before the first boot:
`admin_guild_id`, `admin_reports_channel_id`, `admin_logs_channel_id`,
`admin_logs_command_call_id`, `admin_logs_error_channel_id`,
`admin_logs_files_channel_id`, `admin_dump_channel_id` (strings of digits), and
optionally `admin_logs_bot_actions_channel_id`.

## Converting an existing local volume

docker-compose's `mongodb` starts as a single-node replica set (`--replSet rs0`),
and its healthcheck initiates the set once. The data in `keiko_mongodb_data` stays
as it is:

```bash
docker compose up -d --force-recreate --wait mongodb   # restart with --replSet; the healthcheck runs rs.initiate
docker exec mongo-server mongosh --quiet --eval 'rs.status().members[0].stateStr'  # PRIMARY
make migrate
```

The member is named `localhost:27017`, so a client on the host connects as before
(`mongodb://localhost:27017`). A client inside another container adds
`?directConnection=true` to its URL.

## Comparing with another database

`python -m app.migrations snapshot` prints any database's structure in the format
of `snapshot.json`, so the two can be diffed. `verify` does the comparison and
lists what the live database has beyond it (`extra`), without removing anything.
On production, run it from the VPS with the release image:

```bash
docker run --rm -e AWS_ACCESS_KEY_ID -e AWS_SECRET_ACCESS_KEY -e AWS_REGION <image> python -m app.migrations snapshot
```
