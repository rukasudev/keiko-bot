from flask import request

from app import logger
from app.constants import LogTypes as logconstants
from app.core import dispatch
from app.core.commands import Reminder
from app.exceptions import ErrorContext
from app.integrations.http import webhooks


@webhooks.route('/reminder', methods=['GET', 'POST'])
def reminder_webhook():
    from app import bot

    if not bot.reminder.verify_basic_auth(request.authorization):
        logger.warn(
            'reminder webhook refused — missing or wrong credentials',
            log_type=logconstants.COMMAND_WARN_TYPE,
        )
        return 'Unauthorized', 401, {'WWW-Authenticate': 'Basic realm="keiko"'}

    payload = request.get_json(silent=True)
    reminders = (payload.get('reminders_notified') if isinstance(payload, dict) else None) or []
    logger.info(
        f'{len(reminders)} reminder(s) notified',
        log_type=logconstants.COMMAND_INFO_TYPE,
    )

    failed = 0
    for reminder in reminders:
        reminder_id, title = reminder.get('id'), reminder.get('title')
        try:
            fired = Reminder(str(reminder_id), str(title), reminder.get('notes'))
            if not dispatch.reminder(fired):
                logger.warn(
                    f'Unknown reminder title: {title}',
                    log_type=logconstants.COMMAND_WARN_TYPE,
                )
        except Exception as error:
            failed += 1
            logger.error(
                f'reminder {reminder_id} ({title}) failed: {type(error).__name__}: {error}',
                log_type=logconstants.COMMAND_ERROR_TYPE,
                context=ErrorContext(
                    flow="reminder_webhook",
                    extra={"reminder_id": reminder_id, "title": title},
                ),
                exc_info=True,
            )

    if failed:
        logger.warn(
            f'{failed} of {len(reminders)} reminder(s) failed — answering 500 so reminders-api sends them again',
            log_type=logconstants.COMMAND_WARN_TYPE,
        )
        return 'Some reminders failed', 500
    return 'Reminder webhook received', 200
