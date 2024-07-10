from flask import Blueprint

webhooks = Blueprint('webhooks', __name__)

from . import twitch


@webhooks.route('/healthcheck')
def healthcheck():
    return 'Webhooks are up and running!', 200
