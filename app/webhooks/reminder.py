from datetime import datetime, timedelta

from flask import request

from app import logger
from app.constants import LogTypes as logconstants
from app.webhooks import webhooks


@webhooks.route('/reminder', methods=['GET', 'POST'])
def reminder_webhook():
    logger.info('Reminder webhook received', log_type=logconstants.COMMAND_INFO_TYPE)

    for reminder in request.json.get('reminders_notified'):
        logger.info(f'Reminder: {reminder}', log_type=logconstants.COMMAND_INFO_TYPE)

        if reminder.get('title') == 'youtube_notification':
            proccess_youtube_notification(reminder.get('id'), reminder.get('notes'))

    return 'Reminder webhook received', 200

def proccess_youtube_notification(reminder_id: str, youtuber: str):
    from app import bot

    logger.info(f'Renew youtube notification subscription for **{youtuber}**', log_type=logconstants.COMMAND_INFO_TYPE)

    new_renew_date = datetime.now() + timedelta(minutes=3)
    logger.info(f'New renew date: {new_renew_date}', log_type=logconstants.COMMAND_INFO_TYPE)

    # Update the reminder with the new renew date
    bot.reminder.update_reminder(reminder_id, {'renew_date': new_renew_date})

