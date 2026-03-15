"""Session state management for WhatsApp CLI harness."""

import json
from datetime import datetime, timezone
from pathlib import Path


class Session:
    """Tracks the current session state for the WhatsApp CLI harness.

    Maintains the active chat reference, monitoring state, command history,
    and provides serialisation for session persistence.
    """

    def __init__(self) -> None:
        """Initialize a new empty session."""
        self._active_chat_jid: str | None = None
        self._active_chat_name: str | None = None
        self._monitoring: bool = False
        self._monitoring_jid: str | None = None
        self._history: list[dict] = []
        self._created_at: str = datetime.now(timezone.utc).isoformat()
        self._last_message_check: str | None = None

    # ------------------------------------------------------------------
    # Active chat
    # ------------------------------------------------------------------

    def set_active_chat(self, jid: str, name: str | None = None) -> None:
        """Set the currently active chat.

        Args:
            jid: The chat JID.
            name: Optional display name of the chat.
        """
        self._active_chat_jid = jid
        self._active_chat_name = name

    def get_active_chat(self) -> dict | None:
        """Get the currently active chat.

        Returns:
            Dict with jid and name keys, or None if no chat is active.
        """
        if self._active_chat_jid is None:
            return None
        return {
            "jid": self._active_chat_jid,
            "name": self._active_chat_name,
        }

    def clear_active_chat(self) -> None:
        """Clear the active chat reference."""
        self._active_chat_jid = None
        self._active_chat_name = None

    # ------------------------------------------------------------------
    # Monitoring state
    # ------------------------------------------------------------------

    def set_monitoring(self, active: bool, jid: str | None = None) -> None:
        """Update the monitoring state.

        Args:
            active: Whether monitoring is active.
            jid: Optional JID being monitored (None = all chats).
        """
        self._monitoring = active
        self._monitoring_jid = jid if active else None

    def is_monitoring(self) -> bool:
        """Check whether monitoring is currently active."""
        return self._monitoring

    def get_monitoring_target(self) -> str | None:
        """Get the JID being monitored, or None if monitoring all chats."""
        return self._monitoring_jid

    # ------------------------------------------------------------------
    # Last message check timestamp
    # ------------------------------------------------------------------

    def update_last_check(self) -> None:
        """Record the current time as the last message check timestamp."""
        self._last_message_check = datetime.now(timezone.utc).isoformat()

    def get_last_check(self) -> str | None:
        """Get the ISO timestamp of the last message check."""
        return self._last_message_check

    # ------------------------------------------------------------------
    # History
    # ------------------------------------------------------------------

    def add_to_history(self, command: str, result: str | None = None) -> None:
        """Add a command to the session history.

        Args:
            command: The command string that was executed.
            result: Optional summary of the result.
        """
        self._history.append({
            "command": command,
            "result": result,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

    def get_history(self) -> list[dict]:
        """Get the command history.

        Returns:
            List of history entries with keys: command, result, timestamp.
        """
        return list(self._history)

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def status(self) -> dict:
        """Get the current session status.

        Returns:
            Dict with session state information.
        """
        return {
            "active_chat_jid": self._active_chat_jid,
            "active_chat_name": self._active_chat_name,
            "monitoring": self._monitoring,
            "monitoring_jid": self._monitoring_jid,
            "last_message_check": self._last_message_check,
            "created_at": self._created_at,
            "command_count": len(self._history),
        }

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save_session(self, path: str) -> None:
        """Save the session state to a JSON file.

        Args:
            path: File path to save the session to.
        """
        data = {
            "active_chat_jid": self._active_chat_jid,
            "active_chat_name": self._active_chat_name,
            "monitoring": self._monitoring,
            "monitoring_jid": self._monitoring_jid,
            "last_message_check": self._last_message_check,
            "created_at": self._created_at,
            "history": self._history,
        }
        file_path = Path(path)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(
            json.dumps(data, indent=2, default=str), encoding="utf-8"
        )

    def load_session(self, path: str) -> None:
        """Load a session state from a JSON file.

        Args:
            path: File path to load the session from.

        Raises:
            FileNotFoundError: If the session file does not exist.
            json.JSONDecodeError: If the file is not valid JSON.
        """
        file_path = Path(path)
        data = json.loads(file_path.read_text(encoding="utf-8"))

        self._active_chat_jid = data.get("active_chat_jid")
        self._active_chat_name = data.get("active_chat_name")
        self._monitoring = data.get("monitoring", False)
        self._monitoring_jid = data.get("monitoring_jid")
        self._last_message_check = data.get("last_message_check")
        self._created_at = data.get("created_at", self._created_at)
        self._history = data.get("history", [])
