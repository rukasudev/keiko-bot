from pymongo import MongoClient

import certifi


class MongoDB:
    def __init__(self, MONGO_URL: str):
        """Opens a connection to the database."""
        ca = certifi.where()
        self.db = MongoClient(MONGO_URL, tlsCAFile=ca)

    def find_parameters_by_guild(self, guild_id: str) -> dict:
        return self.db.guild.parameters.find_one({"guild_id": guild_id})

    def find_blocked_links_by_guild(self, guild_id: str) -> dict:
        return self.db.guild.block_links.find_one({"guild_id": guild_id})

    def insert_parameters_by_guild(self, guild_id: str, arg: str) -> str:
        parameters = {
            "stream_monitor": False,
            "welcome_messages": False,
            "random_image_welcome_message": False,
            "default_role_in_members": False,
            "block_links": False,
            "guild_id": str(guild_id),
        }
        parameters[arg] = True

        data = self.db.guild.parameters.insert_one(parameters)

        return data.inserted_id
