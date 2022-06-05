from services.utils import get_text_channels_id


class ModerationData:
    def __init__(self, db):
        self.db = db

    def find_blocked_links_by_guild(self, guild_id: str) -> dict:
        return self.db.guild.block_links.find_one({"guild_id": guild_id})

    def find_parameter_by_guild(self, guild_id: str, param: str) -> dict:
        return self.db.guild.parameters.find_one({"guild_id": guild_id})

    def find_parameters_by_guild(self, guild_id: str) -> dict:
        return self.db.guild.parameters.find_one({"guild_id": guild_id})

    def insert_parameters_by_guild(self, guild_id: str, table: str, **kwargs) -> str:
        parameters = {
            "stream_monitor": False,
            "welcome_messages": False,
            "random_image_welcome_message": False,
            "default_role_new_members": False,
            "block_links": False,
            "guild_id": str(guild_id),
        }
        parameters[table] = True

        data = self.db.guild.parameters.update_one(
            {"guild_id": str(guild_id)}, {"$set": parameters}, upsert=True
        )

        self._insert_cogs_params(guild_id=guild_id, table=table, kwargs=kwargs)

        return data

    def _insert_cogs_params(self, guild_id: str, table: str, **kwargs) -> str:
        kwargs = kwargs.get("kwargs")

        if table == "block_links":
            message = kwargs.get("texts")["$text1"]
            unblocked_chats = kwargs.get("options")["$options1"]
            permited_links = [
                site.lower() for site in kwargs.get("options")["$options2"]
            ]
            guild = kwargs.get("guild")

            param = {
                "message": message,
                "unblocked_chats": get_text_channels_id(guild, unblocked_chats),
                "permited_links": permited_links,
                "guild_id": str(guild_id),
            }

        data = self.db.guild[table].insert_one(param)

        return data.inserted_id
