import asyncio
from os import listdir

import i18n

from app import create_app
from app.config import AppConfig
from app.logger import LoggerHooks, logger


async def main() -> None:
    """Entry async method for starting the bot."""

    config = AppConfig()

    logs = LoggerHooks(config, True)
    logs.start()

    app = create_app(config)

    for folder in listdir("app/languages"):
        i18n.load_path.append(f"app/languages/{folder}")
        i18n.set("fallback", "en")

    await app.run()


try:
    asyncio.run(main())
except Exception as error:
    logger.exception(f"Error when initializing bot: {error}")
