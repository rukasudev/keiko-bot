from datetime import datetime, timedelta

from flask import request

from app import logger
from app.constants import Commands as commands_constants
from app.constants import LogTypes as logconstants
from app.webhooks import webhooks


@webhooks.route('/reminder', methods=['GET', 'POST'])
def reminder_webhook():
    logger.info('Reminder webhook received', log_type=logconstants.COMMAND_INFO_TYPE)

    for reminder in request.json.get('reminders_notified'):
        logger.info(f'Reminder: {reminder}', log_type=logconstants.COMMAND_INFO_TYPE)
        logger.info(f"Reminder title: {reminder.get('title')}", log_type=logconstants.COMMAND_INFO_TYPE)

        if reminder.get('title') == 'youtube_notification':
            proccess_youtube_notification(reminder.get('id'), reminder.get('notes'))

        if reminder.get('title') == commands_constants.REMINDER_API_TITLE_BIRTHDAY:
            process_birthday_reminder(reminder.get('id'), reminder.get('notes'))

    return 'Reminder webhook received', 200


def process_birthday_reminder(reminder_id: str, notes: str) -> None:
    from app import bot
    from app.services.reminders_birthdays import process_birthday_webhook
    logger.info(f"Processing birthday reminder: {reminder_id}", log_type=logconstants.COMMAND_INFO_TYPE)
    logger.info(f"Date: {notes}", log_type=logconstants.COMMAND_INFO_TYPE)

    bot.loop.create_task(process_birthday_webhook(reminder_id, notes))

def proccess_youtube_notification(reminder_id: str, youtuber: str):
    from app import bot

    logger.info(f'Renewing youtube notification subscription for **{youtuber}**', log_type=logconstants.COMMAND_INFO_TYPE)

    channel_id = bot.youtube.get_channel_id_from_username(youtuber)
    if not channel_id:
        logger.error(f'Channel id not found for youtuber {youtuber}', log_type=logconstants.COMMAND_ERROR_TYPE)
        return

    bot.youtube.subscribe_to_new_video_event(channel_id)
    logger.info(
        f"Youtuber {youtuber} resubscribed",
        log_type=logconstants.COMMAND_INFO_TYPE,
    )

    new_renew_date = datetime.now() + timedelta(days=4)

    bot.reminder.update_reminder(reminder_id, new_renew_date.date())
    logger.info(f'Reminder updated. New renew date: {new_renew_date.date()}', log_type=logconstants.COMMAND_INFO_TYPE)
