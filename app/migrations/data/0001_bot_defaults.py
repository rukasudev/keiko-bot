"""The bot's configs and the Notion switch, written only where they are missing."""

from app.migrations.helpers import begin

BOT_DEFAULTS = {
    "prefix": "ke!",
    "status": "online",
    "activity": 0,
    "description": "/help",
    "owner_id": None,
}

NOTION_DEFAULTS = {"enabled": False}


def check(db):
    """Refuse a second bot document, since the bot reads exactly one."""
    bots = db.client.configs.data.count_documents({"status": {"$exists": True}})
    if bots > 1:
        return [f"configs.data holds {bots} bot documents, expected one"]
    return []


def up(db):
    """Create the bot's configs and the Notion switch, each only when missing."""
    with begin(db) as tx:
        bot = tx.configs.data.update_one(
            {"status": {"$exists": True}},
            {"$setOnInsert": BOT_DEFAULTS},
            upsert=True,
        )
        tx.count("configs_data_created", bot.upserted_id is not None)

        notion = tx.configs.integrations.update_one(
            {"name": "notion"},
            {"$setOnInsert": {"configs": NOTION_DEFAULTS}},
            upsert=True,
        )
        tx.count("configs_notion_created", notion.upserted_id is not None)
