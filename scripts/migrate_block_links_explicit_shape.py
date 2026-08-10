"""One-off migration: write the block_links config shape explicitly.

Legacy documents (pre two-mode redesign) carry option labels in
`allowed_links` and no `mode`; the code translates them on every read
(`normalize_block_links_config`). This migration writes that same translation
back into the documents, so what the bot enforces is what the database shows.
The read-time normalization stays in the code as a safety net, and is a no-op
on migrated documents.

MUST run only where the NEW block_links code is deployed: the old code reads
`allowed_links` as a raw label list and crashes on the envelope shape.

Usage:
    .venv/bin/python scripts/migrate_block_links_explicit_shape.py            # dry run, dev
    .venv/bin/python scripts/migrate_block_links_explicit_shape.py --prod     # dry run, prod
    .venv/bin/python scripts/migrate_block_links_explicit_shape.py --apply    # write, dev
    .venv/bin/python scripts/migrate_block_links_explicit_shape.py --prod --apply

A JSON backup of every document about to change is written next to this
script before any write.
"""
import argparse
import json
import pathlib
import sys
from datetime import datetime, timezone

import certifi
from bson import json_util
from dotenv import dotenv_values
from pymongo import MongoClient

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

# The service module chain reaches the app's global clients, which only exist
# once the bot boots; the migration talks to Mongo through its own client.
import app  # noqa: E402

app.redis_client = getattr(app, "redis_client", None)
app.mongo_client = getattr(app, "mongo_client", None)

from app.services.block_links import normalize_block_links_config  # noqa: E402

MIGRATED_KEYS = ("mode", "allowed_links", "allowed_chats", "allowed_roles", "custom_links")


def migration_for(document: dict) -> dict:
    """The exact fields this migration would write, using the same translation
    the bot applies on every read — zero drift by construction."""
    normalized = normalize_block_links_config(document)
    changes = {
        key: normalized[key]
        for key in MIGRATED_KEYS
        if document.get(key) != normalized[key]
    }
    return changes


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prod", action="store_true", help="use MONGO_URL_PROD")
    parser.add_argument("--apply", action="store_true", help="write (default: dry run)")
    args = parser.parse_args()

    env = dotenv_values(pathlib.Path(__file__).resolve().parent.parent / ".env")
    url = (env["MONGO_URL_PROD"] if args.prod else env["MONGO_URL"]).strip()
    is_srv = url.startswith("mongodb+srv")
    client = MongoClient(url, tls=is_srv, tlsCAFile=certifi.where() if is_srv else None,
                         serverSelectionTimeoutMS=8000)
    collection = client.guild.block_links
    target = "PROD" if args.prod else "dev"

    pending = []
    for document in collection.find({}):
        changes = migration_for(document)
        if changes:
            pending.append((document, changes))

    print(f"[{target}] {collection.count_documents({})} documents, "
          f"{len(pending)} need migration")
    for document, changes in pending:
        print(f"\nguild {document.get('guild_id')}:")
        for key, value in changes.items():
            print(f"  {key}: {document.get(key)!r} -> {value!r}")
        if "add_custom" in document:
            print("  add_custom: <removed>")

    if not args.apply or not pending:
        if not args.apply:
            print("\nDry run: nothing written. Pass --apply to migrate.")
        return

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = pathlib.Path(__file__).parent / f"block_links_backup_{target}_{stamp}.json"
    backup.write_text(json.dumps([doc for doc, _ in pending], default=json_util.default,
                                 indent=2))
    print(f"\nBackup: {backup}")

    for document, changes in pending:
        update = {"$set": changes}
        if "add_custom" in document:
            update["$unset"] = {"add_custom": ""}
        result = collection.update_one({"_id": document["_id"]}, update)
        print(f"guild {document.get('guild_id')}: modified={result.modified_count}")

    print("\nDone. Re-running this script must now report 0 documents to migrate.")


if __name__ == "__main__":
    main()
