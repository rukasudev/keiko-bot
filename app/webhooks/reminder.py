from datetime import datetime, timedelta

from flask import request

from app import logger
from app.constants import Commands as commands_constants
from app.constants import LogTypes as logconstants
from app.webhooks import webhooks
from app.webhooks.jobs import schedule_webhook_job


@webhooks.route('/reminder', methods=['GET', 'POST'])
def reminder_webhook():
    reminders = request.json.get('reminders_notified') or []
    logger.info(
        f'{len(reminders)} reminder(s) notified',
        log_type=logconstants.COMMAND_INFO_TYPE,
    )

    for reminder in reminders:
        title = reminder.get('title')

        if title == 'youtube_notification':
            proccess_youtube_notification(reminder.get('id'), reminder.get('notes'))
        elif title == commands_constants.REMINDER_API_TITLE_BIRTHDAY:
            process_birthday_reminder(reminder.get('id'), reminder.get('notes'))
        else:
            logger.warn(
                f'Unknown reminder title: {title}',
                log_type=logconstants.COMMAND_WARN_TYPE,
            )

    return 'Reminder webhook received', 200


def process_birthday_reminder(reminder_id: str, notes: str) -> None:
    from app.webhooks.birthday_handler import process_birthday_webhook

    logger.info(
        f"birthday reminder {reminder_id} — date {notes}",
        log_type=logconstants.COMMAND_INFO_TYPE,
    )
    schedule_webhook_job(
        process_birthday_webhook(reminder_id, notes),
        f"birthday reminder {reminder_id}",
    )

def proccess_youtube_notification(reminder_id: str, youtuber: str):
    from app import bot

    logger.info(
        f'renewing subscription — {youtuber}',
        log_type=logconstants.COMMAND_INFO_TYPE,
    )

    channel_id = bot.youtube.get_channel_id_from_username(youtuber)
    if not channel_id:
        logger.error(f'Channel id not found for youtuber {youtuber}', log_type=logconstants.COMMAND_ERROR_TYPE)
        return

    bot.youtube.subscribe_to_new_video_event(channel_id)

    new_renew_date = datetime.now() + timedelta(days=4)

    bot.reminder.update_reminder(reminder_id, new_renew_date.date())
    logger.info(
        f'renewal scheduled for {new_renew_date.date()}',
        log_type=logconstants.COMMAND_INFO_TYPE,
    )
