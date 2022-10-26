import asyncio

from app import create_app
from app.config import AppConfig
from app.logger import logger


async def main() -> None:
    """Entry async method for starting the bot."""

    config = AppConfig()
    app = create_app(config)
    
    await app.run()


try:
    asyncio.run(main())
except Exception as error:
    logger.exception(f"Error when initializing bot: {error}")
