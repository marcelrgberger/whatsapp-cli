"""Real-time monitoring — poll for new WhatsApp messages."""

from __future__ import annotations

import signal
import time
from datetime import datetime, timezone
from typing import Callable

from whatsapp_cli.core.chats import _resolve_jid
from whatsapp_cli.core.messages import get_messages
from whatsapp_cli.utils.wa_backend import (
    _get_db,
    _apple_ts_to_datetime,
    _datetime_to_apple_ts,
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def monitor_new_messages(
    callback: Callable[[list[dict]], None],
    interval: int = 5,
    jid_or_name: str | None = None,
) -> None:
    """Poll for new messages and invoke a callback when found.

    This function runs in an infinite loop and should be interrupted with
    Ctrl+C (SIGINT) or by raising KeyboardInterrupt.

    Args:
        callback: Function that receives a list of new message dicts.
            Called each time new messages are detected.
        interval: Polling interval in seconds. Defaults to 5.
        jid_or_name: Optional — restrict monitoring to a specific chat.
    """
    last_check = datetime.now(timezone.utc)

    # Resolve JID once at startup
    chat_pk = None
    if jid_or_name is not None:
        jid = _resolve_jid(jid_or_name)
        if jid is None:
            raise ValueError(f"Chat not found: {jid_or_name}")
        db = _get_db()
        try:
            row = db.execute(
                "SELECT Z_PK FROM ZWACHATSESSION WHERE ZCONTACTJID = ?",
                (jid,),
            ).fetchone()
            chat_pk = row["Z_PK"] if row else None
        finally:
            db.close()
        if chat_pk is None:
            raise ValueError(f"Chat session not found for: {jid_or_name}")

    # Graceful shutdown on SIGINT
    _running = True

    def _handle_signal(sig, frame):
        nonlocal _running
        _running = False

    original_handler = signal.signal(signal.SIGINT, _handle_signal)

    try:
        while _running:
            time.sleep(interval)
            new_messages = _poll_new_messages(last_check, chat_pk)
            if new_messages:
                last_check = datetime.now(timezone.utc)
                callback(new_messages)
            else:
                last_check = datetime.now(timezone.utc)
    finally:
        signal.signal(signal.SIGINT, original_handler)


def get_new_messages_since(
    timestamp: datetime,
    jid_or_name: str | None = None,
) -> list[dict]:
    """Get all messages received since a given timestamp.

    Args:
        timestamp: Cutoff datetime (timezone-aware or naive; naive assumed UTC).
        jid_or_name: Optional — restrict to a specific chat.

    Returns:
        List of message dicts newer than the timestamp.
    """
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)

    apple_ts = _datetime_to_apple_ts(timestamp)

    conditions = ["m.ZMESSAGEDATE > ?"]
    params: list = [apple_ts]

    if jid_or_name is not None:
        jid = _resolve_jid(jid_or_name)
        if jid is None:
            return []
        db = _get_db()
        try:
            row = db.execute(
                "SELECT Z_PK FROM ZWACHATSESSION WHERE ZCONTACTJID = ?",
                (jid,),
            ).fetchone()
            if row is None:
                return []
            conditions.append("m.ZCHATSESSION = ?")
            params.append(row["Z_PK"])
        finally:
            db.close()

    where = " AND ".join(conditions)

    db = _get_db()
    try:
        rows = db.execute(
            f"SELECT m.Z_PK, m.ZTEXT, m.ZISFROMME, m.ZMESSAGEDATE, "
            f"m.ZFROMJID, m.ZTOJID, m.ZMESSAGETYPE, m.ZSTARRED, "
            f"cs.ZPARTNERNAME AS _chat_name, cs.ZCONTACTJID AS _chat_jid, "
            f"CASE WHEN mi.Z_PK IS NOT NULL THEN 1 ELSE 0 END AS _has_media "
            f"FROM ZWAMESSAGE m "
            f"JOIN ZWACHATSESSION cs ON cs.Z_PK = m.ZCHATSESSION "
            f"LEFT JOIN ZWAMEDIAITEM mi ON mi.ZMESSAGE = m.Z_PK "
            f"WHERE {where} "
            f"ORDER BY m.ZMESSAGEDATE ASC",
            params,
        ).fetchall()

        return [_message_row_to_monitor_dict(row) for row in rows]
    finally:
        db.close()


def watch_chat(
    jid_or_name: str,
    callback: Callable[[list[dict]], None],
    interval: int = 3,
) -> None:
    """Watch a specific chat for new messages.

    Convenience wrapper around monitor_new_messages for a single chat.

    Args:
        jid_or_name: JID or contact/group name to watch.
        callback: Function called with new messages.
        interval: Polling interval in seconds. Defaults to 3.
    """
    monitor_new_messages(callback, interval=interval, jid_or_name=jid_or_name)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _poll_new_messages(since: datetime, chat_pk: int | None = None) -> list[dict]:
    """Poll the database for messages newer than 'since'.

    Args:
        since: Cutoff datetime (UTC).
        chat_pk: Optional Z_PK to restrict to a single chat.

    Returns:
        List of message dicts.
    """
    apple_ts = _datetime_to_apple_ts(since)

    conditions = ["m.ZMESSAGEDATE > ?"]
    params: list = [apple_ts]

    if chat_pk is not None:
        conditions.append("m.ZCHATSESSION = ?")
        params.append(chat_pk)

    where = " AND ".join(conditions)

    db = _get_db()
    try:
        rows = db.execute(
            f"SELECT m.Z_PK, m.ZTEXT, m.ZISFROMME, m.ZMESSAGEDATE, "
            f"m.ZFROMJID, m.ZTOJID, m.ZMESSAGETYPE, m.ZSTARRED, "
            f"cs.ZPARTNERNAME AS _chat_name, cs.ZCONTACTJID AS _chat_jid, "
            f"CASE WHEN mi.Z_PK IS NOT NULL THEN 1 ELSE 0 END AS _has_media "
            f"FROM ZWAMESSAGE m "
            f"JOIN ZWACHATSESSION cs ON cs.Z_PK = m.ZCHATSESSION "
            f"LEFT JOIN ZWAMEDIAITEM mi ON mi.ZMESSAGE = m.Z_PK "
            f"WHERE {where} "
            f"ORDER BY m.ZMESSAGEDATE ASC",
            params,
        ).fetchall()

        return [_message_row_to_monitor_dict(row) for row in rows]
    finally:
        db.close()


def _message_row_to_monitor_dict(row) -> dict:
    """Convert a message row (with joined chat info) to a monitor dict."""
    msg_time = _apple_ts_to_datetime(row["ZMESSAGEDATE"])
    return {
        "chat_name": row["_chat_name"],
        "chat_jid": row["_chat_jid"],
        "sender": row.get("ZFROMJID") or ("me" if row["ZISFROMME"] else "unknown"),
        "text": row["ZTEXT"],
        "time": msg_time.isoformat() if msg_time else None,
        "is_from_me": bool(row["ZISFROMME"]),
        "message_type": row.get("ZMESSAGETYPE", 0),
        "starred": bool(row.get("ZSTARRED", 0)),
        "has_media": bool(row.get("_has_media", 0)),
        "z_pk": row["Z_PK"],
    }
