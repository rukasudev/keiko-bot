from datetime import datetime, timedelta, timezone

from flask import request

from app import logger
from app.constants import Commands as commands_constants
from app.constants import LogTypes as logconstants
from app.exceptions import ErrorContext
from app.integrations.reminder_webhook import REMINDER_TIMEZONE, reminder_time
from app.services.trace import as_utc
from app.webhooks import webhooks
from app.webhooks.jobs import schedule_webhook_job


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
            if title == 'youtube_notification':
                renew_youtube_subscription(reminder_id)
            elif title == commands_constants.REMINDER_API_TITLE_BIRTHDAY:
                process_birthday_reminder(reminder_id, reminder.get('notes'))
            else:
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


def process_birthday_reminder(reminder_id: str, notes: str) -> None:
    from app.data import birthdays as birthdays_data
    from app.webhooks.birthday_handler import process_birthday_webhook

    if not birthdays_data.has_birthday_reminder(reminder_id):
        logger.warn(
            f"birthday reminder {reminder_id} — not one of **Keiko**'s, ignored",
            log_type=logconstants.COMMAND_WARN_TYPE,
        )
        return

    logger.info(
        f"birthday reminder {reminder_id} — date {notes}",
        log_type=logconstants.COMMAND_INFO_TYPE,
    )
    schedule_webhook_job(
        process_birthday_webhook(reminder_id, notes),
        f"birthday reminder {reminder_id}",
    )


def renew_youtube_subscription(reminder_id: str) -> None:
    from app import bot
    from app.data.reminder import find_reminder_by_id, stamp_hub_confirmation
    from app.services.notifications_youtube_video import (
        HubOutcome,
        ask_the_hub,
        drop_unfollowed_renewal,
    )

    renewal = find_reminder_by_id(reminder_id)

    if not renewal:
        logger.warn(
            f"youtube reminder {reminder_id} — not one of **Keiko**'s, ignored",
            log_type=logconstants.COMMAND_WARN_TYPE,
        )
        return

    youtuber = renewal.get('value')

    if drop_unfollowed_renewal(renewal):
        logger.info(
            f'renewal of **{youtuber}** dropped — no server follows them any more',
            log_type=logconstants.COMMAND_INFO_TYPE,
        )
        return

    logger.info(
        f'renewing subscription — **{youtuber}**',
        log_type=logconstants.COMMAND_INFO_TYPE,
    )
    answer = ask_the_hub(youtuber, 'subscribe')
    now = reminder_time()

    if answer.outcome is HubOutcome.TRY_LATER:
        retry = now + timedelta(seconds=commands_constants.YOUTUBE_RENEWAL_RETRY_SECONDS)
        logger.warn(
            f'renewal of **{youtuber}** failed — {answer.detail}; '
            f'trying again at {retry:%H:%M} ({REMINDER_TIMEZONE})',
            log_type=logconstants.COMMAND_WARN_TYPE,
        )
        report_a_coming_lapse(renewal, reminder_id, now)
        bot.reminder.update_reminder(renewal.get('reminder_id'), retry.date(), time_tz=f'{retry:%H:%M}')
        return

    if answer.outcome is HubOutcome.DONE:
        stamp_hub_confirmation(youtuber, now.astimezone(timezone.utc))
    elif answer.outcome in (HubOutcome.NO_CHANNEL, HubOutcome.REFUSED):
        logger.error(
            f'renewal of **{youtuber}** failed — {answer.detail}; the next renewal tries again',
            log_type=logconstants.COMMAND_ERROR_TYPE,
            context=ErrorContext(
                flow="youtube_renewal",
                extra={"reminder_id": reminder_id, "youtuber": youtuber, "status": answer.status},
            ),
        )

    next_renewal = now + timedelta(seconds=commands_constants.YOUTUBE_RENEWAL_INTERVAL_SECONDS)
    bot.reminder.update_reminder(
        renewal.get('reminder_id'), next_renewal.date(), time_tz=f'{next_renewal:%H:%M}'
    )
    logger.info(
        f'renewal scheduled for {next_renewal:%Y-%m-%d %H:%M} ({REMINDER_TIMEZONE})',
        log_type=logconstants.COMMAND_INFO_TYPE,
    )


def report_a_coming_lapse(renewal: dict, reminder_id: str, now: datetime) -> None:
    """Tell the error channel once that a renewal failing for days is about to let its lease end."""
    from app.data.reminder import mark_lapse_reported

    youtuber = renewal.get('value')
    confirmed = as_utc(renewal.get('hub_confirmed_at') or renewal.get('created_at'))
    lease = timedelta(seconds=commands_constants.YOUTUBE_HUB_LEASE_SECONDS)
    lapses_at = (confirmed + lease if confirmed else now).astimezone(timezone.utc)
    margin = timedelta(seconds=commands_constants.YOUTUBE_HUB_LEASE_MARGIN_SECONDS)

    if renewal.get('lapse_reported_at') or now < lapses_at - margin:
        return

    logger.error(
        f'renewal of **{youtuber}** keeps failing — its hub subscription lapses at '
        f'{lapses_at:%Y-%m-%d %H:%M} UTC',
        log_type=logconstants.COMMAND_ERROR_TYPE,
        context=ErrorContext(
            flow="youtube_renewal",
            extra={
                "reminder_id": reminder_id,
                "youtuber": youtuber,
                "lease_lapses_at": lapses_at.isoformat(),
            },
        ),
    )
    mark_lapse_reported(youtuber, now.astimezone(timezone.utc))
