"""Export operations — export chat history and media from WhatsApp."""

from __future__ import annotations

import csv
import json
import os
import shutil
from pathlib import Path

from whatsapp_cli.core.chats import _resolve_jid, get_chat
from whatsapp_cli.core.messages import get_messages, get_media_messages
from whatsapp_cli.utils.wa_backend import MEDIA_PATH


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def export_chat(
    jid_or_name: str,
    output_path: str,
    format: str = "txt",
    limit: int = 0,
) -> str:
    """Export a chat's message history to a file.

    Args:
        jid_or_name: JID or contact/group name.
        output_path: Destination file path. Parent directories are created
            automatically.
        format: Output format — "txt", "json", or "csv". Defaults to "txt".
        limit: Maximum number of messages to export. 0 means all.

    Returns:
        str: Absolute path to the created file.

    Raises:
        ValueError: If the chat is not found or the format is unsupported.
    """
    chat = get_chat(jid_or_name)
    if chat is None:
        raise ValueError(f"Chat not found: {jid_or_name}")

    # Fetch messages (use a high limit for "all")
    fetch_limit = limit if limit > 0 else 100_000
    messages = get_messages(chat["jid"], limit=fetch_limit)

    out = Path(output_path).expanduser().resolve()
    out.parent.mkdir(parents=True, exist_ok=True)

    format_lower = format.lower()
    if format_lower == "txt":
        _export_txt(chat, messages, out)
    elif format_lower == "json":
        _export_json(chat, messages, out)
    elif format_lower == "csv":
        _export_csv(chat, messages, out)
    else:
        raise ValueError(
            f"Unsupported format: {format}. Use 'txt', 'json', or 'csv'."
        )

    return str(out)


def export_media(jid_or_name: str, output_dir: str, limit: int = 100) -> list[str]:
    """Copy media files from a chat to an output directory.

    Args:
        jid_or_name: JID or contact/group name.
        output_dir: Destination directory. Created if it does not exist.
        limit: Maximum number of media items to export. Defaults to 100.

    Returns:
        List of absolute paths to copied media files.

    Raises:
        ValueError: If the chat is not found.
    """
    chat = get_chat(jid_or_name)
    if chat is None:
        raise ValueError(f"Chat not found: {jid_or_name}")

    media_messages = get_media_messages(chat["jid"], limit=limit)

    out_dir = Path(output_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    copied_files = []
    for msg in media_messages:
        media_info = msg.get("media", {})
        local_path = media_info.get("local_path")
        if not local_path:
            continue

        # local_path may be relative to the media container or absolute
        source = Path(local_path)
        if not source.is_absolute():
            source = Path(MEDIA_PATH) / local_path

        if not source.is_file():
            continue

        dest = out_dir / source.name
        # Avoid overwriting — append counter if needed
        if dest.exists():
            stem = dest.stem
            suffix = dest.suffix
            counter = 1
            while dest.exists():
                dest = out_dir / f"{stem}_{counter}{suffix}"
                counter += 1

        shutil.copy2(str(source), str(dest))
        copied_files.append(str(dest))

    return copied_files


# ---------------------------------------------------------------------------
# Format writers
# ---------------------------------------------------------------------------

def _export_txt(chat: dict, messages: list[dict], out: Path) -> None:
    """Write messages as a plain-text transcript."""
    lines = []
    lines.append(f"WhatsApp Chat Export: {chat['name']} ({chat['jid']})")
    lines.append(f"Exported messages: {len(messages)}")
    lines.append("=" * 60)
    lines.append("")

    for msg in messages:
        timestamp = msg.get("time", "?")
        sender = "You" if msg["is_from_me"] else (msg.get("sender") or chat["name"])
        text = msg.get("text") or f"[{msg.get('message_type', 'media')}]"

        lines.append(f"[{timestamp}] {sender}: {text}")

    lines.append("")
    lines.append("=" * 60)
    lines.append("End of export")

    out.write_text("\n".join(lines), encoding="utf-8")


def _export_json(chat: dict, messages: list[dict], out: Path) -> None:
    """Write messages as a JSON file."""
    export_data = {
        "chat": {
            "name": chat["name"],
            "jid": chat["jid"],
            "session_type": chat.get("session_type"),
        },
        "message_count": len(messages),
        "messages": messages,
    }
    out.write_text(
        json.dumps(export_data, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )


def _export_csv(chat: dict, messages: list[dict], out: Path) -> None:
    """Write messages as a CSV file."""
    fieldnames = [
        "time", "sender", "text", "is_from_me",
        "message_type", "starred", "has_media",
    ]

    with out.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for msg in messages:
            row = dict(msg)
            # Make sender human-readable
            if row.get("is_from_me"):
                row["sender"] = "You"
            elif not row.get("sender") or row["sender"] == "unknown":
                row["sender"] = chat["name"]
            writer.writerow(row)
