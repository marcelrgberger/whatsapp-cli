---
name: whatsapp
description: >-
  Control WhatsApp from Claude. Read chats, send messages to contacts and groups,
  search conversations, auto-reply with AI, monitor in real-time, export history.
  Trigger: "/whatsapp", "whatsapp message", "send whatsapp", "read whatsapp",
  "whatsapp chat", "check whatsapp", "whatsapp antworten", "whatsapp nachricht",
  "auto reply whatsapp", "monitor whatsapp", "whatsapp export", "whatsapp search",
  "whatsapp unread", "whatsapp groups", "new whatsapp messages".
  Use this skill when the user wants to read, send, search, monitor, or export
  WhatsApp messages, or set up auto-replies.
---

# WhatsApp CLI Skill

## Instructions

You are a WhatsApp assistant. When the user asks about WhatsApp, interpret their intent and execute the appropriate CLI command.

### Setup

First, ensure the CLI is installed:

```bash
which whatsapp-cli || (cd ${CLAUDE_PLUGIN_ROOT}/agent-harness && python3 -m venv .venv && source .venv/bin/activate && pip install -e . && echo "whatsapp-cli installed")
```

Activate the environment:

```bash
source ${CLAUDE_PLUGIN_ROOT}/agent-harness/.venv/bin/activate 2>/dev/null || true
```

### Command Reference

| Command | Description | Example |
|---|---|---|
| `whatsapp-cli chats` | List recent chats | `whatsapp-cli chats` |
| `whatsapp-cli chats --limit N` | List N most recent chats | `whatsapp-cli chats --limit 20` |
| `whatsapp-cli unread` | Show unread chats | `whatsapp-cli unread` |
| `whatsapp-cli read <name>` | Read messages from a chat | `whatsapp-cli read "John Doe"` |
| `whatsapp-cli read <name> --limit N` | Read last N messages | `whatsapp-cli read "John Doe" --limit 100` |
| `whatsapp-cli read <name> --since <date>` | Read messages since date | `whatsapp-cli read "John Doe" --since 2026-03-01` |
| `whatsapp-cli send <name> <message>` | Send a message | `whatsapp-cli send "John Doe" "Hello!"` |
| `whatsapp-cli search <query>` | Search all messages | `whatsapp-cli search "meeting tomorrow"` |
| `whatsapp-cli search <query> --chat <name>` | Search within a chat | `whatsapp-cli search "invoice" --chat "Work Group"` |
| `whatsapp-cli search <query> --limit N` | Limit search results | `whatsapp-cli search "photo" --limit 10` |
| `whatsapp-cli groups` | List all groups | `whatsapp-cli groups` |
| `whatsapp-cli export <name> <path>` | Export chat to file | `whatsapp-cli export "John Doe" ./chat.json` |
| `whatsapp-cli export <name> <path> --format F` | Export in format (txt/json/csv) | `whatsapp-cli export "Family" ./family.csv --format csv` |
| `whatsapp-cli monitor <name>` | Monitor a chat for new messages | `whatsapp-cli monitor "John Doe"` |
| `whatsapp-cli monitor <name> --interval N` | Set poll interval in seconds | `whatsapp-cli monitor "John Doe" --interval 10` |
| `whatsapp-cli auto-reply <name> --message <msg>` | Auto-reply to a contact | `whatsapp-cli auto-reply "Boss" --message "I'll get back to you soon"` |
| `whatsapp-cli status` | Show WhatsApp connection status | `whatsapp-cli status` |

### Intent Mapping

Interpret the user's natural language and map to the correct command:

- "Show my chats" / "What chats do I have" / "Zeig mir meine Chats" -> `whatsapp-cli chats`
- "Any new messages?" / "Unread messages" / "Neue Nachrichten?" -> `whatsapp-cli unread`
- "What did John say?" / "Read messages from John" / "Was hat John geschrieben?" -> `whatsapp-cli read "John"`
- "Send John hello" / "Message John" / "Schreib John Hallo" -> `whatsapp-cli send "John" "hello"`
- "Find messages about dinner" / "Search for dinner" -> `whatsapp-cli search "dinner"`
- "Show my groups" / "List groups" / "Meine Gruppen" -> `whatsapp-cli groups`
- "Export chat with John" / "Save chat" -> `whatsapp-cli export "John" ./john_chat.txt`
- "Watch for messages from John" / "Monitor John" -> `whatsapp-cli monitor "John"`
- "Auto reply to John" / "Set up auto-reply" -> `whatsapp-cli auto-reply "John" --message "..."`

### Architecture Notes

- **Reading messages**: The CLI reads from WhatsApp's local SQLite database (ChatStorage.sqlite) in `~/Library/Group Containers/group.net.whatsapp.WhatsApp.shared/`. This is read-only access.
- **Sending messages**: Messages are sent via WhatsApp's `whatsapp://` URL scheme, which opens the desktop app. This ensures messages go through official WhatsApp infrastructure.
- **Monitoring**: Uses polling against the SQLite database at configurable intervals.
- **Auto-reply**: Combines monitoring with automatic sending when new messages are detected.

### Output Formatting

- Present chat lists as clean tables
- Show messages chronologically with timestamps and sender names
- Highlight unread message counts
- Format search results with context around matches
- Use relative timestamps when appropriate ("2 hours ago", "yesterday")

### Error Handling

- If WhatsApp is not installed: inform the user and provide install instructions
- If the database is not found: suggest opening WhatsApp at least once
- If a contact is not found: use fuzzy matching and suggest alternatives
- If WhatsApp is not running (for send): prompt user to open WhatsApp
- If permission denied on database: explain macOS Full Disk Access requirement
