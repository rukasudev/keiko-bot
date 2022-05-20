class ModerationData:
    def __init__(self, db):
        self.db = db

    def find_blocked_links_by_guild(self, guild_id: str) -> dict:
        return self.db.guild.block_links.find_one({"guild_id": guild_id})

    def find_parameter_by_guild(self, guild_id: str, param: str) -> dict:
        return self.db.guild.parameters.find_one({"guild_id": guild_id})

    def find_parameters_by_guild(self, guild_id: str) -> dict:
        return self.db.guild.parameters.find_one({"guild_id": guild_id})

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

        with self.db:
            data = self.db.guild.parameters.insert_one(parameters)

        return data.inserted_id
