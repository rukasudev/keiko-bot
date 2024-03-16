from os import listdir

import i18n

from app import create_app
from app.config import AppConfig
from app.logger import LoggerHooks

if __name__ == "__main__":
    """Entry async method for starting the bot."""

    config = AppConfig()

    logs = LoggerHooks(config, True)
    logs.start()

    app = create_app(config)
    logs.set_bot(app)

    for folder in listdir("app/languages"):
        i18n.load_path.append(f"app/languages/{folder}")
        i18n.set("fallback", "en")

    app.run(config.BOT_TOKEN, reconnect=True)
