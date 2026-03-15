"""Contact operations — read contact information from WhatsApp databases."""

from __future__ import annotations

from whatsapp_cli.utils.wa_backend import _get_db, _get_contacts_db


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def list_contacts() -> list[dict]:
    """List all contacts from the WhatsApp contacts database.

    Returns:
        List of contact dicts with keys: name, jid, phone, sort_name.
        Sorted alphabetically by name.
    """
    try:
        db = _get_contacts_db()
    except FileNotFoundError:
        # Fallback: extract contacts from chat sessions
        return _contacts_from_chats()

    try:
        # Try the ContactsV2.sqlite schema
        rows = db.execute(
            "SELECT ZFULLNAME, ZWHATSAPPID, ZPHONENUMBER, ZSORTNAME "
            "FROM ZWAADDRESSBOOKCONTACT "
            "WHERE ZFULLNAME IS NOT NULL "
            "ORDER BY ZSORTNAME"
        ).fetchall()
        return [
            {
                "name": row["ZFULLNAME"],
                "jid": row["ZWHATSAPPID"],
                "phone": row["ZPHONENUMBER"],
                "sort_name": row["ZSORTNAME"],
            }
            for row in rows
        ]
    except Exception:
        # Schema may differ across versions — fall back to chat sessions
        return _contacts_from_chats()
    finally:
        db.close()


def search_contacts(query: str) -> list[dict]:
    """Search contacts by name (case-insensitive substring match).

    Args:
        query: Search string.

    Returns:
        List of matching contact dicts.
    """
    try:
        db = _get_contacts_db()
    except FileNotFoundError:
        return _search_contacts_from_chats(query)

    try:
        rows = db.execute(
            "SELECT ZFULLNAME, ZWHATSAPPID, ZPHONENUMBER, ZSORTNAME "
            "FROM ZWAADDRESSBOOKCONTACT "
            "WHERE ZFULLNAME LIKE ? "
            "ORDER BY ZSORTNAME",
            (f"%{query}%",),
        ).fetchall()
        return [
            {
                "name": row["ZFULLNAME"],
                "jid": row["ZWHATSAPPID"],
                "phone": row["ZPHONENUMBER"],
                "sort_name": row["ZSORTNAME"],
            }
            for row in rows
        ]
    except Exception:
        return _search_contacts_from_chats(query)
    finally:
        db.close()


def get_contact_info(jid_or_name: str) -> dict | None:
    """Get detailed information about a single contact.

    Looks up the contact in ContactsV2.sqlite first, then falls back to
    the chat session table.

    Args:
        jid_or_name: A JID string or contact name.

    Returns:
        Contact dict with keys: name, jid, phone, sort_name; or None.
    """
    try:
        db = _get_contacts_db()
    except FileNotFoundError:
        return _contact_info_from_chats(jid_or_name)

    try:
        if "@" in jid_or_name:
            row = db.execute(
                "SELECT ZFULLNAME, ZWHATSAPPID, ZPHONENUMBER, ZSORTNAME "
                "FROM ZWAADDRESSBOOKCONTACT "
                "WHERE ZWHATSAPPID = ? LIMIT 1",
                (jid_or_name,),
            ).fetchone()
        else:
            row = db.execute(
                "SELECT ZFULLNAME, ZWHATSAPPID, ZPHONENUMBER, ZSORTNAME "
                "FROM ZWAADDRESSBOOKCONTACT "
                "WHERE ZFULLNAME LIKE ? LIMIT 1",
                (f"%{jid_or_name}%",),
            ).fetchone()

        if row is None:
            return _contact_info_from_chats(jid_or_name)

        return {
            "name": row["ZFULLNAME"],
            "jid": row["ZWHATSAPPID"],
            "phone": row["ZPHONENUMBER"],
            "sort_name": row["ZSORTNAME"],
        }
    except Exception:
        return _contact_info_from_chats(jid_or_name)
    finally:
        db.close()


def resolve_name_to_jid(name: str) -> str | None:
    """Resolve a display name to a WhatsApp JID.

    Searches both the contacts database and the chat session table.

    Args:
        name: Contact display name (partial match supported).

    Returns:
        JID string, or None if no match is found.
    """
    contact = get_contact_info(name)
    if contact and contact.get("jid"):
        return contact["jid"]

    # Fallback: search chat sessions
    db = _get_db()
    try:
        row = db.execute(
            "SELECT ZCONTACTJID FROM ZWACHATSESSION "
            "WHERE ZPARTNERNAME LIKE ? LIMIT 1",
            (f"%{name}%",),
        ).fetchone()
        return row["ZCONTACTJID"] if row else None
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Fallback helpers — extract contact info from chat sessions
# ---------------------------------------------------------------------------

def _contacts_from_chats() -> list[dict]:
    """Extract contacts from ZWACHATSESSION (fallback when ContactsV2 missing)."""
    db = _get_db()
    try:
        rows = db.execute(
            "SELECT ZPARTNERNAME, ZCONTACTJID FROM ZWACHATSESSION "
            "WHERE ZSESSIONTYPE = 0 AND ZPARTNERNAME IS NOT NULL "
            "ORDER BY ZPARTNERNAME"
        ).fetchall()
        return [
            {
                "name": row["ZPARTNERNAME"],
                "jid": row["ZCONTACTJID"],
                "phone": _jid_to_phone(row["ZCONTACTJID"]),
                "sort_name": row["ZPARTNERNAME"],
            }
            for row in rows
        ]
    finally:
        db.close()


def _search_contacts_from_chats(query: str) -> list[dict]:
    """Search contacts using ZWACHATSESSION as fallback."""
    db = _get_db()
    try:
        rows = db.execute(
            "SELECT ZPARTNERNAME, ZCONTACTJID FROM ZWACHATSESSION "
            "WHERE ZSESSIONTYPE = 0 AND ZPARTNERNAME LIKE ? "
            "ORDER BY ZPARTNERNAME",
            (f"%{query}%",),
        ).fetchall()
        return [
            {
                "name": row["ZPARTNERNAME"],
                "jid": row["ZCONTACTJID"],
                "phone": _jid_to_phone(row["ZCONTACTJID"]),
                "sort_name": row["ZPARTNERNAME"],
            }
            for row in rows
        ]
    finally:
        db.close()


def _contact_info_from_chats(jid_or_name: str) -> dict | None:
    """Get contact info from ZWACHATSESSION as fallback."""
    db = _get_db()
    try:
        if "@" in jid_or_name:
            row = db.execute(
                "SELECT ZPARTNERNAME, ZCONTACTJID FROM ZWACHATSESSION "
                "WHERE ZCONTACTJID = ? LIMIT 1",
                (jid_or_name,),
            ).fetchone()
        else:
            row = db.execute(
                "SELECT ZPARTNERNAME, ZCONTACTJID FROM ZWACHATSESSION "
                "WHERE ZPARTNERNAME LIKE ? LIMIT 1",
                (f"%{jid_or_name}%",),
            ).fetchone()

        if row is None:
            return None

        return {
            "name": row["ZPARTNERNAME"],
            "jid": row["ZCONTACTJID"],
            "phone": _jid_to_phone(row["ZCONTACTJID"]),
            "sort_name": row["ZPARTNERNAME"],
        }
    finally:
        db.close()


def _jid_to_phone(jid: str | None) -> str | None:
    """Extract phone number from a JID like '4915563097687@s.whatsapp.net'."""
    if not jid or "@" not in jid:
        return None
    phone_part = jid.split("@")[0]
    if phone_part.isdigit():
        return f"+{phone_part}"
    return None
