"""Give every auto roles document saved before the roles by time an empty ladder."""

from app.migrations.helpers import begin

COG = "default_roles"
EMPTY_LADDER = {"style": "composition", "values": []}


def up(db):
    """Write the empty ladder once; a document that holds a ladder keeps it."""
    with begin(db) as tx:
        written = tx.guild.cogs.update_many(
            {"cog": COG, "options.role_ladder": {"$exists": False}},
            {"$set": {"options.role_ladder": EMPTY_LADDER}},
        )
        tx.count("ladders_added", written.modified_count)
