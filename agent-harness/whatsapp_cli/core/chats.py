"""Chat operations — read chat list and details from WhatsApp SQLite database."""

from __future__ import annotations

from whatsapp_cli.utils.wa_backend import _get_db, _apple_ts_to_datetime


# ---------------------------------------------------------------------------
# Session type mapping
# ---------------------------------------------------------------------------

_SESSION_TYPE_MAP = {
    0: "individual",
    1: "group",
    3: "status",
}


def _session_type_label(code: int | None) -> str:
    """Map ZSESSIONTYPE integer to a human-readable label."""
    if code is None:
        return "unknown"
    return _SESSION_TYPE_MAP.get(code, f"unknown({code})")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _resolve_jid(jid_or_name: str) -> str | None:
    """Resolve a JID or contact name to a JID.

    If the input contains '@', it is treated as a JID and returned as-is.
    Otherwise, search ZWACHATSESSION.ZPARTNERNAME for a match (case-insensitive).

    Args:
        jid_or_name: A JID string or a contact/group name.

    Returns:
        The JID string, or None if no match found.
    """
    if "@" in jid_or_name:
        return jid_or_name

    db = _get_db()
    try:
        row = db.execute(
            "SELECT ZCONTACTJID FROM ZWACHATSESSION "
            "WHERE ZPARTNERNAME LIKE ? LIMIT 1",
            (f"%{jid_or_name}%",),
        ).fetchone()
        return row["ZCONTACTJID"] if row else None
    finally:
        db.close()


def _chat_row_to_dict(row) -> dict:
    """Convert a ZWACHATSESSION row to a standardised dict."""
    last_msg_time = _apple_ts_to_datetime(row["ZLASTMESSAGEDATE"])
    return {
        "name": row["ZPARTNERNAME"],
        "jid": row["ZCONTACTJID"],
        "unread_count": row["ZUNREADCOUNT"] or 0,
        "last_message": row["ZLASTMESSAGETEXT"],
        "last_message_time": last_msg_time.isoformat() if last_msg_time else None,
        "session_type": _session_type_label(row["ZSESSIONTYPE"]),
        "z_pk": row["Z_PK"],
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def list_chats(
    limit: int = 50,
    include_groups: bool = True,
    include_status: bool = False,
) -> list[dict]:
    """List chats sorted by last message date (most recent first).

    Args:
        limit: Maximum number of chats to return. Defaults to 50.
        include_groups: Include group chats. Defaults to True.
        include_status: Include status broadcasts. Defaults to False.

    Returns:
        List of chat dicts with keys: name, jid, unread_count, last_message,
        last_message_time, session_type, z_pk.
    """
    conditions = []
    if not include_status:
        conditions.append("ZSESSIONTYPE != 3")
    if not include_groups:
        conditions.append("ZSESSIONTYPE != 1")

    where = ""
    if conditions:
        where = "WHERE " + " AND ".join(conditions)

    query = (
        f"SELECT Z_PK, ZPARTNERNAME, ZCONTACTJID, ZUNREADCOUNT, "
        f"ZLASTMESSAGETEXT, ZLASTMESSAGEDATE, ZSESSIONTYPE "
        f"FROM ZWACHATSESSION "
        f"{where} "
        f"ORDER BY ZLASTMESSAGEDATE DESC "
        f"LIMIT ?"
    )

    db = _get_db()
    try:
        rows = db.execute(query, (limit,)).fetchall()
        return [_chat_row_to_dict(row) for row in rows]
    finally:
        db.close()


def search_chats(query: str) -> list[dict]:
    """Search chats by contact/group name (case-insensitive substring match).

    Args:
        query: Search string to match against partner names.

    Returns:
        List of matching chat dicts.
    """
    db = _get_db()
    try:
        rows = db.execute(
            "SELECT Z_PK, ZPARTNERNAME, ZCONTACTJID, ZUNREADCOUNT, "
            "ZLASTMESSAGETEXT, ZLASTMESSAGEDATE, ZSESSIONTYPE "
            "FROM ZWACHATSESSION "
            "WHERE ZPARTNERNAME LIKE ? "
            "ORDER BY ZLASTMESSAGEDATE DESC",
            (f"%{query}%",),
        ).fetchall()
        return [_chat_row_to_dict(row) for row in rows]
    finally:
        db.close()


def get_chat(jid_or_name: str) -> dict | None:
    """Get detailed information about a single chat.

    Args:
        jid_or_name: A JID string or contact/group name.

    Returns:
        Chat dict, or None if not found.
    """
    jid = _resolve_jid(jid_or_name)
    if jid is None:
        return None

    db = _get_db()
    try:
        row = db.execute(
            "SELECT Z_PK, ZPARTNERNAME, ZCONTACTJID, ZUNREADCOUNT, "
            "ZLASTMESSAGETEXT, ZLASTMESSAGEDATE, ZSESSIONTYPE "
            "FROM ZWACHATSESSION "
            "WHERE ZCONTACTJID = ?",
            (jid,),
        ).fetchone()
        if row is None:
            return None

        chat = _chat_row_to_dict(row)

        # Add message count
        msg_count = db.execute(
            "SELECT COUNT(*) as cnt FROM ZWAMESSAGE WHERE ZCHATSESSION = ?",
            (row["Z_PK"],),
        ).fetchone()
        chat["message_count"] = msg_count["cnt"] if msg_count else 0

        return chat
    finally:
        db.close()


def get_unread_chats() -> list[dict]:
    """List all chats that have unread messages.

    Returns:
        List of chat dicts where unread_count > 0, sorted by unread count descending.
    """
    db = _get_db()
    try:
        rows = db.execute(
            "SELECT Z_PK, ZPARTNERNAME, ZCONTACTJID, ZUNREADCOUNT, "
            "ZLASTMESSAGETEXT, ZLASTMESSAGEDATE, ZSESSIONTYPE "
            "FROM ZWACHATSESSION "
            "WHERE ZUNREADCOUNT > 0 "
            "ORDER BY ZUNREADCOUNT DESC",
        ).fetchall()
        return [_chat_row_to_dict(row) for row in rows]
    finally:
        db.close()


def get_chat_by_phone(phone: str) -> dict | None:
    """Find a chat by phone number.

    The phone number is cleaned to digits only and matched against JIDs
    (which are stored as digits followed by @s.whatsapp.net).

    Args:
        phone: Phone number with or without + prefix.

    Returns:
        Chat dict, or None if not found.
    """
    import re

    clean_phone = re.sub(r"[^\d]", "", phone)
    if not clean_phone:
        return None

    jid = f"{clean_phone}@s.whatsapp.net"
    return get_chat(jid)
