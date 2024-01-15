from typing import Any

from app import mongo_client
from app.constants import CogsConstants as constants


def find_parameter_by_guild(guild_id: str, param: str) -> Any:
    parameters = mongo_client.guild.parameters.find_one({"guild_id": str(guild_id)})

    return parameters.get(param) if parameters.get(param) else None


def find_parameters_by_guild(guild_id: str) -> dict:
    return mongo_client.guild.parameters.find_one({"guild_id": str(guild_id)})


def insert_parameters_by_guild(guild_id: str) -> str:
    parameters = constants.COGS_MODERATIONS_COMMANDS_DEFAULT
    parameters["guild_id"] = str(guild_id)

    return mongo_client.guild.parameters.insert_one(parameters)


def upsert_parameters_by_guild(guild_id: str, parameter: str, value: bool):
    parameters = constants.COGS_MODERATIONS_COMMANDS_DEFAULT
    parameters["guild_id"] = str(guild_id)
    parameters[parameter] = value

    return mongo_client.guild.parameters.update_one(
        {"guild_id": str(guild_id)}, {"$set": parameters}, upsert=True
    )
