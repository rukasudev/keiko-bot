from app import mongo_client
from typing import Any


def find_parameter_by_guild(guild_id: str, param: str) -> Any:
    parameters = mongo_client.guild.parameters.find_one({"guild_id": guild_id})

    return parameters.get(param) if parameters.get(param) else None


def find_parameters_by_guild(guild_id: str) -> dict:
    return mongo_client.guild.parameters.find_one({"guild_id": guild_id})


# TODO: change this keys to constants
def insert_parameters_by_guild(guild_id: str) -> str:
    parameters = {
        "stream_monitor": False,
        "welcome_messages": False,
        "random_image_welcome_message": False,
        "default_role_new_members": False,
        "block_links": False,
        "guild_id": str(guild_id),
    }

    return mongo_client.guild.parameters.insert_one(parameters)


def upsert_parameters_by_guild(guild_id: str, parameter: str, value: bool):
    parameters = {
        "stream_monitor": False,
        "welcome_messages": False,
        "random_image_welcome_message": False,
        "default_role_new_members": False,
        "block_links": False,
        "guild_id": str(guild_id),
    }
    parameters[parameter] = value

    return mongo_client.guild.parameters.update_one(
        {"guild_id": str(guild_id)}, {"$set": parameters}, upsert=True
    )
