"""Kick's public API: the app token, channels by slug, the live-status subscriptions and
the key Kick signs its webhooks with."""

import base64
import binascii
import threading
from dataclasses import dataclass
from http import HTTPStatus
from time import monotonic
from typing import Any, Callable, Dict, Iterable, List, Optional, Set, Tuple

from cryptography.exceptions import InvalidSignature, UnsupportedAlgorithm
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from requests import RequestException, Response

from app import logger
from app.constants import Commands
from app.constants import LogTypes as logconstants
from app.integrations import http_delete, http_get, http_post


class KickAPIError(RuntimeError):
    """Kick refused a call or did not answer, or Keiko holds no Kick credentials."""

    def __init__(self, message: str, status: Optional[int] = None) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class KickSubscription:
    """One event subscription Kick holds for Keiko's app."""

    id: str
    broadcaster_user_id: int
    event: str


@dataclass(frozen=True)
class KickResync:
    """What one resync left on Kick: the subscription it kept or created for each
    followed broadcaster, and the ones it deleted."""

    kept: Tuple[KickSubscription, ...]
    created: Tuple[KickSubscription, ...]
    deleted: Tuple[KickSubscription, ...]


class KickClient:
    """Keiko's app on Kick, authenticated with its own client credentials."""

    def __init__(self, bot: Any) -> None:
        self.bot = bot
        self.token: Optional[str] = None
        self.token_expires_at = 0.0
        self._public_key: Optional[rsa.RSAPublicKey] = None
        self._key_read_at: Optional[float] = None
        self._token_lock = threading.Lock()
        self._key_lock = threading.Lock()
        self._warned_unconfigured = False

    def channels(self, slugs: Iterable[str]) -> Dict[str, Dict[str, Any]]:
        """Each channel Kick knows among `slugs`, by its lowercased slug."""
        wanted = sorted(
            {
                slug
                for slug in (str(slug).strip().lower() for slug in slugs)
                if slug and len(slug) <= Commands.KICK_SLUG_MAX_LENGTH
            }
        )

        found: Dict[str, Dict[str, Any]] = {}
        size = Commands.KICK_CHANNELS_PER_REQUEST
        for start in range(0, len(wanted), size):
            answer = self._authorized(
                http_get,
                "channels",
                f"{Commands.KICK_API_URL}/channels",
                params=[("slug", slug) for slug in wanted[start : start + size]],
            )
            for channel in answer.get("data") or []:
                slug = str(channel.get("slug") or "").lower()
                if slug:
                    found[slug] = channel
        return found

    def subscribe_live_status(self, broadcaster_user_id: int) -> str:
        """Subscribe to a broadcaster's live starts and ends; the subscription id."""
        answer = self._authorized(
            http_post,
            "subscriptions",
            Commands.KICK_SUBSCRIPTIONS_URL,
            json={
                "broadcaster_user_id": int(broadcaster_user_id),
                "events": [
                    {
                        "name": Commands.KICK_LIVE_STATUS_EVENT,
                        "version": Commands.KICK_LIVE_STATUS_VERSION,
                    }
                ],
                "method": "webhook",
            },
        )

        created = (answer.get("data") or [{}])[0]
        subscription_id = created.get("subscription_id")
        if not subscription_id:
            reason = created.get("error") or "no subscription id"
            raise KickAPIError(
                f"Kick did not subscribe broadcaster {broadcaster_user_id}: {reason}"
            )
        return str(subscription_id)

    def subscriptions(self) -> List[KickSubscription]:
        """Every event subscription Kick holds for Keiko's app."""
        answer = self._authorized(
            http_get, "subscriptions", Commands.KICK_SUBSCRIPTIONS_URL
        )
        return [
            KickSubscription(
                id=str(entry["id"]),
                broadcaster_user_id=int(entry["broadcaster_user_id"]),
                event=str(entry.get("event") or ""),
            )
            for entry in answer.get("data") or []
            if entry.get("id") and entry.get("broadcaster_user_id") is not None
        ]

    def delete_subscription(self, subscription_id: str) -> None:
        """Drop one subscription, so Kick stops posting its events."""
        self._authorized(
            http_delete,
            "subscriptions",
            Commands.KICK_SUBSCRIPTIONS_URL,
            params={"id": subscription_id},
        )

    def resync(self, followed: Set[int]) -> KickResync:
        """Leave Kick one live subscription per followed broadcaster, and no other."""
        kept: Dict[int, KickSubscription] = {}
        surplus: List[KickSubscription] = []
        for subscription in self.subscriptions():
            if subscription.event != Commands.KICK_LIVE_STATUS_EVENT:
                continue
            broadcaster_user_id = subscription.broadcaster_user_id
            if broadcaster_user_id in followed and broadcaster_user_id not in kept:
                kept[broadcaster_user_id] = subscription
            else:
                surplus.append(subscription)

        missing = sorted(set(followed) - set(kept))
        created = [
            made for user in missing if (made := self._subscribed_in_resync(user))
        ]
        deleted = [gone for gone in surplus if self._deleted_in_resync(gone)]
        return KickResync(
            kept=tuple(kept.values()), created=tuple(created), deleted=tuple(deleted)
        )

    def public_key(self) -> rsa.RSAPublicKey:
        """The key Kick signs its webhooks with, read once; raises while Kick cannot
        hand it over, or was asked for it less than a window ago."""
        return self._held_key()[0]

    def verify_signature(
        self, message_id: str, timestamp: str, body: bytes, signature: str
    ) -> bool:
        """Whether Kick signed this message, a key held from before read again once
        when it refuses, since Kick may have rotated it; raises when the key cannot be
        read, or not again yet."""
        if not message_id or not timestamp or not signature:
            return False
        try:
            signed = base64.b64decode(signature, validate=True)
        except (binascii.Error, ValueError):
            return False

        message = f"{message_id}.{timestamp}.".encode("utf-8") + body
        key, read_now = self._held_key()
        if _signed_by(key, message, signed):
            return True
        if read_now:
            return False
        return _signed_by(self._key_after(key), message, signed)

    def _held_key(self) -> Tuple[rsa.RSAPublicKey, bool]:
        with self._key_lock:
            if self._public_key is not None:
                return self._public_key, False
            self._public_key = self._read_public_key()
            return self._public_key, True

    def _key_after(self, refused: rsa.RSAPublicKey) -> rsa.RSAPublicKey:
        with self._key_lock:
            if self._public_key is not None and self._public_key is not refused:
                return self._public_key
            self._public_key = self._read_public_key()
            return self._public_key

    def _read_public_key(self) -> rsa.RSAPublicKey:
        now = monotonic()
        last = self._key_read_at
        if last is not None and now - last < Commands.KICK_PUBLIC_KEY_RETRY_SECONDS:
            raise KickAPIError(
                "Kick's public key was asked for less than "
                f"{Commands.KICK_PUBLIC_KEY_RETRY_SECONDS} s ago"
            )
        self._key_read_at = now

        answer = self._answer(
            "public key",
            self._send(
                http_get,
                "public key",
                f"{Commands.KICK_API_URL}/public-key",
                self._headers(),
            ),
        )
        pem = str((answer.get("data") or {}).get("public_key") or "")
        try:
            key = serialization.load_pem_public_key(pem.encode("utf-8"))
        except (ValueError, UnsupportedAlgorithm) as error:
            raise KickAPIError(f"Kick's public key does not load: {error}") from error
        if not isinstance(key, rsa.RSAPublicKey):
            raise KickAPIError("Kick's public key is not an RSA key")
        return key

    def _subscribed_in_resync(
        self, broadcaster_user_id: int
    ) -> Optional[KickSubscription]:
        try:
            subscription_id = self.subscribe_live_status(broadcaster_user_id)
        except KickAPIError as error:
            logger.warn(
                f"kick resync could not subscribe broadcaster {broadcaster_user_id}: "
                f"{error}",
                log_type=logconstants.COMMAND_WARN_TYPE,
            )
            return None
        return KickSubscription(
            subscription_id, broadcaster_user_id, Commands.KICK_LIVE_STATUS_EVENT
        )

    def _deleted_in_resync(self, subscription: KickSubscription) -> bool:
        try:
            self.delete_subscription(subscription.id)
        except KickAPIError as error:
            logger.warn(
                f"kick resync could not delete subscription {subscription.id} of "
                f"broadcaster {subscription.broadcaster_user_id}: {error}",
                log_type=logconstants.COMMAND_WARN_TYPE,
            )
            return False
        return True

    def _authorized(
        self, verb: Callable[..., Response], what: str, url: str, **options: Any
    ) -> Dict[str, Any]:
        token = self._token()
        response = self._send(verb, what, url, self._bearer(token), **options)
        if response.status_code == HTTPStatus.UNAUTHORIZED:
            token = self._token(refused=token)
            response = self._send(verb, what, url, self._bearer(token), **options)
        return self._answer(what, response)

    def _bearer(self, token: str) -> Dict[str, str]:
        return {**self._headers(), "Authorization": f"Bearer {token}"}

    def _token(self, refused: Optional[str] = None) -> str:
        with self._token_lock:
            if refused is not None and self.token == refused:
                self.token = None
            if self.token is None or monotonic() >= self.token_expires_at:
                self._authenticate()
            assert self.token is not None
            return self.token

    def _authenticate(self) -> None:
        client_id, client_secret = self._credentials()
        answer = self._answer(
            "token",
            self._send(
                http_post,
                "token",
                Commands.KICK_TOKEN_URL,
                self._headers(),
                data={
                    "grant_type": "client_credentials",
                    "client_id": client_id,
                    "client_secret": client_secret,
                },
            ),
        )

        token = answer.get("access_token")
        if not token:
            raise KickAPIError("Kick's token answer carries no access token")
        lifetime = int(answer.get("expires_in") or Commands.KICK_TOKEN_DEFAULT_SECONDS)
        self.token = str(token)
        self.token_expires_at = (
            monotonic() + lifetime - Commands.KICK_TOKEN_MARGIN_SECONDS
        )

    def _credentials(self) -> Tuple[str, str]:
        client_id = self.bot.config.KICK_CLIENT_ID
        client_secret = self.bot.config.KICK_CLIENT_SECRET
        if client_id and client_secret:
            return str(client_id), str(client_secret)

        if not self._warned_unconfigured:
            self._warned_unconfigured = True
            logger.warn(
                "kick is off — no KICK_CLIENT_ID or KICK_CLIENT_SECRET is configured",
                log_type=logconstants.COMMAND_WARN_TYPE,
            )
        raise KickAPIError("Kick has no client id or secret configured")

    def _headers(self) -> Dict[str, str]:
        return {"User-Agent": Commands.KICK_USER_AGENT, "Accept": "application/json"}

    def _send(
        self,
        verb: Callable[..., Response],
        what: str,
        url: str,
        headers: Dict[str, str],
        **options: Any,
    ) -> Response:
        try:
            return verb(
                "kick",
                url,
                headers=headers,
                timeout=Commands.KICK_TIMEOUT_SECONDS,
                **options,
            )
        except RequestException as error:
            raise KickAPIError(
                f"Kick's {what} did not answer: {type(error).__name__}"
            ) from error

    def _answer(self, what: str, response: Response) -> Dict[str, Any]:
        status = response.status_code
        if not 200 <= status < 300:
            raise KickAPIError(f"Kick's {what} answered {status}", status=status)
        if status == HTTPStatus.NO_CONTENT:
            return {}

        try:
            answer = response.json()
        except ValueError as error:
            raise KickAPIError(
                f"Kick's {what} answered {status} without JSON"
            ) from error
        return answer if isinstance(answer, dict) else {}


def _signed_by(key: rsa.RSAPublicKey, message: bytes, signature: bytes) -> bool:
    try:
        key.verify(signature, message, padding.PKCS1v15(), hashes.SHA256())
    except InvalidSignature:
        return False
    return True
