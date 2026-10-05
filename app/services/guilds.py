"""What Keiko records about a server when it joins one."""
from typing import Any, Dict, Optional, Tuple

from app.constants import GuildConstants as constants
from app.data.moderations import count_moderations_by_owner, find_moderations_by_guild
from app.services.moderations import insert_moderations_by_guild, update_moderations_by_guild


def join_guild(guild_id: int, owner_id: str) -> Tuple[Optional[Dict[str, Any]], int]:
    """Mark Keiko online in a server it joined: the server's earlier record, if any, and its owner's server count."""
    exist = find_moderations_by_guild(guild_id)

    if not exist:
        insert_moderations_by_guild(guild_id, owner_id=owner_id)
    else:
        update_moderations_by_guild(guild_id, constants.IS_BOT_ONLINE, True)
        update_moderations_by_guild(guild_id, "owner_id", owner_id)

    return exist, count_moderations_by_owner(owner_id)
