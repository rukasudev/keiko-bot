from flask import request

from app import logger
from app.constants import LogTypes as logconstants
from app.webhooks import webhooks


@webhooks.route('/reminder', methods=['GET', 'POST'])
def reminder_webhook():
    logger.info('Reminder webhook received', log_type=logconstants.COMMAND_INFO_TYPE)
    logger.info(f'Reminder webhook data: {request.data}', log_type=logconstants.COMMAND_INFO_TYPE)
    return 'Reminder webhook received', 200
