"""The route Kick posts its signed events to: one live start or end per message."""

from flask import request
from redis.exceptions import RedisError

from app import logger
from app.constants import Commands
from app.constants import LogTypes as logconstants
from app.core import dispatch
from app.core.commands import WebhookEvent
from app.integrations.http import webhooks
from app.integrations.kick.client import KickAPIError


@webhooks.route("/kick", methods=["POST"])
def kick_webhook() -> tuple[str, int]:
    """Check Kick's signature, take each message once and hand a live to its command."""
    from app import bot

    message_id = request.headers.get("Kick-Event-Message-Id", "")
    try:
        signed = bot.kick.verify_signature(
            message_id,
            request.headers.get("Kick-Event-Message-Timestamp", ""),
            request.get_data(),
            request.headers.get("Kick-Event-Signature", ""),
        )
    except KickAPIError as error:
        logger.warn(
            f"kick notice sent back — its signing key cannot be read now: {error}",
            log_type=logconstants.COMMAND_WARN_TYPE,
        )
        return "Signing key unavailable", 503
    if not signed:
        logger.warn(
            "kick notice refused — missing or wrong signature",
            log_type=logconstants.COMMAND_WARN_TYPE,
        )
        return "Invalid signature", 403

    event_type = request.headers.get("Kick-Event-Type", "")
    if event_type != Commands.KICK_LIVE_STATUS_EVENT:
        logger.info(
            f"kick {event_type or 'unnamed'} event ignored. No action taken.",
            log_type=logconstants.COMMAND_INFO_TYPE,
        )
        return "Webhook processed", 200

    payload = request.get_json(force=True, silent=True)
    if not isinstance(payload, dict):
        payload = {}
    slug = str((payload.get("broadcaster") or {}).get("channel_slug") or "").lower()
    if not slug or not isinstance(payload.get("is_live"), bool):
        logger.warn(
            f"kick {event_type} came without a channel or its live status",
            log_type=logconstants.COMMAND_WARN_TYPE,
        )
        return "Webhook processed", 200

    claim = Commands.REDIS_KICK_EVENT_CLAIM.format(message_id=message_id)
    if not _first_delivery(claim, slug):
        logger.info(
            f"kick message {message_id} was already handled. No action taken.",
            log_type=logconstants.COMMAND_INFO_TYPE,
        )
        return "Already handled", 200

    status = "online" if payload["is_live"] else "offline"
    try:
        reply = dispatch.webhook(WebhookEvent(f"kick.stream.{status}", slug, payload))
    except Exception:
        _give_back(claim, slug)
        raise
    if reply is None:
        return "Webhook processed", 200
    if reply.status >= 500:
        _give_back(claim, slug)
    return reply.body, reply.status


def _first_delivery(claim: str, slug: str) -> bool:
    from app.core.cache import claim_redis_key

    try:
        return claim_redis_key(claim, Commands.KICK_EVENT_TTL_SECONDS)
    except RedisError as error:
        logger.warn(
            f"the kick message of **{slug}** is handled without its claim — Redis: "
            f"{type(error).__name__}",
            log_type=logconstants.COMMAND_WARN_TYPE,
        )
        return True


def _give_back(claim: str, slug: str) -> None:
    from app.core.cache import release_redis_key

    try:
        release_redis_key(claim)
    except RedisError as error:
        logger.warn(
            f"the claim of the kick message of **{slug}** stays until it expires "
            f"— Redis: {type(error).__name__}",
            log_type=logconstants.COMMAND_WARN_TYPE,
        )
