from flask import request

from app import logger
from app.constants import LogTypes as logconstants
from app.webhooks import webhooks


@webhooks.route('/youtube', methods=['GET', 'POST'])
def youtube_webhook():
    # from app import bot

    challenge = request.args.get('hub.challenge')

    if (challenge):
        return challenge

    logger.info(f'Received Youtube webhook: {request.data}', log_type=logconstants.COMMAND_INFO_TYPE)

    return 'Processed', 204
