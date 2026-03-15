"""WhatsApp macOS backend — SQLite read access, URL scheme, and UI automation."""

import os
import re
import sqlite3
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

# ---------------------------------------------------------------------------
# Path constants
# ---------------------------------------------------------------------------

_WA_CONTAINER = os.path.expanduser(
    "~/Library/Group Containers/group.net.whatsapp.WhatsApp.shared"
)

DB_PATH = os.path.join(_WA_CONTAINER, "ChatStorage.sqlite")

CONTACTS_DB_PATH = os.path.join(_WA_CONTAINER, "ContactsV2.sqlite")

MEDIA_PATH = os.path.join(_WA_CONTAINER, "Message", "Media")

# Apple Core Data epoch offset (seconds between 1970-01-01 and 2001-01-01)
_APPLE_EPOCH_OFFSET = 978307200


# ---------------------------------------------------------------------------
# SQLite helpers
# ---------------------------------------------------------------------------

def _get_db(path: str | None = None) -> sqlite3.Connection:
    """Return a read-only SQLite connection to the WhatsApp database.

    Args:
        path: Optional override for the database path. Defaults to DB_PATH.

    Returns:
        sqlite3.Connection configured for read-only access with row_factory.

    Raises:
        FileNotFoundError: If the database file does not exist.
        sqlite3.OperationalError: If the database cannot be opened.
    """
    db_path = path or DB_PATH
    if not os.path.isfile(db_path):
        raise FileNotFoundError(
            f"WhatsApp database not found at {db_path}. "
            "Is WhatsApp installed and has been opened at least once?"
        )
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _get_contacts_db() -> sqlite3.Connection:
    """Return a read-only SQLite connection to the contacts database.

    Returns:
        sqlite3.Connection configured for read-only access with row_factory.

    Raises:
        FileNotFoundError: If the contacts database does not exist.
    """
    return _get_db(CONTACTS_DB_PATH)


# ---------------------------------------------------------------------------
# Timestamp conversion
# ---------------------------------------------------------------------------

def _apple_ts_to_datetime(ts: float | None) -> datetime | None:
    """Convert an Apple Core Data timestamp to a Python datetime.

    Apple Core Data stores timestamps as seconds since 2001-01-01 00:00:00 UTC.
    We add the epoch offset to get a Unix timestamp.

    Args:
        ts: Apple Core Data timestamp (seconds since 2001-01-01), or None.

    Returns:
        datetime in UTC, or None if ts is None.
    """
    if ts is None:
        return None
    return datetime.fromtimestamp(ts + _APPLE_EPOCH_OFFSET, tz=timezone.utc)


def _datetime_to_apple_ts(dt: datetime) -> float:
    """Convert a Python datetime to an Apple Core Data timestamp.

    Args:
        dt: datetime object (timezone-aware or naive; naive assumed UTC).

    Returns:
        float: Apple Core Data timestamp.
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp() - _APPLE_EPOCH_OFFSET


# ---------------------------------------------------------------------------
# AppleScript / System Events helpers
# ---------------------------------------------------------------------------

def _run_applescript(script: str) -> str:
    """Execute an AppleScript snippet and return stdout.

    Args:
        script: The AppleScript source code.

    Returns:
        str: stdout from osascript.

    Raises:
        RuntimeError: If osascript returns a non-zero exit code.
    """
    result = subprocess.run(
        ["osascript", "-e", script],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"AppleScript failed (rc={result.returncode}): {result.stderr.strip()}"
        )
    return result.stdout.strip()


# ---------------------------------------------------------------------------
# WhatsApp process helpers
# ---------------------------------------------------------------------------

def find_whatsapp() -> str | None:
    """Find the WhatsApp.app bundle path on macOS.

    Returns:
        str: Path to WhatsApp.app, or None if not found.
    """
    candidates = [
        "/Applications/WhatsApp.app",
        os.path.expanduser("~/Applications/WhatsApp.app"),
    ]
    for path in candidates:
        if os.path.isdir(path):
            return path

    # Fallback: mdfind
    try:
        result = subprocess.run(
            ["mdfind", "kMDItemCFBundleIdentifier == 'net.whatsapp.WhatsApp'"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip().splitlines()[0]
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    return None


def is_whatsapp_running() -> bool:
    """Check whether WhatsApp is currently running.

    Returns:
        bool: True if WhatsApp process is active.
    """
    try:
        result = subprocess.run(
            ["pgrep", "-f", "WhatsApp"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


def ensure_whatsapp_running() -> None:
    """Launch WhatsApp if it is not already running.

    Waits up to 10 seconds for the app to become active.

    Raises:
        RuntimeError: If WhatsApp is not installed or fails to launch.
    """
    if is_whatsapp_running():
        return

    app_path = find_whatsapp()
    if app_path is None:
        raise RuntimeError(
            "WhatsApp is not installed. "
            "Please install WhatsApp from the Mac App Store or whatsapp.com."
        )

    subprocess.run(["open", "-a", app_path], check=True, timeout=10)

    # Wait for the process to appear
    for _ in range(20):
        time.sleep(0.5)
        if is_whatsapp_running():
            return

    raise RuntimeError("WhatsApp was launched but did not start within 10 seconds.")


# ---------------------------------------------------------------------------
# Send via URL scheme
# ---------------------------------------------------------------------------

def send_url_scheme(phone: str, text: str) -> None:
    """Open a WhatsApp chat via the whatsapp:// URL scheme with pre-filled text.

    This opens the chat window with the text pre-filled but does NOT send it.
    Use send_via_ui() for a complete send flow.

    Args:
        phone: Phone number (with or without +). Will be cleaned to digits only.
        text: Message text to pre-fill.
    """
    # Clean phone number to digits only
    clean_phone = re.sub(r"[^\d]", "", phone)
    encoded_text = quote(text, safe="")
    url = f"whatsapp://send?phone={clean_phone}&text={encoded_text}"

    subprocess.run(["open", url], check=True, timeout=10)


# ---------------------------------------------------------------------------
# Send via UI automation (full flow)
# ---------------------------------------------------------------------------

def send_via_ui(phone_or_jid: str, text: str, confirm: bool = True) -> bool:
    """Send a message via UI automation: URL scheme + System Events keystroke.

    Flow:
      1. Ensure WhatsApp is running.
      2. Open chat via URL scheme with the message text pre-filled.
      3. Wait for WhatsApp to become active and the chat to open.
      4. Use System Events to press Enter to send the message.

    Args:
        phone_or_jid: Phone number (digits, with or without +) or a JID.
            If a JID is provided, the phone portion is extracted.
        text: The message to send.
        confirm: If True (default), log a confirmation. Set to False for
            automated/batch sending (use with caution).

    Returns:
        bool: True if the send sequence completed without errors.

    Raises:
        RuntimeError: If WhatsApp is not installed or the send fails.
    """
    # Extract phone number from JID if needed
    phone = phone_or_jid
    if "@" in phone:
        phone = phone.split("@")[0]

    # Remove any non-digit characters
    phone = re.sub(r"[^\d]", "", phone)

    if not phone:
        raise ValueError(f"Could not extract phone number from: {phone_or_jid}")

    # Step 1: Ensure WhatsApp is running
    ensure_whatsapp_running()

    # Step 2: Open chat with pre-filled text
    send_url_scheme(phone, text)

    # Step 3: Wait for WhatsApp to become the active window
    time.sleep(2.0)

    # Step 4: Activate WhatsApp and press Enter via System Events
    _activate_whatsapp()
    time.sleep(0.5)

    _press_enter()

    return True


def _activate_whatsapp() -> None:
    """Bring WhatsApp to the foreground via AppleScript."""
    _run_applescript(
        'tell application "WhatsApp" to activate'
    )


def _press_enter() -> None:
    """Press the Enter/Return key via System Events."""
    _run_applescript(
        'tell application "System Events" to keystroke return'
    )
