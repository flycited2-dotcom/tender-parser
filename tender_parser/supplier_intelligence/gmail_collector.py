"""Read-only Gmail API access for one independently authorized mailbox.

The caller owns its durable checkpoint. Save the returned history ID only after
all pages and messages from a cycle have been processed successfully. If a
history cursor expires, scan message IDs again and compare them with the
caller's processed-message store.
"""

from __future__ import annotations

import os
import json
import random
import tempfile
import time
from pathlib import Path
from typing import Any

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError


GMAIL_READONLY_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
SCOPES = (GMAIL_READONLY_SCOPE,)


class AuthorizationRequired(RuntimeError):
    """The mailbox has no usable token and interactive consent is disabled."""


class AccountMismatch(RuntimeError):
    """The OAuth token belongs to a different Gmail mailbox."""


class HistoryCursorExpired(RuntimeError):
    """Gmail discarded the history cursor; rescan IDs against local state."""


class GmailCollector:
    """Read a single Gmail account using its own OAuth refresh token.

    ``client_secret_path`` can be shared by multiple instances, but every
    account must have a distinct ``token_path``. No auth flow starts in the
    constructor. Use ``authorize(interactive=True)`` once per account in a
    local CLI session, then ``authorize()`` for scheduled runs.
    """

    def __init__(self, account: str, client_secret_path: str | Path, token_path: str | Path,
                 *, min_request_interval_seconds: float = 0.25):
        self.account = account.strip().lower()
        if not self.account or "@" not in self.account:
            raise ValueError("A Gmail account address is required")
        self.client_secret_path = Path(client_secret_path).expanduser()
        self.token_path = Path(token_path).expanduser()
        self._service: Any | None = None
        self._min_request_interval_seconds = min_request_interval_seconds
        self._last_request_at = 0.0

    def authorize(self, interactive: bool = False) -> dict[str, Any]:
        """Load/refresh this account's token; optionally run local OAuth consent.

        Returns the verified Gmail profile. A wrong-account token is never
        saved. Interactive consent requires a local browser and OAuth client
        secret for an installed/desktop application.
        """

        credentials: Credentials | None = None
        if self.token_path.is_file():
            try:
                credentials = Credentials.from_authorized_user_file(
                    str(self.token_path), scopes=SCOPES
                )
            except (OSError, ValueError):
                if not interactive:
                    raise AuthorizationRequired(
                        f"Invalid OAuth token for {self.account}: {self.token_path}"
                    ) from None

        persist_token = False
        if credentials and credentials.expired and credentials.refresh_token:
            try:
                credentials.refresh(Request())
                persist_token = True
            except Exception as exc:
                if not interactive:
                    raise AuthorizationRequired(
                        f"OAuth refresh failed for {self.account}; reauthorize locally"
                    ) from exc
                credentials = None

        if not credentials or not credentials.valid:
            if not interactive:
                raise AuthorizationRequired(
                    f"No usable OAuth token for {self.account}: {self.token_path}"
                )
            if not self.client_secret_path.is_file():
                raise FileNotFoundError(
                    f"OAuth client secret is missing: {self.client_secret_path}"
                )
            from google_auth_oauthlib.flow import InstalledAppFlow

            flow = InstalledAppFlow.from_client_secrets_file(
                str(self.client_secret_path), scopes=SCOPES
            )
            credentials = flow.run_local_server(
                host="localhost", port=0, open_browser=True,
                access_type="offline", prompt="consent",
            )
            persist_token = True

        service = build("gmail", "v1", credentials=credentials, cache_discovery=False)
        self._service = service
        try:
            profile = self.profile()
            actual_account = str(profile.get("emailAddress") or "").lower()
            if actual_account != self.account:
                raise AccountMismatch(
                    f"Token for {self.account} belongs to {actual_account or 'unknown account'}"
                )
        except Exception:
            self._service = None
            raise

        if persist_token:
            self._save_token(credentials)
        return profile

    def profile(self) -> dict[str, Any]:
        """Return Gmail's profile for the authorized mailbox."""

        return self._execute(self._api().users().getProfile(userId="me"))

    def list_message_ids(
        self, page_token: str | None = None, page_size: int = 500
    ) -> tuple[list[str], str | None, str | None]:
        """Return one page of ordinary-mail IDs, next token, and history ID.

        An unfiltered Gmail list includes Inbox, Sent, archived mail and other
        ordinary labels. Spam and Trash are excluded. The returned history ID
        is a candidate checkpoint and must not be persisted until the whole
        backfill page sequence has succeeded.
        """

        if not 1 <= page_size <= 500:
            raise ValueError("page_size must be between 1 and 500")
        request: dict[str, Any] = {
            "userId": "me",
            "maxResults": page_size,
            "includeSpamTrash": False,
        }
        if page_token:
            request["pageToken"] = page_token
        # messages.list omits historyId. Capture the mailbox cursor before the
        # *first* page only. Later profile cursors could skip mail arriving
        # during a long backfill if the caller accidentally saved the last one.
        history_id = None
        if not page_token:
            history_id = str(self.profile().get("historyId") or "") or None
        response = self._execute(self._api().users().messages().list(**request))
        ids = [str(item["id"]) for item in response.get("messages", []) if item.get("id")]
        return ids, response.get("nextPageToken"), history_id

    def list_history(
        self, start_history_id: str | int, page_token: str | None = None
    ) -> tuple[list[str], str | None, str | None]:
        """Return changed message IDs, next token and latest history ID.

        A 404 from Gmail raises ``HistoryCursorExpired``. The caller should
        rescan message IDs, skip already processed IDs, and then persist a new
        profile history ID. Deletions are omitted because there is no message
        body to parse; label changes are included because labels can add RFQ
        context to an existing message.
        """

        if not str(start_history_id).strip():
            raise ValueError("start_history_id is required")
        request: dict[str, Any] = {
            "userId": "me",
            "startHistoryId": str(start_history_id),
            "maxResults": 500,
        }
        if page_token:
            request["pageToken"] = page_token
        try:
            response = self._execute(self._api().users().history().list(**request))
        except HttpError as exc:
            if int(getattr(exc.resp, "status", 0)) == 404:
                raise HistoryCursorExpired(
                    f"Gmail history cursor expired for {self.account}"
                ) from exc
            raise

        ids: list[str] = []
        seen: set[str] = set()
        for event in response.get("history", []):
            deleted = {
                str(item["message"]["id"])
                for item in event.get("messagesDeleted", [])
                if item.get("message", {}).get("id")
            }
            details = list(event.get("messages", [])) + [
                item.get("message", {})
                for kind in ("messagesAdded", "labelsAdded", "labelsRemoved")
                for item in event.get(kind, [])
            ]
            for item in details:
                message_id = str(item.get("id") or "")
                if message_id and message_id not in deleted and message_id not in seen:
                    seen.add(message_id)
                    ids.append(message_id)
        return ids, response.get("nextPageToken"), str(response.get("historyId") or "") or None

    def get_message(self, message_id: str) -> dict[str, Any]:
        """Return the raw Gmail message in ``full`` format."""

        return self._execute(self._api().users().messages().get(
            userId="me", id=message_id, format="full"
        ))

    def get_attachment(self, message_id: str, attachment_id: str) -> dict[str, Any]:
        """Return raw Gmail attachment JSON with base64url ``data``."""

        return self._execute(self._api().users().messages().attachments().get(
            userId="me", messageId=message_id, id=attachment_id
        ))

    def _execute(self, request: Any) -> dict[str, Any]:
        """Throttle read calls and back off when Gmail temporarily limits them."""
        for attempt in range(7):
            wait = self._last_request_at + self._min_request_interval_seconds - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            self._last_request_at = time.monotonic()
            try:
                return request.execute()
            except HttpError as exc:
                if attempt == 6 or not _retryable_gmail_error(exc):
                    raise
                time.sleep(min(2 ** attempt, 64) + random.random())
        raise RuntimeError("Gmail request retry loop exhausted")

    def _api(self) -> Any:
        if self._service is None:
            raise AuthorizationRequired(f"Call authorize() for {self.account} first")
        return self._service

    def _save_token(self, credentials: Credentials) -> None:
        self.token_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=self.token_path.parent,
                prefix=".gmail-token-", suffix=".tmp", delete=False,
            ) as file:
                temp_path = Path(file.name)
                file.write(credentials.to_json())
                file.flush()
                os.fsync(file.fileno())
            os.chmod(temp_path, 0o600)
            os.replace(temp_path, self.token_path)
        finally:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)


def _retryable_gmail_error(exc: HttpError) -> bool:
    status = int(getattr(exc.resp, "status", 0))
    if status in {429, 500, 502, 503, 504}:
        return True
    if status != 403:
        return False
    try:
        payload = json.loads(exc.content)
        details = payload.get("error", {})
        reasons = {str(item.get("reason") or "") for item in details.get("errors", [])}
        reasons.update(str(item.get("reason") or "") for item in details.get("details", []))
    except (TypeError, ValueError, AttributeError):
        return False
    return bool(reasons & {"rateLimitExceeded", "userRateLimitExceeded", "RATE_LIMIT_EXCEEDED"})
