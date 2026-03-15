"""Group operations — read group info from WhatsApp SQLite database."""

from __future__ import annotations

from whatsapp_cli.core.chats import _resolve_jid
from whatsapp_cli.utils.wa_backend import _get_db, _apple_ts_to_datetime


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get_group_chat_pk(jid_or_name: str) -> int | None:
    """Resolve a group JID or name to its Z_PK in ZWACHATSESSION.

    Args:
        jid_or_name: Group JID (ending in @g.us) or group name.

    Returns:
        Z_PK or None if not found.
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


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def list_groups() -> list[dict]:
    """List all WhatsApp group chats.

    Returns:
        List of group dicts with keys: name, jid, unread_count, last_message,
        last_message_time, member_count.
    """
    db = _get_db()
    try:
        rows = db.execute(
            "SELECT cs.Z_PK, cs.ZPARTNERNAME, cs.ZCONTACTJID, "
            "cs.ZUNREADCOUNT, cs.ZLASTMESSAGETEXT, cs.ZLASTMESSAGEDATE "
            "FROM ZWACHATSESSION cs "
            "WHERE cs.ZSESSIONTYPE = 1 "
            "ORDER BY cs.ZLASTMESSAGEDATE DESC"
        ).fetchall()

        groups = []
        for row in rows:
            last_msg_time = _apple_ts_to_datetime(row["ZLASTMESSAGEDATE"])

            # Count members for this group
            member_count_row = db.execute(
                "SELECT COUNT(*) as cnt FROM ZWAGROUPMEMBER "
                "WHERE ZCHATSESSION = ?",
                (row["Z_PK"],),
            ).fetchone()
            member_count = member_count_row["cnt"] if member_count_row else 0

            groups.append({
                "name": row["ZPARTNERNAME"],
                "jid": row["ZCONTACTJID"],
                "unread_count": row["ZUNREADCOUNT"] or 0,
                "last_message": row["ZLASTMESSAGETEXT"],
                "last_message_time": last_msg_time.isoformat() if last_msg_time else None,
                "member_count": member_count,
            })

        return groups
    finally:
        db.close()


def get_group_info(jid_or_name: str) -> dict | None:
    """Get detailed information about a group.

    Args:
        jid_or_name: Group JID or name.

    Returns:
        Dict with keys: name, jid, owner, creator, member_count, members,
        unread_count, last_message, last_message_time, creation_date.
        Returns None if group not found.
    """
    jid = _resolve_jid(jid_or_name)
    if jid is None:
        return None

    db = _get_db()
    try:
        chat_row = db.execute(
            "SELECT Z_PK, ZPARTNERNAME, ZCONTACTJID, ZUNREADCOUNT, "
            "ZLASTMESSAGETEXT, ZLASTMESSAGEDATE "
            "FROM ZWACHATSESSION "
            "WHERE ZCONTACTJID = ? AND ZSESSIONTYPE = 1",
            (jid,),
        ).fetchone()

        if chat_row is None:
            return None

        chat_pk = chat_row["Z_PK"]
        last_msg_time = _apple_ts_to_datetime(chat_row["ZLASTMESSAGEDATE"])

        # Group info from ZWAGROUPINFO
        group_info_row = db.execute(
            "SELECT ZCREATORJID, ZOWNERJID, ZCREATIONDATE "
            "FROM ZWAGROUPINFO "
            "WHERE ZCHATSESSION = ?",
            (chat_pk,),
        ).fetchone()

        creator = None
        owner = None
        creation_date = None
        if group_info_row:
            creator = group_info_row["ZCREATORJID"]
            owner = group_info_row["ZOWNERJID"]
            creation_ts = group_info_row.get("ZCREATIONDATE")
            creation_date = (
                _apple_ts_to_datetime(creation_ts).isoformat()
                if creation_ts else None
            )

        # Members
        members = _fetch_group_members(db, chat_pk)

        return {
            "name": chat_row["ZPARTNERNAME"],
            "jid": chat_row["ZCONTACTJID"],
            "owner": owner,
            "creator": creator,
            "creation_date": creation_date,
            "member_count": len(members),
            "members": members,
            "unread_count": chat_row["ZUNREADCOUNT"] or 0,
            "last_message": chat_row["ZLASTMESSAGETEXT"],
            "last_message_time": last_msg_time.isoformat() if last_msg_time else None,
        }
    finally:
        db.close()


def get_group_members(jid_or_name: str) -> list[dict]:
    """List members of a group.

    Args:
        jid_or_name: Group JID or name.

    Returns:
        List of member dicts with keys: jid, is_admin.
        Returns empty list if group not found.
    """
    chat_pk = _get_group_chat_pk(jid_or_name)
    if chat_pk is None:
        return []

    db = _get_db()
    try:
        return _fetch_group_members(db, chat_pk)
    finally:
        db.close()


def search_groups(query: str) -> list[dict]:
    """Search groups by name (case-insensitive substring match).

    Args:
        query: Search string.

    Returns:
        List of matching group dicts.
    """
    db = _get_db()
    try:
        rows = db.execute(
            "SELECT cs.Z_PK, cs.ZPARTNERNAME, cs.ZCONTACTJID, "
            "cs.ZUNREADCOUNT, cs.ZLASTMESSAGETEXT, cs.ZLASTMESSAGEDATE "
            "FROM ZWACHATSESSION cs "
            "WHERE cs.ZSESSIONTYPE = 1 AND cs.ZPARTNERNAME LIKE ? "
            "ORDER BY cs.ZLASTMESSAGEDATE DESC",
            (f"%{query}%",),
        ).fetchall()

        groups = []
        for row in rows:
            last_msg_time = _apple_ts_to_datetime(row["ZLASTMESSAGEDATE"])
            member_count_row = db.execute(
                "SELECT COUNT(*) as cnt FROM ZWAGROUPMEMBER "
                "WHERE ZCHATSESSION = ?",
                (row["Z_PK"],),
            ).fetchone()
            member_count = member_count_row["cnt"] if member_count_row else 0

            groups.append({
                "name": row["ZPARTNERNAME"],
                "jid": row["ZCONTACTJID"],
                "unread_count": row["ZUNREADCOUNT"] or 0,
                "last_message": row["ZLASTMESSAGETEXT"],
                "last_message_time": last_msg_time.isoformat() if last_msg_time else None,
                "member_count": member_count,
            })

        return groups
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _fetch_group_members(db, chat_pk: int) -> list[dict]:
    """Fetch group members for a given chat session PK.

    Args:
        db: Open sqlite3 connection.
        chat_pk: Z_PK of the chat session.

    Returns:
        List of member dicts with keys: jid, is_admin.
    """
    rows = db.execute(
        "SELECT ZMEMBERJID, ZISADMIN FROM ZWAGROUPMEMBER "
        "WHERE ZCHATSESSION = ? "
        "ORDER BY ZMEMBERJID",
        (chat_pk,),
    ).fetchall()

    return [
        {
            "jid": row["ZMEMBERJID"],
            "is_admin": bool(row.get("ZISADMIN", 0)),
        }
        for row in rows
    ]
