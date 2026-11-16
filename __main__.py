from app import create_app
from app.bot import run
from app.integrations.http import create_api, run_api
from app.config import AppConfig
from app.logger import LoggerHooks
from app.core import i18n, images
from app.integrations.http import handle_webhook_api

if __name__ == "__main__":
    """Entry async method for starting the bot."""

    config = AppConfig()

    logs = LoggerHooks(config, True)
    logs.start()
    images.refuse_decompression_bombs()

    app = create_app(config)
    logs.set_bot(app)

    i18n.load()

    handle_webhook_api(config)

    run(app, config.BOT_TOKEN)
