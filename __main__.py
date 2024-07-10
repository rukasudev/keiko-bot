import os
from os import listdir
from threading import Thread

import i18n

from app import create_app
from app.api import create_api, run_api
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

    webhook_api = create_api(config.ENVIRONMENT)
    run_api_lambda = lambda: run_api(webhook_api, 5000)

    api_thread = Thread(target=run_api_lambda)
    api_thread.start()

    app.run(config.BOT_TOKEN, reconnect=True)
