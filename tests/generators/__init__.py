"""
Generators para criacao de dados de teste no banco.

Uso:
    from tests.generators import moderations

    await moderations.welcome_messages(db, guild_id="123")
"""

from tests.generators import moderations

__all__ = ["moderations"]
