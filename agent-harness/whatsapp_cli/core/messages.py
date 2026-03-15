"""Message operations — read messages from SQLite, send via UI automation."""

from __future__ import annotations

from datetime import datetime, timezone

from whatsapp_cli.core.chats import _resolve_jid
from whatsapp_cli.utils.wa_backend import (
    _get_db,
    _apple_ts_to_datetime,
    _datetime_to_apple_ts,
    send_via_ui,
    send_file as _backend_send_file,
)


# ---------------------------------------------------------------------------
# Message type mapping
# ---------------------------------------------------------------------------

_MESSAGE_TYPE_MAP = {
    0: "text",
    1: "image",
    2: "video",
    3: "voice",
    4: "contact",
    5: "location",
    6: "system",
    7: "link",
    8: "document",
    9: "audio",
    14: "deleted",
    15: "sticker",
}


def _message_type_label(code: int | None) -> str:
    """Map ZMESSAGETYPE integer to a readable label."""
    if code is None:
        return "unknown"
    return _MESSAGE_TYPE_MAP.get(code, f"other({code})")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get_chat_pk(jid_or_name: str) -> int | None:
    """Resolve a JID or name to the Z_PK of the chat session.

    Args:
        jid_or_name: A JID or contact/group name.

    Returns:
        Z_PK integer or None if not found.
    """
    jid = _resolve_jid(jid_or_name)
    if jid is None:
        return None

    db = _get_db()
    try:
        row = db.execute(
            "SELECT Z_PK FROM ZWACHATSESSION WHERE ZCONTACTJID = ?",
            (jid,),
        ).fetchone()
        return row["Z_PK"] if row else None
    finally:
        db.close()


def _message_row_to_dict(row) -> dict:
    """Convert a ZWAMESSAGE row to a standardised dict."""
    msg_time = _apple_ts_to_datetime(row["ZMESSAGEDATE"])
    return {
        "sender": row.get("ZFROMJID") or ("me" if row["ZISFROMME"] else "unknown"),
        "text": row["ZTEXT"],
        "time": msg_time.isoformat() if msg_time else None,
        "is_from_me": bool(row["ZISFROMME"]),
        "message_type": _message_type_label(row.get("ZMESSAGETYPE")),
        "starred": bool(row.get("ZSTARRED", 0)),
        "has_media": bool(row.get("_has_media", 0)),
        "z_pk": row["Z_PK"],
    }


# ---------------------------------------------------------------------------
# Public API — Read
# ---------------------------------------------------------------------------

def get_messages(
    jid_or_name: str,
    limit: int = 50,
    before: datetime | None = None,
    after: datetime | None = None,
) -> list[dict]:
    """Get messages from a specific chat.

    Args:
        jid_or_name: JID or contact/group name to fetch messages from.
        limit: Maximum number of messages to return. Defaults to 50.
        before: Only return messages before this datetime.
        after: Only return messages after this datetime.

    Returns:
        List of message dicts sorted by time ascending (oldest first).
        Each dict has: sender, text, time, is_from_me, message_type,
        starred, has_media, z_pk.
    """
    chat_pk = _get_chat_pk(jid_or_name)
    if chat_pk is None:
        return []

    conditions = ["m.ZCHATSESSION = ?"]
    params: list = [chat_pk]

    if before is not None:
        conditions.append("m.ZMESSAGEDATE < ?")
        params.append(_datetime_to_apple_ts(before))
    if after is not None:
        conditions.append("m.ZMESSAGEDATE > ?")
        params.append(_datetime_to_apple_ts(after))

    where = " AND ".join(conditions)

    query = (
        f"SELECT m.Z_PK, m.ZTEXT, m.ZISFROMME, m.ZMESSAGEDATE, "
        f"m.ZFROMJID, m.ZTOJID, m.ZMESSAGETYPE, m.ZSTARRED, "
        f"CASE WHEN mi.Z_PK IS NOT NULL THEN 1 ELSE 0 END AS _has_media "
        f"FROM ZWAMESSAGE m "
        f"LEFT JOIN ZWAMEDIAITEM mi ON mi.ZMESSAGE = m.Z_PK "
        f"WHERE {where} "
        f"ORDER BY m.ZMESSAGEDATE DESC "
        f"LIMIT ?"
    )
    params.append(limit)

    db = _get_db()
    try:
        rows = db.execute(query, params).fetchall()
        # Return in chronological order (oldest first)
        messages = [_message_row_to_dict(row) for row in rows]
        messages.reverse()
        return messages
    finally:
        db.close()


def search_messages(
    query: str,
    jid_or_name: str | None = None,
) -> list[dict]:
    """Full-text search across messages.

    Args:
        query: Text to search for (case-insensitive substring match).
        jid_or_name: Optional — restrict search to a specific chat.

    Returns:
        List of message dicts matching the query, sorted by time descending.
    """
    conditions = ["m.ZTEXT LIKE ?"]
    params: list = [f"%{query}%"]

    if jid_or_name is not None:
        chat_pk = _get_chat_pk(jid_or_name)
        if chat_pk is None:
            return []
        conditions.append("m.ZCHATSESSION = ?")
        params.append(chat_pk)

    where = " AND ".join(conditions)

    sql = (
        f"SELECT m.Z_PK, m.ZTEXT, m.ZISFROMME, m.ZMESSAGEDATE, "
        f"m.ZFROMJID, m.ZTOJID, m.ZMESSAGETYPE, m.ZSTARRED, "
        f"CASE WHEN mi.Z_PK IS NOT NULL THEN 1 ELSE 0 END AS _has_media "
        f"FROM ZWAMESSAGE m "
        f"LEFT JOIN ZWAMEDIAITEM mi ON mi.ZMESSAGE = m.Z_PK "
        f"WHERE {where} "
        f"ORDER BY m.ZMESSAGEDATE DESC "
        f"LIMIT 100"
    )

    db = _get_db()
    try:
        rows = db.execute(sql, params).fetchall()
        return [_message_row_to_dict(row) for row in rows]
    finally:
        db.close()


def get_starred_messages(jid_or_name: str | None = None) -> list[dict]:
    """Get starred (bookmarked) messages.

    Args:
        jid_or_name: Optional — restrict to a specific chat.

    Returns:
        List of starred message dicts.
    """
    conditions = ["m.ZSTARRED = 1"]
    params: list = []

    if jid_or_name is not None:
        chat_pk = _get_chat_pk(jid_or_name)
        if chat_pk is None:
            return []
        conditions.append("m.ZCHATSESSION = ?")
        params.append(chat_pk)

    where = " AND ".join(conditions)

    sql = (
        f"SELECT m.Z_PK, m.ZTEXT, m.ZISFROMME, m.ZMESSAGEDATE, "
        f"m.ZFROMJID, m.ZTOJID, m.ZMESSAGETYPE, m.ZSTARRED, "
        f"CASE WHEN mi.Z_PK IS NOT NULL THEN 1 ELSE 0 END AS _has_media "
        f"FROM ZWAMESSAGE m "
        f"LEFT JOIN ZWAMEDIAITEM mi ON mi.ZMESSAGE = m.Z_PK "
        f"WHERE {where} "
        f"ORDER BY m.ZMESSAGEDATE DESC"
    )

    db = _get_db()
    try:
        rows = db.execute(sql, params).fetchall()
        return [_message_row_to_dict(row) for row in rows]
    finally:
        db.close()


def get_media_messages(jid_or_name: str, limit: int = 20) -> list[dict]:
    """Get messages that have media attachments from a specific chat.

    Args:
        jid_or_name: JID or contact/group name.
        limit: Maximum number of results. Defaults to 20.

    Returns:
        List of message dicts enriched with media info (local_path, file_size,
        duration, media_url, title).
    """
    chat_pk = _get_chat_pk(jid_or_name)
    if chat_pk is None:
        return []

    sql = (
        "SELECT m.Z_PK, m.ZTEXT, m.ZISFROMME, m.ZMESSAGEDATE, "
        "m.ZFROMJID, m.ZTOJID, m.ZMESSAGETYPE, m.ZSTARRED, "
        "1 AS _has_media, "
        "mi.ZMEDIALOCALPATH, mi.ZFILESIZE, mi.ZMOVIEDURATION, "
        "mi.ZMEDIAURL, mi.ZTITLE "
        "FROM ZWAMESSAGE m "
        "INNER JOIN ZWAMEDIAITEM mi ON mi.ZMESSAGE = m.Z_PK "
        "WHERE m.ZCHATSESSION = ? "
        "ORDER BY m.ZMESSAGEDATE DESC "
        "LIMIT ?"
    )

    db = _get_db()
    try:
        rows = db.execute(sql, (chat_pk, limit)).fetchall()
        results = []
        for row in rows:
            msg = _message_row_to_dict(row)
            msg["media"] = {
                "local_path": row["ZMEDIALOCALPATH"],
                "file_size": row["ZFILESIZE"],
                "duration": row["ZMOVIEDURATION"],
                "media_url": row["ZMEDIAURL"],
                "title": row["ZTITLE"],
            }
            results.append(msg)
        return results
    finally:
        db.close()


def count_messages(jid_or_name: str | None = None) -> dict:
    """Count messages, optionally filtered to a specific chat.

    Args:
        jid_or_name: Optional — restrict count to a specific chat.

    Returns:
        Dict with keys: total, from_me, from_others.
        If jid_or_name is provided and not found, returns all zeros.
    """
    db = _get_db()
    try:
        if jid_or_name is not None:
            chat_pk = _get_chat_pk(jid_or_name)
            if chat_pk is None:
                return {"total": 0, "from_me": 0, "from_others": 0}

            total = db.execute(
                "SELECT COUNT(*) as cnt FROM ZWAMESSAGE WHERE ZCHATSESSION = ?",
                (chat_pk,),
            ).fetchone()["cnt"]

            from_me = db.execute(
                "SELECT COUNT(*) as cnt FROM ZWAMESSAGE "
                "WHERE ZCHATSESSION = ? AND ZISFROMME = 1",
                (chat_pk,),
            ).fetchone()["cnt"]
        else:
            total = db.execute(
                "SELECT COUNT(*) as cnt FROM ZWAMESSAGE"
            ).fetchone()["cnt"]

            from_me = db.execute(
                "SELECT COUNT(*) as cnt FROM ZWAMESSAGE WHERE ZISFROMME = 1"
            ).fetchone()["cnt"]

        return {
            "total": total,
            "from_me": from_me,
            "from_others": total - from_me,
        }
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Public API — Send
# ---------------------------------------------------------------------------

def send_message(phone_or_name: str, text: str) -> bool:
    """Send a message to a contact or group.

    Resolves names to phone numbers via the chat database, then uses
    URL scheme + UI automation to send.

    Args:
        phone_or_name: Phone number (with or without +), JID, or contact name.
        text: The message text to send.

    Returns:
        bool: True if the send sequence completed without errors.

    Raises:
        ValueError: If the recipient could not be resolved.
        RuntimeError: If WhatsApp is not available or the send fails.
    """
    import re

    target = phone_or_name

    # If it looks like a name (no digits or @), resolve to JID first
    if not re.search(r"[\d@]", target):
        jid = _resolve_jid_for_send(target)
        if jid is None:
            raise ValueError(
                f"Could not find a chat for '{phone_or_name}'. "
                "Please use a phone number or JID."
            )
        target = jid

    return send_via_ui(target, text)


def _resolve_jid_for_send(name: str) -> str | None:
    """Resolve a display name to a JID for sending.

    Only individual chats (not groups) are considered.

    Args:
        name: Contact display name.

    Returns:
        JID string or None if not found.
    """
    db = _get_db()
    try:
        row = db.execute(
            "SELECT ZCONTACTJID FROM ZWACHATSESSION "
            "WHERE ZPARTNERNAME LIKE ? AND ZSESSIONTYPE = 0 LIMIT 1",
            (f"%{name}%",),
        ).fetchone()
        return row["ZCONTACTJID"] if row else None
    finally:
        db.close()


def send_file(phone_or_name: str, file_path: str, caption: str = "") -> dict:
    """Send a file (image/video/document) to a contact or group.

    Resolves names to JIDs via the chat database, validates the file exists,
    then uses clipboard + UI automation to send.

    Args:
        phone_or_name: Phone number (with or without +), JID, or contact/group name.
        file_path: Path to the file to send.
        caption: Optional caption text for the file.

    Returns:
        dict: Result with keys: sent (bool), to, file_path, caption.

    Raises:
        ValueError: If the recipient could not be resolved.
        FileNotFoundError: If the file does not exist.
        RuntimeError: If WhatsApp is not available or the send fails.
    """
    import os
    import re

    # Validate file exists
    abs_path = os.path.abspath(os.path.expanduser(file_path))
    if not os.path.isfile(abs_path):
        raise FileNotFoundError(f"File not found: {abs_path}")

    target = phone_or_name

    # If it looks like a name (no digits or @), resolve to JID first
    if not re.search(r"[\d@]", target):
        jid = _resolve_jid(target)
        if jid is None:
            raise ValueError(
                f"Could not find a chat for '{phone_or_name}'. "
                "Please use a phone number or JID."
            )
        target = jid

    _backend_send_file(target, abs_path, caption=caption)

    return {
        "sent": True,
        "to": phone_or_name,
        "file_path": abs_path,
        "caption": caption,
    }
