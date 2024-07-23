from threading import Thread

from flask import Blueprint

from app.api import create_api, run_api
from app.config import AppConfig

webhooks = Blueprint('webhooks', __name__)

from . import twitch


def handle_webhook_api(config: AppConfig) -> None:
    if not config.run_local_webhook_api():
        return
    
    webhook_api = create_api(config.ENVIRONMENT)
    run_api_lambda = lambda: run_api(webhook_api, 5005)

    api_thread = Thread(target=run_api_lambda)
    return api_thread.start()
    

@webhooks.route('/healthcheck')
def healthcheck():
    from app import bot

    bot.get_channel(bot.config.ADMIN_LOGS_CHANNEL_ID).send('Webhooks are up and running!')
    return 'Webhooks are up and running!', 200
