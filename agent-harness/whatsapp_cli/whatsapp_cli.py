"""whatsapp-cli -- Click-based CLI for WhatsApp on macOS.

Main entry point for the CLI harness. Supports both one-shot commands
and an interactive REPL mode (default when no subcommand is given).

Reads from the WhatsApp SQLite database for queries and uses the
whatsapp:// URL scheme + System Events for sending messages.
"""

import json
import os
import shlex
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import click

from whatsapp_cli import __version__
from whatsapp_cli.utils.repl_skin import ReplSkin


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_APPLE_EPOCH_OFFSET = 978307200

_SKIN = ReplSkin("whatsapp", version=__version__)


# ---------------------------------------------------------------------------
# Timestamp helpers
# ---------------------------------------------------------------------------

def _apple_ts_to_datetime(ts: float | None) -> datetime | None:
    """Convert an Apple Core Data timestamp to a Python datetime."""
    if ts is None:
        return None
    return datetime.fromtimestamp(ts + _APPLE_EPOCH_OFFSET, tz=timezone.utc)


def _datetime_to_apple_ts(dt: datetime) -> float:
    """Convert a Python datetime to an Apple Core Data timestamp."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp() - _APPLE_EPOCH_OFFSET


def _format_timestamp(ts: float | None) -> str:
    """Format an Apple timestamp into a human-readable local string."""
    dt = _apple_ts_to_datetime(ts)
    if dt is None:
        return ""
    local_dt = dt.astimezone()
    now = datetime.now().astimezone()
    delta = now - local_dt

    if delta.days == 0:
        return local_dt.strftime("%H:%M")
    elif delta.days == 1:
        return "Yesterday " + local_dt.strftime("%H:%M")
    elif delta.days < 7:
        return local_dt.strftime("%a %H:%M")
    elif local_dt.year == now.year:
        return local_dt.strftime("%d %b %H:%M")
    else:
        return local_dt.strftime("%d %b %Y %H:%M")


def _truncate(text: str, max_len: int = 50) -> str:
    """Truncate text with ellipsis if it exceeds max_len."""
    if not text:
        return ""
    text = text.replace("\n", " ").replace("\r", "")
    if len(text) > max_len:
        return text[: max_len - 1] + "\u2026"
    return text


# ---------------------------------------------------------------------------
# SQLite access
# ---------------------------------------------------------------------------

_WA_CONTAINER = os.path.expanduser(
    "~/Library/Group Containers/group.net.whatsapp.WhatsApp.shared"
)
_DB_PATH = os.path.join(_WA_CONTAINER, "ChatStorage.sqlite")
_CONTACTS_DB_PATH = os.path.join(_WA_CONTAINER, "ContactsV2.sqlite")
_MEDIA_PATH = os.path.join(_WA_CONTAINER, "Message", "Media")


def _get_db(path: str | None = None):
    """Return a read-only SQLite connection to the WhatsApp database."""
    import sqlite3

    db_path = path or _DB_PATH
    if not os.path.isfile(db_path):
        raise FileNotFoundError(
            f"WhatsApp database not found at {db_path}. "
            "Is WhatsApp installed and has been opened at least once?"
        )
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _get_contacts_db():
    """Return a read-only SQLite connection to the contacts database."""
    return _get_db(_CONTACTS_DB_PATH)


# ---------------------------------------------------------------------------
# WhatsApp process helpers
# ---------------------------------------------------------------------------

def _is_whatsapp_running() -> bool:
    """Check whether WhatsApp is currently running."""
    try:
        result = subprocess.run(
            ["pgrep", "-f", "WhatsApp"],
            capture_output=True, text=True, timeout=5,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


def _ensure_whatsapp_running() -> None:
    """Launch WhatsApp if it is not already running."""
    if _is_whatsapp_running():
        return
    candidates = [
        "/Applications/WhatsApp.app",
        os.path.expanduser("~/Applications/WhatsApp.app"),
    ]
    app_path = None
    for p in candidates:
        if os.path.isdir(p):
            app_path = p
            break
    if app_path is None:
        raise RuntimeError("WhatsApp is not installed.")
    subprocess.run(["open", "-a", app_path], check=True, timeout=10)
    for _ in range(20):
        time.sleep(0.5)
        if _is_whatsapp_running():
            return
    raise RuntimeError("WhatsApp was launched but did not start within 10 seconds.")


# ---------------------------------------------------------------------------
# Sending messages
# ---------------------------------------------------------------------------

def _send_message(phone_or_jid: str, text: str) -> bool:
    """Send a message via URL scheme + System Events keystroke.

    Returns True if the send sequence completed.
    """
    from urllib.parse import quote
    import re

    phone = phone_or_jid
    if "@" in phone:
        phone = phone.split("@")[0]
    phone = re.sub(r"[^\d]", "", phone)
    if not phone:
        raise ValueError(f"Could not extract phone number from: {phone_or_jid}")

    _ensure_whatsapp_running()

    encoded = quote(text, safe="")
    url = f"whatsapp://send?phone={phone}&text={encoded}"
    subprocess.run(["open", url], check=True, timeout=10)
    time.sleep(2.0)

    # Activate and press Enter
    subprocess.run(
        ["osascript", "-e", 'tell application "WhatsApp" to activate'],
        capture_output=True, timeout=10,
    )
    time.sleep(0.5)
    subprocess.run(
        ["osascript", "-e",
         'tell application "System Events" to keystroke return'],
        capture_output=True, timeout=10,
    )
    return True


# ---------------------------------------------------------------------------
# Core query functions (chat, message, contact, group, monitor, export)
# ---------------------------------------------------------------------------

# -- Chats ----------------------------------------------------------------

def _list_chats(limit: int = 20, groups: bool = True,
                status: bool = False) -> list[dict]:
    """List recent chats ordered by last message date."""
    conn = _get_db()
    try:
        where_parts = []
        if not groups:
            where_parts.append("cs.ZCONTACTJID NOT LIKE '%@g.us'")
        if not status:
            where_parts.append("cs.ZCONTACTJID NOT LIKE '%@broadcast'")
            where_parts.append("cs.ZCONTACTJID != 'status@broadcast'")

        where_clause = ""
        if where_parts:
            where_clause = "WHERE " + " AND ".join(where_parts)

        query = f"""
            SELECT
                cs.ZCONTACTJID AS jid,
                cs.ZPARTNERNAME AS name,
                cs.ZLASTMESSAGEDATE AS last_ts,
                cs.ZMESSAGECOUNTER AS msg_count,
                (SELECT zm.ZTEXT FROM ZWAMESSAGE zm
                 WHERE zm.ZCHATSESSION = cs.Z_PK
                 ORDER BY zm.ZMESSAGEDATE DESC LIMIT 1) AS last_message,
                (SELECT COUNT(*) FROM ZWAMESSAGE zm2
                 WHERE zm2.ZCHATSESSION = cs.Z_PK
                   AND zm2.ZISFROMME = 0
                   AND zm2.ZMESSAGEDATE > COALESCE(cs.ZLASTMESSAGEDATE - 1, 0))
                AS unread_count
            FROM ZWACHATSESSION cs
            {where_clause}
            ORDER BY cs.ZLASTMESSAGEDATE DESC
            LIMIT ?
        """
        rows = conn.execute(query, (limit,)).fetchall()
        return [
            {
                "jid": r["jid"] or "",
                "name": r["name"] or r["jid"] or "",
                "last_timestamp": r["last_ts"],
                "last_message": r["last_message"] or "",
                "message_count": r["msg_count"] or 0,
                "unread_count": r["unread_count"] or 0,
            }
            for r in rows
        ]
    finally:
        conn.close()


def _search_chats(query: str) -> list[dict]:
    """Search chats by name."""
    conn = _get_db()
    try:
        rows = conn.execute(
            """SELECT cs.ZCONTACTJID AS jid, cs.ZPARTNERNAME AS name,
                      cs.ZLASTMESSAGEDATE AS last_ts, cs.ZMESSAGECOUNTER AS msg_count
               FROM ZWACHATSESSION cs
               WHERE cs.ZPARTNERNAME LIKE ? OR cs.ZCONTACTJID LIKE ?
               ORDER BY cs.ZLASTMESSAGEDATE DESC""",
            (f"%{query}%", f"%{query}%"),
        ).fetchall()
        return [
            {
                "jid": r["jid"] or "",
                "name": r["name"] or r["jid"] or "",
                "last_timestamp": r["last_ts"],
                "message_count": r["msg_count"] or 0,
            }
            for r in rows
        ]
    finally:
        conn.close()


def _get_unread_chats() -> list[dict]:
    """Get chats with unread messages."""
    conn = _get_db()
    try:
        rows = conn.execute(
            """SELECT cs.ZCONTACTJID AS jid, cs.ZPARTNERNAME AS name,
                      cs.ZLASTMESSAGEDATE AS last_ts,
                      (SELECT COUNT(*) FROM ZWAMESSAGE zm
                       WHERE zm.ZCHATSESSION = cs.Z_PK
                         AND zm.ZISFROMME = 0
                         AND zm.ZMESSAGEDATE > COALESCE(cs.ZLASTMESSAGEDATE - 1, 0))
                      AS unread_count,
                      (SELECT zm2.ZTEXT FROM ZWAMESSAGE zm2
                       WHERE zm2.ZCHATSESSION = cs.Z_PK
                       ORDER BY zm2.ZMESSAGEDATE DESC LIMIT 1) AS last_message
               FROM ZWACHATSESSION cs
               HAVING unread_count > 0
               ORDER BY cs.ZLASTMESSAGEDATE DESC"""
        ).fetchall()
        return [
            {
                "jid": r["jid"] or "",
                "name": r["name"] or r["jid"] or "",
                "last_timestamp": r["last_ts"],
                "unread_count": r["unread_count"],
                "last_message": r["last_message"] or "",
            }
            for r in rows
        ]
    finally:
        conn.close()


def _get_chat_details(name_or_jid: str) -> dict | None:
    """Get detailed info about a single chat."""
    conn = _get_db()
    try:
        row = conn.execute(
            """SELECT cs.Z_PK AS pk, cs.ZCONTACTJID AS jid,
                      cs.ZPARTNERNAME AS name, cs.ZLASTMESSAGEDATE AS last_ts,
                      cs.ZMESSAGECOUNTER AS msg_count,
                      cs.ZUNREADCOUNT AS unread
               FROM ZWACHATSESSION cs
               WHERE cs.ZPARTNERNAME LIKE ? OR cs.ZCONTACTJID = ?
               LIMIT 1""",
            (f"%{name_or_jid}%", name_or_jid),
        ).fetchone()
        if row is None:
            return None
        return {
            "pk": row["pk"],
            "jid": row["jid"] or "",
            "name": row["name"] or row["jid"] or "",
            "last_timestamp": row["last_ts"],
            "message_count": row["msg_count"] or 0,
            "unread": row["unread"] or 0,
        }
    finally:
        conn.close()


def _find_chat_by_phone(phone: str) -> dict | None:
    """Find a chat by phone number."""
    import re
    clean = re.sub(r"[^\d]", "", phone)
    conn = _get_db()
    try:
        row = conn.execute(
            """SELECT cs.Z_PK AS pk, cs.ZCONTACTJID AS jid,
                      cs.ZPARTNERNAME AS name, cs.ZLASTMESSAGEDATE AS last_ts,
                      cs.ZMESSAGECOUNTER AS msg_count
               FROM ZWACHATSESSION cs
               WHERE cs.ZCONTACTJID LIKE ?
               LIMIT 1""",
            (f"%{clean}%",),
        ).fetchone()
        if row is None:
            return None
        return {
            "pk": row["pk"],
            "jid": row["jid"] or "",
            "name": row["name"] or row["jid"] or "",
            "last_timestamp": row["last_ts"],
            "message_count": row["msg_count"] or 0,
        }
    finally:
        conn.close()


def _resolve_chat_pk(name_or_jid: str) -> int | None:
    """Resolve a chat name or JID to its primary key."""
    conn = _get_db()
    try:
        row = conn.execute(
            """SELECT cs.Z_PK AS pk FROM ZWACHATSESSION cs
               WHERE cs.ZPARTNERNAME LIKE ? OR cs.ZCONTACTJID = ?
               LIMIT 1""",
            (f"%{name_or_jid}%", name_or_jid),
        ).fetchone()
        return row["pk"] if row else None
    finally:
        conn.close()


def _resolve_jid(name_or_jid: str) -> str | None:
    """Resolve a chat name to its JID."""
    conn = _get_db()
    try:
        row = conn.execute(
            """SELECT cs.ZCONTACTJID AS jid FROM ZWACHATSESSION cs
               WHERE cs.ZPARTNERNAME LIKE ? OR cs.ZCONTACTJID = ?
               LIMIT 1""",
            (f"%{name_or_jid}%", name_or_jid),
        ).fetchone()
        return row["jid"] if row else None
    finally:
        conn.close()


# -- Messages -------------------------------------------------------------

def _get_messages(name_or_jid: str, limit: int = 30,
                  before: str | None = None,
                  after: str | None = None) -> list[dict]:
    """Get messages from a chat."""
    chat_pk = _resolve_chat_pk(name_or_jid)
    if chat_pk is None:
        return []

    conn = _get_db()
    try:
        where_parts = ["zm.ZCHATSESSION = ?"]
        params: list = [chat_pk]

        if before:
            dt = datetime.fromisoformat(before).replace(tzinfo=timezone.utc)
            where_parts.append("zm.ZMESSAGEDATE < ?")
            params.append(_datetime_to_apple_ts(dt))
        if after:
            dt = datetime.fromisoformat(after).replace(tzinfo=timezone.utc)
            where_parts.append("zm.ZMESSAGEDATE > ?")
            params.append(_datetime_to_apple_ts(dt))

        params.append(limit)

        rows = conn.execute(
            f"""SELECT zm.ZTEXT AS text, zm.ZMESSAGEDATE AS ts,
                       zm.ZISFROMME AS from_me, zm.ZMESSAGETYPE AS msg_type,
                       zm.ZFROMJID AS from_jid, zm.ZTOJID AS to_jid,
                       zm.ZSTANZAID AS stanza_id,
                       gm.ZCONTACTNAME AS sender_name
                FROM ZWAMESSAGE zm
                LEFT JOIN ZWAGROUPMEMBER gm ON zm.ZGROUPMEMBER = gm.Z_PK
                WHERE {' AND '.join(where_parts)}
                ORDER BY zm.ZMESSAGEDATE DESC
                LIMIT ?""",
            params,
        ).fetchall()

        messages = []
        for r in rows:
            sender = "Me" if r["from_me"] else (r["sender_name"] or r["from_jid"] or "Unknown")
            messages.append({
                "text": r["text"] or "",
                "timestamp": r["ts"],
                "formatted_time": _format_timestamp(r["ts"]),
                "from_me": bool(r["from_me"]),
                "sender": sender,
                "message_type": r["msg_type"],
                "stanza_id": r["stanza_id"] or "",
            })

        messages.reverse()
        return messages
    finally:
        conn.close()


def _search_messages(query: str, chat_name: str | None = None,
                     limit: int = 30) -> list[dict]:
    """Search messages globally or within a specific chat."""
    conn = _get_db()
    try:
        where_parts = ["zm.ZTEXT LIKE ?"]
        params: list = [f"%{query}%"]

        if chat_name:
            chat_pk = _resolve_chat_pk(chat_name)
            if chat_pk is None:
                return []
            where_parts.append("zm.ZCHATSESSION = ?")
            params.append(chat_pk)

        params.append(limit)

        rows = conn.execute(
            f"""SELECT zm.ZTEXT AS text, zm.ZMESSAGEDATE AS ts,
                       zm.ZISFROMME AS from_me, zm.ZFROMJID AS from_jid,
                       cs.ZPARTNERNAME AS chat_name, cs.ZCONTACTJID AS chat_jid,
                       gm.ZCONTACTNAME AS sender_name
                FROM ZWAMESSAGE zm
                JOIN ZWACHATSESSION cs ON zm.ZCHATSESSION = cs.Z_PK
                LEFT JOIN ZWAGROUPMEMBER gm ON zm.ZGROUPMEMBER = gm.Z_PK
                WHERE {' AND '.join(where_parts)}
                ORDER BY zm.ZMESSAGEDATE DESC
                LIMIT ?""",
            params,
        ).fetchall()

        return [
            {
                "text": r["text"] or "",
                "timestamp": r["ts"],
                "formatted_time": _format_timestamp(r["ts"]),
                "from_me": bool(r["from_me"]),
                "sender": "Me" if r["from_me"] else (r["sender_name"] or r["from_jid"] or "Unknown"),
                "chat_name": r["chat_name"] or r["chat_jid"] or "",
            }
            for r in rows
        ]
    finally:
        conn.close()


def _get_starred_messages(chat_name: str | None = None) -> list[dict]:
    """Get starred/flagged messages."""
    conn = _get_db()
    try:
        where_parts = ["zm.ZSTARRED = 1"]
        params: list = []

        if chat_name:
            chat_pk = _resolve_chat_pk(chat_name)
            if chat_pk is None:
                return []
            where_parts.append("zm.ZCHATSESSION = ?")
            params.append(chat_pk)

        rows = conn.execute(
            f"""SELECT zm.ZTEXT AS text, zm.ZMESSAGEDATE AS ts,
                       zm.ZISFROMME AS from_me, zm.ZFROMJID AS from_jid,
                       cs.ZPARTNERNAME AS chat_name,
                       gm.ZCONTACTNAME AS sender_name
                FROM ZWAMESSAGE zm
                JOIN ZWACHATSESSION cs ON zm.ZCHATSESSION = cs.Z_PK
                LEFT JOIN ZWAGROUPMEMBER gm ON zm.ZGROUPMEMBER = gm.Z_PK
                WHERE {' AND '.join(where_parts)}
                ORDER BY zm.ZMESSAGEDATE DESC""",
            params,
        ).fetchall()

        return [
            {
                "text": r["text"] or "",
                "timestamp": r["ts"],
                "formatted_time": _format_timestamp(r["ts"]),
                "from_me": bool(r["from_me"]),
                "sender": "Me" if r["from_me"] else (r["sender_name"] or r["from_jid"] or "Unknown"),
                "chat_name": r["chat_name"] or "",
            }
            for r in rows
        ]
    finally:
        conn.close()


def _get_media_messages(name_or_jid: str, limit: int = 20) -> list[dict]:
    """Get media messages from a chat."""
    chat_pk = _resolve_chat_pk(name_or_jid)
    if chat_pk is None:
        return []

    conn = _get_db()
    try:
        rows = conn.execute(
            """SELECT zm.ZTEXT AS text, zm.ZMESSAGEDATE AS ts,
                      zm.ZISFROMME AS from_me, zm.ZMESSAGETYPE AS msg_type,
                      zm.ZMEDIALOCALPATH AS media_path,
                      mm.ZVCARDSTRING AS media_info,
                      gm.ZCONTACTNAME AS sender_name
               FROM ZWAMESSAGE zm
               LEFT JOIN ZWAMEDIAITEM mm ON mm.ZMESSAGE = zm.Z_PK
               LEFT JOIN ZWAGROUPMEMBER gm ON zm.ZGROUPMEMBER = gm.Z_PK
               WHERE zm.ZCHATSESSION = ?
                 AND zm.ZMESSAGETYPE != 0
                 AND mm.Z_PK IS NOT NULL
               ORDER BY zm.ZMESSAGEDATE DESC
               LIMIT ?""",
            (chat_pk, limit),
        ).fetchall()

        return [
            {
                "text": r["text"] or "(media)",
                "timestamp": r["ts"],
                "formatted_time": _format_timestamp(r["ts"]),
                "from_me": bool(r["from_me"]),
                "sender": "Me" if r["from_me"] else (r["sender_name"] or "Unknown"),
                "message_type": r["msg_type"],
                "media_path": r["media_path"] or "",
            }
            for r in rows
        ]
    finally:
        conn.close()


def _count_messages(chat_name: str | None = None) -> dict:
    """Count messages, optionally filtered by chat."""
    conn = _get_db()
    try:
        if chat_name:
            chat_pk = _resolve_chat_pk(chat_name)
            if chat_pk is None:
                return {"total": 0, "sent": 0, "received": 0}
            row = conn.execute(
                """SELECT COUNT(*) AS total,
                          SUM(CASE WHEN ZISFROMME = 1 THEN 1 ELSE 0 END) AS sent,
                          SUM(CASE WHEN ZISFROMME = 0 THEN 1 ELSE 0 END) AS received
                   FROM ZWAMESSAGE WHERE ZCHATSESSION = ?""",
                (chat_pk,),
            ).fetchone()
        else:
            row = conn.execute(
                """SELECT COUNT(*) AS total,
                          SUM(CASE WHEN ZISFROMME = 1 THEN 1 ELSE 0 END) AS sent,
                          SUM(CASE WHEN ZISFROMME = 0 THEN 1 ELSE 0 END) AS received
                   FROM ZWAMESSAGE"""
            ).fetchone()
        return {
            "total": row["total"] or 0,
            "sent": row["sent"] or 0,
            "received": row["received"] or 0,
        }
    finally:
        conn.close()


# -- Contacts -------------------------------------------------------------

def _list_contacts() -> list[dict]:
    """List all contacts from the WhatsApp contacts database."""
    try:
        conn = _get_contacts_db()
    except FileNotFoundError:
        # Fall back to chat session partners
        return _list_contacts_from_chats()

    try:
        rows = conn.execute(
            """SELECT ZWHATSAPPID AS jid, ZFULLNAME AS name,
                      ZPHONENUMBER AS phone, ZFAVORITE AS favorite
               FROM ZWACONTACT
               WHERE ZFULLNAME IS NOT NULL AND ZFULLNAME != ''
               ORDER BY ZFULLNAME"""
        ).fetchall()
        return [
            {
                "jid": r["jid"] or "",
                "name": r["name"] or "",
                "phone": r["phone"] or "",
                "favorite": bool(r["favorite"]) if r["favorite"] else False,
            }
            for r in rows
        ]
    except Exception:
        return _list_contacts_from_chats()
    finally:
        conn.close()


def _list_contacts_from_chats() -> list[dict]:
    """List contacts derived from chat sessions (fallback)."""
    conn = _get_db()
    try:
        rows = conn.execute(
            """SELECT ZCONTACTJID AS jid, ZPARTNERNAME AS name
               FROM ZWACHATSESSION
               WHERE ZCONTACTJID NOT LIKE '%@g.us'
                 AND ZCONTACTJID != 'status@broadcast'
                 AND ZPARTNERNAME IS NOT NULL
               ORDER BY ZPARTNERNAME"""
        ).fetchall()
        return [
            {
                "jid": r["jid"] or "",
                "name": r["name"] or r["jid"] or "",
                "phone": (r["jid"] or "").split("@")[0] if r["jid"] else "",
                "favorite": False,
            }
            for r in rows
        ]
    finally:
        conn.close()


def _search_contacts(query: str) -> list[dict]:
    """Search contacts by name or phone."""
    all_contacts = _list_contacts()
    q = query.lower()
    return [
        c for c in all_contacts
        if q in c["name"].lower() or q in c.get("phone", "") or q in c.get("jid", "")
    ]


def _get_contact_info(name_or_jid: str) -> dict | None:
    """Get detailed contact info."""
    contacts = _list_contacts()
    q = name_or_jid.lower()
    for c in contacts:
        if q in c["name"].lower() or c["jid"] == name_or_jid:
            chat = _get_chat_details(c["jid"]) or {}
            c["message_count"] = chat.get("message_count", 0)
            c["last_timestamp"] = chat.get("last_timestamp")
            return c
    return None


def _resolve_name_to_jid(name: str) -> str | None:
    """Resolve a contact name to a JID."""
    return _resolve_jid(name)


# -- Groups ---------------------------------------------------------------

def _list_groups() -> list[dict]:
    """List all WhatsApp groups."""
    conn = _get_db()
    try:
        rows = conn.execute(
            """SELECT cs.ZCONTACTJID AS jid, cs.ZPARTNERNAME AS name,
                      cs.ZLASTMESSAGEDATE AS last_ts,
                      cs.ZMESSAGECOUNTER AS msg_count
               FROM ZWACHATSESSION cs
               WHERE cs.ZCONTACTJID LIKE '%@g.us'
               ORDER BY cs.ZLASTMESSAGEDATE DESC"""
        ).fetchall()
        return [
            {
                "jid": r["jid"] or "",
                "name": r["name"] or r["jid"] or "",
                "last_timestamp": r["last_ts"],
                "message_count": r["msg_count"] or 0,
            }
            for r in rows
        ]
    finally:
        conn.close()


def _get_group_info(name_or_jid: str) -> dict | None:
    """Get group info."""
    chat = _get_chat_details(name_or_jid)
    if chat is None:
        return None
    if not chat["jid"].endswith("@g.us"):
        return None

    members = _get_group_members(name_or_jid)
    chat["member_count"] = len(members)
    chat["members"] = members
    return chat


def _get_group_members(name_or_jid: str) -> list[dict]:
    """Get members of a group."""
    chat_pk = _resolve_chat_pk(name_or_jid)
    if chat_pk is None:
        return []

    conn = _get_db()
    try:
        rows = conn.execute(
            """SELECT gm.ZMEMBERJID AS jid, gm.ZCONTACTNAME AS name,
                      gm.ZISADMIN AS is_admin
               FROM ZWAGROUPMEMBER gm
               WHERE gm.ZCHATSESSION = ?
               ORDER BY gm.ZCONTACTNAME""",
            (chat_pk,),
        ).fetchall()
        return [
            {
                "jid": r["jid"] or "",
                "name": r["name"] or r["jid"] or "",
                "is_admin": bool(r["is_admin"]) if r["is_admin"] else False,
            }
            for r in rows
        ]
    finally:
        conn.close()


def _search_groups(query: str) -> list[dict]:
    """Search groups by name."""
    groups = _list_groups()
    q = query.lower()
    return [g for g in groups if q in g["name"].lower()]


# -- Monitor --------------------------------------------------------------

def _get_messages_since(timestamp: float,
                        chat_name: str | None = None) -> list[dict]:
    """Get messages since a given Apple timestamp."""
    conn = _get_db()
    try:
        where_parts = ["zm.ZMESSAGEDATE > ?"]
        params: list = [timestamp]

        if chat_name:
            chat_pk = _resolve_chat_pk(chat_name)
            if chat_pk is None:
                return []
            where_parts.append("zm.ZCHATSESSION = ?")
            params.append(chat_pk)

        rows = conn.execute(
            f"""SELECT zm.ZTEXT AS text, zm.ZMESSAGEDATE AS ts,
                       zm.ZISFROMME AS from_me, zm.ZFROMJID AS from_jid,
                       cs.ZPARTNERNAME AS chat_name, cs.ZCONTACTJID AS chat_jid,
                       gm.ZCONTACTNAME AS sender_name
                FROM ZWAMESSAGE zm
                JOIN ZWACHATSESSION cs ON zm.ZCHATSESSION = cs.Z_PK
                LEFT JOIN ZWAGROUPMEMBER gm ON zm.ZGROUPMEMBER = gm.Z_PK
                WHERE {' AND '.join(where_parts)}
                ORDER BY zm.ZMESSAGEDATE ASC""",
            params,
        ).fetchall()

        return [
            {
                "text": r["text"] or "",
                "timestamp": r["ts"],
                "formatted_time": _format_timestamp(r["ts"]),
                "from_me": bool(r["from_me"]),
                "sender": "Me" if r["from_me"] else (r["sender_name"] or r["from_jid"] or "Unknown"),
                "chat_name": r["chat_name"] or r["chat_jid"] or "",
            }
            for r in rows
        ]
    finally:
        conn.close()


# -- Export ---------------------------------------------------------------

def _export_chat(name_or_jid: str, output_path: str,
                 fmt: str = "txt") -> dict:
    """Export chat history to a file."""
    # Get all messages (no limit)
    chat_pk = _resolve_chat_pk(name_or_jid)
    if chat_pk is None:
        return {"error": f"Chat not found: {name_or_jid}"}

    chat_info = _get_chat_details(name_or_jid)
    chat_name = chat_info["name"] if chat_info else name_or_jid

    conn = _get_db()
    try:
        rows = conn.execute(
            """SELECT zm.ZTEXT AS text, zm.ZMESSAGEDATE AS ts,
                      zm.ZISFROMME AS from_me, zm.ZFROMJID AS from_jid,
                      gm.ZCONTACTNAME AS sender_name
               FROM ZWAMESSAGE zm
               LEFT JOIN ZWAGROUPMEMBER gm ON zm.ZGROUPMEMBER = gm.Z_PK
               WHERE zm.ZCHATSESSION = ?
               ORDER BY zm.ZMESSAGEDATE ASC""",
            (chat_pk,),
        ).fetchall()

        messages = []
        for r in rows:
            sender = "Me" if r["from_me"] else (r["sender_name"] or r["from_jid"] or "Unknown")
            dt = _apple_ts_to_datetime(r["ts"])
            ts_str = dt.astimezone().strftime("%Y-%m-%d %H:%M:%S") if dt else ""
            messages.append({
                "timestamp": ts_str,
                "sender": sender,
                "text": r["text"] or "(media/attachment)",
                "from_me": bool(r["from_me"]),
            })
    finally:
        conn.close()

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    if fmt == "json":
        with open(out, "w", encoding="utf-8") as f:
            json.dump({"chat": chat_name, "messages": messages}, f,
                      indent=2, ensure_ascii=False)
    elif fmt == "csv":
        import csv
        with open(out, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=["timestamp", "sender", "text", "from_me"])
            writer.writeheader()
            writer.writerows(messages)
    else:  # txt
        with open(out, "w", encoding="utf-8") as f:
            f.write(f"Chat export: {chat_name}\n")
            f.write(f"Messages: {len(messages)}\n")
            f.write("-" * 60 + "\n\n")
            for m in messages:
                f.write(f"[{m['timestamp']}] {m['sender']}: {m['text']}\n")

    return {
        "chat": chat_name,
        "messages_exported": len(messages),
        "output_path": str(out.resolve()),
        "format": fmt,
    }


def _export_media(name_or_jid: str, output_dir: str) -> dict:
    """Export media files from a chat."""
    import shutil

    chat_pk = _resolve_chat_pk(name_or_jid)
    if chat_pk is None:
        return {"error": f"Chat not found: {name_or_jid}"}

    conn = _get_db()
    try:
        rows = conn.execute(
            """SELECT zm.ZMEDIALOCALPATH AS path, zm.ZMESSAGEDATE AS ts,
                      mm.ZVCARDSTRING AS info, zm.ZMESSAGETYPE AS msg_type
               FROM ZWAMESSAGE zm
               LEFT JOIN ZWAMEDIAITEM mm ON mm.ZMESSAGE = zm.Z_PK
               WHERE zm.ZCHATSESSION = ?
                 AND zm.ZMEDIALOCALPATH IS NOT NULL
                 AND zm.ZMEDIALOCALPATH != ''
               ORDER BY zm.ZMESSAGEDATE ASC""",
            (chat_pk,),
        ).fetchall()
    finally:
        conn.close()

    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    exported = 0
    for r in rows:
        media_path = r["path"]
        if not media_path:
            continue

        # Try to find the file in the WhatsApp media directory
        candidates = [
            Path(media_path),
            Path(_MEDIA_PATH) / media_path,
            Path(_WA_CONTAINER) / media_path,
        ]
        src = None
        for c in candidates:
            if c.is_file():
                src = c
                break

        if src is None:
            continue

        dest = out_dir / src.name
        if dest.exists():
            stem = dest.stem
            suffix = dest.suffix
            counter = 1
            while dest.exists():
                dest = out_dir / f"{stem}_{counter}{suffix}"
                counter += 1

        shutil.copy2(str(src), str(dest))
        exported += 1

    return {
        "total_media_found": len(rows),
        "exported": exported,
        "output_dir": str(out_dir.resolve()),
    }


# ---------------------------------------------------------------------------
# Session state
# ---------------------------------------------------------------------------

class SessionState:
    """Holds runtime state shared across CLI invocations."""

    def __init__(self):
        self.json_mode: bool = False

    @property
    def display_context(self) -> str:
        """Context string for the REPL prompt."""
        try:
            unreads = _get_unread_chats()
            count = sum(u["unread_count"] for u in unreads)
            if count > 0:
                return f"{count} unread"
        except Exception:
            pass
        return ""


pass_state = click.make_pass_decorator(SessionState, ensure=True)


# ---------------------------------------------------------------------------
# Output helper
# ---------------------------------------------------------------------------

def _output(ctx: click.Context, data: dict):
    """Emit output: JSON when --json is active, otherwise human-readable."""
    state: SessionState = ctx.ensure_object(SessionState)
    if state.json_mode:
        click.echo(json.dumps(data, indent=2, ensure_ascii=False))
    else:
        if "error" in data:
            _SKIN.error(data["error"])
        elif "message" in data:
            _SKIN.success(data["message"])
        elif "rows" in data and "headers" in data:
            _SKIN.table(data["headers"], data["rows"])
        else:
            for key, value in data.items():
                _SKIN.status(key, str(value))


def _safe_run(func, *args, **kwargs):
    """Run a function and return (result, None) or (None, error_string)."""
    try:
        result = func(*args, **kwargs)
        return result, None
    except Exception as exc:
        return None, str(exc)


# ---------------------------------------------------------------------------
# Main group
# ---------------------------------------------------------------------------

@click.group(invoke_without_command=True)
@click.option("--json", "json_mode", is_flag=True, default=False,
              help="Output results as JSON.")
@click.version_option(version=__version__, prog_name="whatsapp-cli")
@click.pass_context
def cli(ctx, json_mode):
    """whatsapp-cli -- Agent-operable CLI for WhatsApp on macOS."""
    state = ctx.ensure_object(SessionState)
    state.json_mode = json_mode

    if ctx.invoked_subcommand is None:
        _run_repl(ctx)


# ===========================================================================
# chat group
# ===========================================================================

@cli.group()
@click.pass_context
def chat(ctx):
    """Chat operations."""
    pass


@chat.command("list")
@click.option("--limit", default=20, help="Number of chats to show.")
@click.option("--groups/--no-groups", default=True, help="Include group chats.")
@click.option("--status/--no-status", default=False, help="Include status broadcasts.")
@click.pass_context
def chat_list(ctx, limit, groups, status):
    """List recent chats."""
    chats, err = _safe_run(_list_chats, limit=limit, groups=groups, status=status)
    if err:
        _output(ctx, {"error": f"Failed to list chats: {err}"})
        return

    state = ctx.ensure_object(SessionState)
    if state.json_mode:
        _output(ctx, {"chats": chats, "count": len(chats)})
        return

    headers = ["Name", "Last Message", "Time", "Msgs", "Unread"]
    rows = [
        [
            _truncate(c["name"], 25),
            _truncate(c["last_message"], 35),
            _format_timestamp(c["last_timestamp"]),
            str(c["message_count"]),
            str(c["unread_count"]) if c["unread_count"] > 0 else "",
        ]
        for c in chats
    ]
    _output(ctx, {"headers": headers, "rows": rows})


@chat.command("search")
@click.argument("query")
@click.pass_context
def chat_search(ctx, query):
    """Search chats by name."""
    chats, err = _safe_run(_search_chats, query)
    if err:
        _output(ctx, {"error": f"Search failed: {err}"})
        return

    state = ctx.ensure_object(SessionState)
    if state.json_mode:
        _output(ctx, {"results": chats, "count": len(chats), "query": query})
        return

    if not chats:
        _SKIN.info(f"No chats found for '{query}'")
        return

    headers = ["Name", "JID", "Time", "Messages"]
    rows = [
        [
            _truncate(c["name"], 25),
            _truncate(c["jid"], 30),
            _format_timestamp(c["last_timestamp"]),
            str(c["message_count"]),
        ]
        for c in chats
    ]
    _output(ctx, {"headers": headers, "rows": rows})


@chat.command("unread")
@click.pass_context
def chat_unread(ctx):
    """Show chats with unread messages."""
    unreads, err = _safe_run(_get_unread_chats)
    if err:
        _output(ctx, {"error": f"Failed to get unread chats: {err}"})
        return

    state = ctx.ensure_object(SessionState)
    if state.json_mode:
        _output(ctx, {"unread_chats": unreads, "count": len(unreads)})
        return

    if not unreads:
        _SKIN.success("No unread messages")
        return

    headers = ["Name", "Unread", "Last Message", "Time"]
    rows = [
        [
            _truncate(u["name"], 25),
            str(u["unread_count"]),
            _truncate(u["last_message"], 35),
            _format_timestamp(u["last_timestamp"]),
        ]
        for u in unreads
    ]
    _output(ctx, {"headers": headers, "rows": rows})


@chat.command("get")
@click.argument("name_or_jid")
@click.pass_context
def chat_get(ctx, name_or_jid):
    """Get detailed info about a chat."""
    info, err = _safe_run(_get_chat_details, name_or_jid)
    if err:
        _output(ctx, {"error": f"Failed to get chat: {err}"})
        return
    if info is None:
        _output(ctx, {"error": f"Chat not found: {name_or_jid}"})
        return

    state = ctx.ensure_object(SessionState)
    if state.json_mode:
        _output(ctx, info)
        return

    _SKIN.status_block({
        "Name": info["name"],
        "JID": info["jid"],
        "Messages": str(info["message_count"]),
        "Last Activity": _format_timestamp(info["last_timestamp"]),
    }, title=info["name"])


@chat.command("find")
@click.argument("phone")
@click.pass_context
def chat_find(ctx, phone):
    """Find a chat by phone number."""
    info, err = _safe_run(_find_chat_by_phone, phone)
    if err:
        _output(ctx, {"error": f"Search failed: {err}"})
        return
    if info is None:
        _output(ctx, {"error": f"No chat found for phone: {phone}"})
        return

    state = ctx.ensure_object(SessionState)
    if state.json_mode:
        _output(ctx, info)
        return

    _SKIN.status_block({
        "Name": info["name"],
        "JID": info["jid"],
        "Messages": str(info["message_count"]),
        "Last Activity": _format_timestamp(info["last_timestamp"]),
    }, title=info["name"])


# ===========================================================================
# message group
# ===========================================================================

@cli.group()
@click.pass_context
def message(ctx):
    """Message operations."""
    pass


@message.command("get")
@click.argument("name_or_jid")
@click.option("--limit", default=30, help="Number of messages to retrieve.")
@click.option("--before", default=None, help="Get messages before this date (ISO format).")
@click.option("--after", default=None, help="Get messages after this date (ISO format).")
@click.pass_context
def message_get(ctx, name_or_jid, limit, before, after):
    """Get messages from a chat."""
    messages, err = _safe_run(_get_messages, name_or_jid, limit=limit,
                              before=before, after=after)
    if err:
        _output(ctx, {"error": f"Failed to get messages: {err}"})
        return

    state = ctx.ensure_object(SessionState)
    if state.json_mode:
        _output(ctx, {"messages": messages, "count": len(messages),
                       "chat": name_or_jid})
        return

    if not messages:
        _SKIN.info(f"No messages found in '{name_or_jid}'")
        return

    _SKIN.section(f"Messages: {name_or_jid}")
    for m in messages:
        sender_prefix = f"  {'>>>' if m['from_me'] else '<<<'}"
        sender = m["sender"]
        ts = m["formatted_time"]
        text = m["text"] or "(media/attachment)"

        if m["from_me"]:
            click.echo(f"  {_SKIN._c('\033[38;5;75m', f'[{ts}]')} "
                       f"{_SKIN._c('\033[38;5;75m\033[1m', sender)}: {text}")
        else:
            click.echo(f"  {_SKIN._c('\033[38;5;245m', f'[{ts}]')} "
                       f"{_SKIN._c('\033[38;5;40m\033[1m', sender)}: {text}")


@message.command("search")
@click.argument("query")
@click.option("--chat", "chat_name", default=None,
              help="Search within a specific chat.")
@click.pass_context
def message_search(ctx, query, chat_name):
    """Search messages across chats."""
    results, err = _safe_run(_search_messages, query, chat_name=chat_name)
    if err:
        _output(ctx, {"error": f"Search failed: {err}"})
        return

    state = ctx.ensure_object(SessionState)
    if state.json_mode:
        _output(ctx, {"results": results, "count": len(results), "query": query})
        return

    if not results:
        _SKIN.info(f"No messages found matching '{query}'")
        return

    headers = ["Chat", "Sender", "Message", "Time"]
    rows = [
        [
            _truncate(r["chat_name"], 20),
            _truncate(r["sender"], 15),
            _truncate(r["text"], 35),
            r["formatted_time"],
        ]
        for r in results
    ]
    _output(ctx, {"headers": headers, "rows": rows})


@message.command("starred")
@click.option("--chat", "chat_name", default=None,
              help="Filter starred messages by chat.")
@click.pass_context
def message_starred(ctx, chat_name):
    """Get starred/flagged messages."""
    results, err = _safe_run(_get_starred_messages, chat_name=chat_name)
    if err:
        _output(ctx, {"error": f"Failed to get starred messages: {err}"})
        return

    state = ctx.ensure_object(SessionState)
    if state.json_mode:
        _output(ctx, {"starred": results, "count": len(results)})
        return

    if not results:
        _SKIN.info("No starred messages found")
        return

    headers = ["Chat", "Sender", "Message", "Time"]
    rows = [
        [
            _truncate(r["chat_name"], 20),
            _truncate(r["sender"], 15),
            _truncate(r["text"], 35),
            r["formatted_time"],
        ]
        for r in results
    ]
    _output(ctx, {"headers": headers, "rows": rows})


@message.command("media")
@click.argument("name_or_jid")
@click.option("--limit", default=20, help="Number of media messages to show.")
@click.pass_context
def message_media(ctx, name_or_jid, limit):
    """Get media messages from a chat."""
    results, err = _safe_run(_get_media_messages, name_or_jid, limit=limit)
    if err:
        _output(ctx, {"error": f"Failed to get media messages: {err}"})
        return

    state = ctx.ensure_object(SessionState)
    if state.json_mode:
        _output(ctx, {"media": results, "count": len(results)})
        return

    if not results:
        _SKIN.info(f"No media messages found in '{name_or_jid}'")
        return

    headers = ["Sender", "Type", "Caption", "Time", "Path"]
    rows = [
        [
            _truncate(r["sender"], 15),
            str(r["message_type"]),
            _truncate(r["text"], 30),
            r["formatted_time"],
            _truncate(r["media_path"], 25),
        ]
        for r in results
    ]
    _output(ctx, {"headers": headers, "rows": rows})


@message.command("send")
@click.argument("name_or_phone")
@click.argument("text")
@click.pass_context
def message_send(ctx, name_or_phone, text):
    """Send a message to a contact or group."""
    # Resolve name to JID/phone if needed
    target = name_or_phone
    jid = _resolve_jid(name_or_phone)
    if jid:
        target = jid

    result, err = _safe_run(_send_message, target, text)
    if err:
        _output(ctx, {"error": f"Failed to send message: {err}"})
        return

    state = ctx.ensure_object(SessionState)
    if state.json_mode:
        _output(ctx, {"sent": True, "to": name_or_phone, "text": text})
        return

    _SKIN.success(f"Message sent to {name_or_phone}")


@message.command("send-file")
@click.argument("name_or_phone")
@click.argument("file_path", type=click.Path(exists=True))
@click.option("--caption", default="", help="Caption for the file.")
@click.pass_context
def message_send_file(ctx, name_or_phone, file_path, caption):
    """Send a file (image/video/document) to a contact or group."""
    from whatsapp_cli.utils.wa_backend import send_file as _backend_send_file

    # Resolve name to JID if needed
    target = name_or_phone
    jid = _resolve_jid(name_or_phone)
    if jid:
        target = jid

    abs_path = os.path.abspath(os.path.expanduser(file_path))
    result, err = _safe_run(_backend_send_file, target, abs_path, caption)
    if err:
        _output(ctx, {"error": f"Failed to send file: {err}"})
        return

    state = ctx.ensure_object(SessionState)
    if state.json_mode:
        _output(ctx, {"sent": True, "to": name_or_phone,
                       "file_path": abs_path, "caption": caption})
        return

    _SKIN.success(f"File sent to {name_or_phone}: {os.path.basename(abs_path)}")


@message.command("count")
@click.option("--chat", "chat_name", default=None,
              help="Count messages in a specific chat.")
@click.pass_context
def message_count(ctx, chat_name):
    """Count messages."""
    counts, err = _safe_run(_count_messages, chat_name=chat_name)
    if err:
        _output(ctx, {"error": f"Failed to count messages: {err}"})
        return

    state = ctx.ensure_object(SessionState)
    if state.json_mode:
        _output(ctx, counts)
        return

    label = f" in '{chat_name}'" if chat_name else " (all chats)"
    _SKIN.status_block({
        "Total": str(counts["total"]),
        "Sent": str(counts["sent"]),
        "Received": str(counts["received"]),
    }, title=f"Message Count{label}")


# ===========================================================================
# contact group
# ===========================================================================

@cli.group()
@click.pass_context
def contact(ctx):
    """Contact operations."""
    pass


@contact.command("list")
@click.pass_context
def contact_list(ctx):
    """List all contacts."""
    contacts, err = _safe_run(_list_contacts)
    if err:
        _output(ctx, {"error": f"Failed to list contacts: {err}"})
        return

    state = ctx.ensure_object(SessionState)
    if state.json_mode:
        _output(ctx, {"contacts": contacts, "count": len(contacts)})
        return

    headers = ["Name", "Phone", "JID"]
    rows = [
        [
            _truncate(c["name"], 25),
            c.get("phone", ""),
            _truncate(c["jid"], 30),
        ]
        for c in contacts
    ]
    _output(ctx, {"headers": headers, "rows": rows})


@contact.command("search")
@click.argument("query")
@click.pass_context
def contact_search(ctx, query):
    """Search contacts by name or phone."""
    results, err = _safe_run(_search_contacts, query)
    if err:
        _output(ctx, {"error": f"Search failed: {err}"})
        return

    state = ctx.ensure_object(SessionState)
    if state.json_mode:
        _output(ctx, {"results": results, "count": len(results), "query": query})
        return

    if not results:
        _SKIN.info(f"No contacts found for '{query}'")
        return

    headers = ["Name", "Phone", "JID"]
    rows = [
        [
            _truncate(c["name"], 25),
            c.get("phone", ""),
            _truncate(c["jid"], 30),
        ]
        for c in results
    ]
    _output(ctx, {"headers": headers, "rows": rows})


@contact.command("info")
@click.argument("name_or_jid")
@click.pass_context
def contact_info(ctx, name_or_jid):
    """Get contact info."""
    info, err = _safe_run(_get_contact_info, name_or_jid)
    if err:
        _output(ctx, {"error": f"Failed to get contact info: {err}"})
        return
    if info is None:
        _output(ctx, {"error": f"Contact not found: {name_or_jid}"})
        return

    state = ctx.ensure_object(SessionState)
    if state.json_mode:
        _output(ctx, info)
        return

    block = {
        "Name": info["name"],
        "JID": info["jid"],
        "Phone": info.get("phone", ""),
        "Messages": str(info.get("message_count", 0)),
    }
    if info.get("last_timestamp"):
        block["Last Activity"] = _format_timestamp(info["last_timestamp"])
    _SKIN.status_block(block, title=info["name"])


@contact.command("resolve")
@click.argument("name")
@click.pass_context
def contact_resolve(ctx, name):
    """Resolve a contact name to a JID."""
    jid, err = _safe_run(_resolve_name_to_jid, name)
    if err:
        _output(ctx, {"error": f"Resolve failed: {err}"})
        return
    if jid is None:
        _output(ctx, {"error": f"Could not resolve name: {name}"})
        return

    state = ctx.ensure_object(SessionState)
    if state.json_mode:
        _output(ctx, {"name": name, "jid": jid})
        return

    _SKIN.status("Name", name)
    _SKIN.status("JID", jid)


# ===========================================================================
# group group
# ===========================================================================

@cli.group()
@click.pass_context
def group(ctx):
    """Group operations."""
    pass


@group.command("list")
@click.pass_context
def group_list(ctx):
    """List all groups."""
    groups, err = _safe_run(_list_groups)
    if err:
        _output(ctx, {"error": f"Failed to list groups: {err}"})
        return

    state = ctx.ensure_object(SessionState)
    if state.json_mode:
        _output(ctx, {"groups": groups, "count": len(groups)})
        return

    headers = ["Name", "JID", "Messages", "Last Activity"]
    rows = [
        [
            _truncate(g["name"], 30),
            _truncate(g["jid"], 30),
            str(g["message_count"]),
            _format_timestamp(g["last_timestamp"]),
        ]
        for g in groups
    ]
    _output(ctx, {"headers": headers, "rows": rows})


@group.command("info")
@click.argument("name_or_jid")
@click.pass_context
def group_info(ctx, name_or_jid):
    """Get group info."""
    info, err = _safe_run(_get_group_info, name_or_jid)
    if err:
        _output(ctx, {"error": f"Failed to get group info: {err}"})
        return
    if info is None:
        _output(ctx, {"error": f"Group not found: {name_or_jid}"})
        return

    state = ctx.ensure_object(SessionState)
    if state.json_mode:
        _output(ctx, info)
        return

    _SKIN.status_block({
        "Name": info["name"],
        "JID": info["jid"],
        "Members": str(info.get("member_count", 0)),
        "Messages": str(info["message_count"]),
        "Last Activity": _format_timestamp(info["last_timestamp"]),
    }, title=info["name"])


@group.command("members")
@click.argument("name_or_jid")
@click.pass_context
def group_members(ctx, name_or_jid):
    """Get group members."""
    members, err = _safe_run(_get_group_members, name_or_jid)
    if err:
        _output(ctx, {"error": f"Failed to get members: {err}"})
        return

    state = ctx.ensure_object(SessionState)
    if state.json_mode:
        _output(ctx, {"members": members, "count": len(members),
                       "group": name_or_jid})
        return

    if not members:
        _SKIN.info(f"No members found for '{name_or_jid}'")
        return

    headers = ["Name", "JID", "Admin"]
    rows = [
        [
            _truncate(m["name"], 25),
            _truncate(m["jid"], 30),
            "Yes" if m["is_admin"] else "",
        ]
        for m in members
    ]
    _output(ctx, {"headers": headers, "rows": rows})


@group.command("search")
@click.argument("query")
@click.pass_context
def group_search(ctx, query):
    """Search groups by name."""
    results, err = _safe_run(_search_groups, query)
    if err:
        _output(ctx, {"error": f"Search failed: {err}"})
        return

    state = ctx.ensure_object(SessionState)
    if state.json_mode:
        _output(ctx, {"results": results, "count": len(results), "query": query})
        return

    if not results:
        _SKIN.info(f"No groups found for '{query}'")
        return

    headers = ["Name", "JID", "Messages", "Last Activity"]
    rows = [
        [
            _truncate(g["name"], 30),
            _truncate(g["jid"], 30),
            str(g["message_count"]),
            _format_timestamp(g["last_timestamp"]),
        ]
        for g in results
    ]
    _output(ctx, {"headers": headers, "rows": rows})


# ===========================================================================
# monitor group
# ===========================================================================

@cli.group()
@click.pass_context
def monitor(ctx):
    """Monitoring commands."""
    pass


@monitor.command("watch")
@click.option("--chat", "chat_name", default=None,
              help="Watch a specific chat only.")
@click.option("--interval", default=5, help="Polling interval in seconds.")
@click.pass_context
def monitor_watch(ctx, chat_name, interval):
    """Watch for new messages in real time."""
    state = ctx.ensure_object(SessionState)
    label = f" in '{chat_name}'" if chat_name else " (all chats)"
    _SKIN.info(f"Watching for new messages{label}. Press Ctrl+C to stop.")

    last_ts = _datetime_to_apple_ts(datetime.now(tz=timezone.utc))

    try:
        while True:
            new_msgs = _get_messages_since(last_ts, chat_name=chat_name)
            for m in new_msgs:
                if state.json_mode:
                    click.echo(json.dumps(m, ensure_ascii=False))
                else:
                    chat_label = f"[{m['chat_name']}] " if not chat_name else ""
                    ts = m["formatted_time"]
                    sender = m["sender"]
                    text = m["text"] or "(media)"
                    click.echo(
                        f"  {_SKIN._c('\033[38;5;245m', f'[{ts}]')} "
                        f"{chat_label}"
                        f"{_SKIN._c('\033[38;5;40m\033[1m', sender)}: {text}"
                    )
                if m["timestamp"] and m["timestamp"] > last_ts:
                    last_ts = m["timestamp"]

            time.sleep(interval)
    except KeyboardInterrupt:
        _SKIN.info("Stopped watching.")


@monitor.command("since")
@click.argument("timestamp")
@click.option("--chat", "chat_name", default=None,
              help="Filter by chat.")
@click.pass_context
def monitor_since(ctx, timestamp, chat_name):
    """Get messages since a given timestamp (ISO format or Unix)."""
    try:
        if timestamp.replace(".", "").replace("-", "").isdigit():
            ts = float(timestamp)
        else:
            dt = datetime.fromisoformat(timestamp)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            ts = _datetime_to_apple_ts(dt)
    except (ValueError, TypeError) as exc:
        _output(ctx, {"error": f"Invalid timestamp: {exc}"})
        return

    messages, err = _safe_run(_get_messages_since, ts, chat_name=chat_name)
    if err:
        _output(ctx, {"error": f"Failed to get messages: {err}"})
        return

    state = ctx.ensure_object(SessionState)
    if state.json_mode:
        _output(ctx, {"messages": messages, "count": len(messages)})
        return

    if not messages:
        _SKIN.info("No new messages since that time.")
        return

    for m in messages:
        chat_label = f"[{m['chat_name']}] " if not chat_name else ""
        ts_str = m["formatted_time"]
        sender = m["sender"]
        text = m["text"] or "(media)"
        click.echo(
            f"  {_SKIN._c('\033[38;5;245m', f'[{ts_str}]')} "
            f"{chat_label}"
            f"{_SKIN._c('\033[38;5;40m\033[1m', sender)}: {text}"
        )


@monitor.command("auto-reply")
@click.option("--chat", "chat_name", default=None,
              help="Chat to auto-reply in (required).")
@click.option("--prompt", "prompt_text", required=True,
              help="System prompt for the Claude agent.")
@click.option("--interval", default=10,
              help="Polling interval in seconds.")
@click.option("--context-messages", default=10,
              help="Number of recent messages to include as context.")
@click.pass_context
def monitor_auto_reply(ctx, chat_name, prompt_text, interval, context_messages):
    """Set up auto-reply using Claude as the response generator.

    Polls for new messages and uses the Claude CLI to generate responses.
    Only replies to messages that are NOT from you.
    """
    if not chat_name:
        _output(ctx, {"error": "You must specify --chat for auto-reply."})
        return

    # Verify the chat exists
    chat_info = _get_chat_details(chat_name)
    if chat_info is None:
        _output(ctx, {"error": f"Chat not found: {chat_name}"})
        return

    jid = chat_info["jid"]
    display_name = chat_info["name"]

    _SKIN.info(f"Auto-reply active for '{display_name}'. Press Ctrl+C to stop.")
    _SKIN.hint(f"Prompt: {_truncate(prompt_text, 60)}")
    _SKIN.hint(f"Interval: {interval}s, Context: {context_messages} messages")

    last_ts = _datetime_to_apple_ts(datetime.now(tz=timezone.utc))

    try:
        while True:
            new_msgs = _get_messages_since(last_ts, chat_name=chat_name)

            for m in new_msgs:
                if m["timestamp"] and m["timestamp"] > last_ts:
                    last_ts = m["timestamp"]

                # Only reply to messages from others
                if m["from_me"]:
                    continue

                if not m["text"]:
                    continue

                _SKIN.info(f"New message from {m['sender']}: {_truncate(m['text'], 50)}")

                # Get recent messages for context
                recent = _get_messages(chat_name, limit=context_messages)
                conversation_lines = []
                for msg in recent:
                    sender = msg["sender"]
                    text = msg["text"] or "(media)"
                    conversation_lines.append(f"{sender}: {text}")

                conversation_text = "\n".join(conversation_lines)

                # Build the Claude prompt
                full_prompt = (
                    f"{prompt_text}\n\n"
                    f"Conversation:\n{conversation_text}\n\n"
                    f"Reply to the last message:"
                )

                # Call claude CLI to generate a response
                try:
                    result = subprocess.run(
                        ["claude", "-p", full_prompt],
                        capture_output=True, text=True, timeout=60,
                    )
                    if result.returncode != 0:
                        _SKIN.error(f"Claude failed: {result.stderr.strip()}")
                        continue

                    reply = result.stdout.strip()
                    if not reply:
                        _SKIN.warning("Claude returned empty response, skipping.")
                        continue

                    _SKIN.info(f"Sending reply: {_truncate(reply, 50)}")

                    # Send the reply
                    sent, send_err = _safe_run(_send_message, jid, reply)
                    if send_err:
                        _SKIN.error(f"Failed to send reply: {send_err}")
                    else:
                        _SKIN.success(f"Reply sent to {display_name}")

                except subprocess.TimeoutExpired:
                    _SKIN.error("Claude timed out generating a response.")
                except FileNotFoundError:
                    _SKIN.error(
                        "Claude CLI not found. Install it with: "
                        "npm install -g @anthropic-ai/claude-cli"
                    )
                    return

            time.sleep(interval)
    except KeyboardInterrupt:
        _SKIN.info("Auto-reply stopped.")


# ===========================================================================
# export group
# ===========================================================================

@cli.group("export")
@click.pass_context
def export_group(ctx):
    """Export operations."""
    pass


@export_group.command("chat")
@click.argument("name_or_jid")
@click.argument("output_path")
@click.option("--format", "fmt", default="txt",
              type=click.Choice(["txt", "json", "csv"]),
              help="Export format.")
@click.pass_context
def export_chat(ctx, name_or_jid, output_path, fmt):
    """Export chat history to a file."""
    result, err = _safe_run(_export_chat, name_or_jid, output_path, fmt=fmt)
    if err:
        _output(ctx, {"error": f"Export failed: {err}"})
        return

    state = ctx.ensure_object(SessionState)
    if state.json_mode:
        _output(ctx, result)
        return

    if "error" in result:
        _output(ctx, result)
        return

    _SKIN.success(
        f"Exported {result['messages_exported']} messages from "
        f"'{result['chat']}' to {result['output_path']} ({result['format']})"
    )


@export_group.command("media")
@click.argument("name_or_jid")
@click.argument("output_dir")
@click.pass_context
def export_media(ctx, name_or_jid, output_dir):
    """Export media files from a chat."""
    result, err = _safe_run(_export_media, name_or_jid, output_dir)
    if err:
        _output(ctx, {"error": f"Export failed: {err}"})
        return

    state = ctx.ensure_object(SessionState)
    if state.json_mode:
        _output(ctx, result)
        return

    if "error" in result:
        _output(ctx, result)
        return

    _SKIN.success(
        f"Exported {result['exported']}/{result['total_media_found']} "
        f"media files to {result['output_dir']}"
    )


# ===========================================================================
# ui group — UI automation via System Events
# ===========================================================================

@cli.group()
@click.pass_context
def ui(ctx):
    """UI automation operations (System Events)."""
    pass


@ui.command("navigate")
@click.argument("view", type=click.Choice(
    ["chats", "calls", "updates", "archived", "starred", "settings", "profile"],
    case_sensitive=False,
))
@click.pass_context
def ui_navigate(ctx, view):
    """Navigate to a WhatsApp view (chats/calls/updates/archived/starred/settings/profile)."""
    from whatsapp_cli.utils.wa_backend import navigate_view

    result, err = _safe_run(navigate_view, view)
    if err:
        _output(ctx, {"error": f"Failed to navigate: {err}"})
        return

    state = ctx.ensure_object(SessionState)
    if state.json_mode:
        _output(ctx, {"navigated": True, "view": view})
        return

    _SKIN.success(f"Navigated to {view.title()}")


@ui.command("voice-call")
@click.argument("name_or_phone")
@click.pass_context
def ui_voice_call(ctx, name_or_phone):
    """Start a voice call with a contact or group."""
    from whatsapp_cli.utils.wa_backend import start_voice_call

    target = name_or_phone
    jid = _resolve_jid(name_or_phone)
    if jid:
        target = jid

    result, err = _safe_run(start_voice_call, target)
    if err:
        _output(ctx, {"error": f"Failed to start voice call: {err}"})
        return

    state = ctx.ensure_object(SessionState)
    if state.json_mode:
        _output(ctx, {"calling": True, "type": "voice", "to": name_or_phone})
        return

    _SKIN.success(f"Voice call started with {name_or_phone}")


@ui.command("video-call")
@click.argument("name_or_phone")
@click.pass_context
def ui_video_call(ctx, name_or_phone):
    """Start a video call with a contact or group."""
    from whatsapp_cli.utils.wa_backend import start_video_call

    target = name_or_phone
    jid = _resolve_jid(name_or_phone)
    if jid:
        target = jid

    result, err = _safe_run(start_video_call, target)
    if err:
        _output(ctx, {"error": f"Failed to start video call: {err}"})
        return

    state = ctx.ensure_object(SessionState)
    if state.json_mode:
        _output(ctx, {"calling": True, "type": "video", "to": name_or_phone})
        return

    _SKIN.success(f"Video call started with {name_or_phone}")


@ui.command("new-chat")
@click.pass_context
def ui_new_chat(ctx):
    """Open the New Chat dialog."""
    from whatsapp_cli.utils.wa_backend import open_new_chat

    result, err = _safe_run(open_new_chat)
    if err:
        _output(ctx, {"error": f"Failed to open new chat: {err}"})
        return

    state = ctx.ensure_object(SessionState)
    if state.json_mode:
        _output(ctx, {"opened": True, "dialog": "new_chat"})
        return

    _SKIN.success("New Chat dialog opened")


@ui.command("new-group")
@click.pass_context
def ui_new_group(ctx):
    """Open the New Group dialog."""
    from whatsapp_cli.utils.wa_backend import open_new_group

    result, err = _safe_run(open_new_group)
    if err:
        _output(ctx, {"error": f"Failed to open new group: {err}"})
        return

    state = ctx.ensure_object(SessionState)
    if state.json_mode:
        _output(ctx, {"opened": True, "dialog": "new_group"})
        return

    _SKIN.success("New Group dialog opened")


@ui.command("search")
@click.argument("query")
@click.pass_context
def ui_search(ctx, query):
    """Search in WhatsApp UI (Cmd+F)."""
    from whatsapp_cli.utils.wa_backend import search_ui

    result, err = _safe_run(search_ui, query)
    if err:
        _output(ctx, {"error": f"Failed to search: {err}"})
        return

    state = ctx.ensure_object(SessionState)
    if state.json_mode:
        _output(ctx, {"searching": True, "query": query})
        return

    _SKIN.success(f"Searching for '{query}'")


@ui.command("contact-info")
@click.argument("name_or_phone")
@click.pass_context
def ui_contact_info(ctx, name_or_phone):
    """Open contact or group info panel."""
    from whatsapp_cli.utils.wa_backend import open_contact_info

    target = name_or_phone
    jid = _resolve_jid(name_or_phone)
    if jid:
        target = jid

    result, err = _safe_run(open_contact_info, target)
    if err:
        _output(ctx, {"error": f"Failed to open contact info: {err}"})
        return

    state = ctx.ensure_object(SessionState)
    if state.json_mode:
        _output(ctx, {"opened": True, "contact": name_or_phone})
        return

    _SKIN.success(f"Contact info opened for {name_or_phone}")


# ===========================================================================
# session group
# ===========================================================================

@cli.group()
@click.pass_context
def session(ctx):
    """Session management."""
    pass


@session.command("status")
@click.pass_context
def session_status(ctx):
    """Show session status."""
    wa_running = _is_whatsapp_running()
    db_exists = os.path.isfile(_DB_PATH)

    info = {
        "whatsapp_running": wa_running,
        "database_found": db_exists,
        "database_path": _DB_PATH,
        "version": __version__,
    }

    # Get some stats if DB is available
    if db_exists:
        try:
            conn = _get_db()
            try:
                chat_count = conn.execute(
                    "SELECT COUNT(*) FROM ZWACHATSESSION"
                ).fetchone()[0]
                msg_count = conn.execute(
                    "SELECT COUNT(*) FROM ZWAMESSAGE"
                ).fetchone()[0]
                info["total_chats"] = chat_count
                info["total_messages"] = msg_count
            finally:
                conn.close()
        except Exception:
            pass

    state = ctx.ensure_object(SessionState)
    if state.json_mode:
        _output(ctx, info)
        return

    _SKIN.status_block({
        "WhatsApp": "Running" if wa_running else "Not running",
        "Database": "Found" if db_exists else "Not found",
        "DB Path": _DB_PATH,
        "Version": __version__,
        "Chats": str(info.get("total_chats", "N/A")),
        "Messages": str(info.get("total_messages", "N/A")),
    }, title="Session Status")


# ===========================================================================
# repl command (explicit entry point)
# ===========================================================================

@cli.command("repl")
@click.pass_context
def repl_command(ctx):
    """Enter the interactive REPL."""
    _run_repl(ctx)


# ===========================================================================
# REPL
# ===========================================================================

_REPL_HELP = {
    # chat
    "chat list":            "List recent chats [--limit N] [--groups/--no-groups]",
    "chat search":          "Search chats <query>",
    "chat unread":          "Show unread chats",
    "chat get":             "Get chat details <name_or_jid>",
    "chat find":            "Find chat by phone <phone>",
    # message
    "message get":          "Get messages <name_or_jid> [--limit N] [--before] [--after]",
    "message search":       "Search messages <query> [--chat NAME]",
    "message starred":      "Get starred messages [--chat NAME]",
    "message media":        "Get media messages <name_or_jid> [--limit N]",
    "message send":         "Send message <name_or_phone> <text>",
    "message send-file":    "Send file <name_or_phone> <file_path> [--caption TEXT]",
    "message count":        "Count messages [--chat NAME]",
    # contact
    "contact list":         "List contacts",
    "contact search":       "Search contacts <query>",
    "contact info":         "Get contact info <name_or_jid>",
    "contact resolve":      "Resolve name to JID <name>",
    # group
    "group list":           "List groups",
    "group info":           "Get group info <name_or_jid>",
    "group members":        "Get group members <name_or_jid>",
    "group search":         "Search groups <query>",
    # monitor
    "monitor watch":        "Watch for new messages [--chat NAME] [--interval N]",
    "monitor since":        "Get messages since <timestamp> [--chat NAME]",
    "monitor auto-reply":   "Auto-reply [--chat NAME] --prompt PROMPT [--interval N]",
    # export
    "export chat":          "Export chat <name_or_jid> <output> [--format txt/json/csv]",
    "export media":         "Export media <name_or_jid> <output_dir>",
    # ui
    "ui navigate":          "Navigate view <chats/calls/updates/archived/starred/settings/profile>",
    "ui voice-call":        "Start voice call <name_or_phone>",
    "ui video-call":        "Start video call <name_or_phone>",
    "ui new-chat":          "Open new chat dialog",
    "ui new-group":         "Open new group dialog",
    "ui search":            "Search in WhatsApp UI <query>",
    "ui contact-info":      "Open contact/group info <name_or_phone>",
    # session
    "session status":       "Show session status",
    # misc
    "help":                 "Show this help message",
    "quit / exit":          "Exit the REPL",
}


def _tokenize_input(raw: str) -> list[str]:
    """Split user input respecting quoted strings."""
    try:
        return shlex.split(raw)
    except ValueError:
        return raw.split()


def _run_repl(ctx: click.Context):
    """Run the interactive REPL loop."""
    state = ctx.ensure_object(SessionState)
    skin = ReplSkin("whatsapp", version=__version__)

    skin.print_banner()

    pt_session = skin.create_prompt_session()

    while True:
        context_str = state.display_context

        try:
            user_input = skin.get_input(
                pt_session,
                context=context_str,
            )
        except (EOFError, KeyboardInterrupt):
            skin.print_goodbye()
            break

        if not user_input:
            continue

        lowered = user_input.strip().lower()
        if lowered in ("quit", "exit", "q"):
            skin.print_goodbye()
            break

        if lowered in ("help", "?"):
            skin.help(_REPL_HELP)
            continue

        tokens = _tokenize_input(user_input)
        if not tokens:
            continue

        args = list(tokens)
        if state.json_mode and "--json" not in args:
            args = ["--json"] + args

        try:
            cli.main(
                args=args,
                prog_name="whatsapp-cli",
                standalone_mode=False,
                **{"obj": state},
            )
        except click.exceptions.UsageError as exc:
            skin.error(str(exc))
            skin.hint("Type 'help' to see available commands.")
        except click.exceptions.Abort:
            skin.warning("Command aborted.")
        except SystemExit:
            pass
        except Exception as exc:
            skin.error(f"Unexpected error: {exc}")


# ===========================================================================
# Entry point
# ===========================================================================

def main():
    """Entry point for console_scripts."""
    cli(obj=SessionState())


if __name__ == "__main__":
    main()
