# WhatsApp CLI

Control WhatsApp from Claude Code. Read chats, send messages, search conversations, manage groups, set up auto-replies, monitor contacts, and export chat history — all from your terminal.

## Features

- **Read chats** — View recent conversations and unread messages
- **Send messages** — Send messages to contacts and groups via WhatsApp's URL scheme
- **Search** — Full-text search across all conversations
- **Groups** — List and interact with WhatsApp groups
- **Auto-reply** — Set up automatic replies for specific contacts
- **Monitor** — Watch a chat in real-time for new messages
- **Export** — Export chat history to TXT, JSON, or CSV

## Requirements

- **macOS** (uses WhatsApp's local SQLite database)
- **WhatsApp desktop app** installed and logged in
- **Python 3.10+**
- **Full Disk Access** granted to your terminal (System Settings > Privacy & Security > Full Disk Access)

## Installation

### Via Claude Code Marketplace

```bash
claude plugin install whatsapp-cli
```

### Manual Installation

```bash
git clone https://github.com/marcelrgberger/whatsapp-cli.git
cd whatsapp-cli/agent-harness
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

## Usage

### Slash Command

Use the `/whatsapp` slash command in Claude Code:

```
/whatsapp                     — List recent chats
/whatsapp unread              — Show unread messages
/whatsapp read John           — Read messages from John
/whatsapp send John Hello!    — Send "Hello!" to John
/whatsapp search meeting      — Search for "meeting" across all chats
/whatsapp groups              — List all groups
/whatsapp export John ./chat  — Export chat with John
/whatsapp monitor John        — Watch John's chat for new messages
/whatsapp auto-reply John     — Set up auto-reply for John
```

### Natural Language

Just describe what you want in Claude Code:

- "Show my unread WhatsApp messages"
- "What did John say on WhatsApp?"
- "Send a WhatsApp message to John saying I'll be late"
- "Search my WhatsApp for the restaurant address"
- "Export my chat with the family group"

### Direct CLI

```bash
whatsapp-cli chats
whatsapp-cli unread
whatsapp-cli read "John Doe" --limit 50
whatsapp-cli send "John Doe" "Hello!"
whatsapp-cli search "meeting" --limit 20
whatsapp-cli groups
whatsapp-cli export "John Doe" ./chat.json --format json
whatsapp-cli monitor "John Doe" --interval 5
whatsapp-cli auto-reply "John Doe" --message "I'll get back to you soon"
whatsapp-cli status
```

## Command Reference

| Command | Description | Options |
|---|---|---|
| `chats` | List recent chats | `--limit N` |
| `unread` | Show unread chats with counts | — |
| `read <name>` | Read messages from a chat | `--limit N`, `--since DATE` |
| `send <name> <msg>` | Send a message | — |
| `search <query>` | Search messages | `--chat NAME`, `--limit N` |
| `groups` | List all groups | — |
| `export <name> <path>` | Export chat history | `--format txt\|json\|csv` |
| `monitor <name>` | Watch for new messages | `--interval N` |
| `auto-reply <name>` | Auto-reply to a contact | `--message MSG` |
| `status` | Check WhatsApp connection | — |

## Architecture

```
whatsapp-cli/
├── .claude-plugin/          # Claude Code plugin metadata
│   ├── plugin.json
│   └── marketplace.json
├── commands/
│   └── whatsapp.md          # /whatsapp slash command definition
├── skills/
│   └── whatsapp/
│       └── SKILL.md         # Natural language skill definition
├── agent-harness/
│   ├── setup.py             # Python package setup
│   └── whatsapp_cli/        # CLI source code
│       ├── __init__.py
│       ├── whatsapp_cli.py  # Main CLI entry point (Click)
│       ├── database.py      # SQLite database reader
│       ├── sender.py        # Message sending via URL scheme
│       ├── monitor.py       # Chat monitoring / polling
│       └── exporter.py      # Chat export (TXT, JSON, CSV)
├── README.md
├── LICENSE
└── .gitignore
```

### How It Works

- **Reading messages**: Reads directly from WhatsApp's local SQLite database (`ChatStorage.sqlite`) located in `~/Library/Group Containers/group.net.whatsapp.WhatsApp.shared/`. This is strictly read-only — the database is never modified.
- **Sending messages**: Uses WhatsApp's `whatsapp://send?phone=...&text=...` URL scheme, which opens the WhatsApp desktop app with a pre-filled message. The message is sent through official WhatsApp infrastructure.
- **Monitoring**: Polls the SQLite database at configurable intervals to detect new messages.
- **Auto-reply**: Combines monitoring with automatic URL scheme invocation when new messages are detected.

## Security

- **Read-only database access** — The CLI never writes to or modifies WhatsApp's database.
- **Official send mechanism** — Messages are sent via WhatsApp's URL scheme, going through the official WhatsApp desktop app and end-to-end encryption.
- **Local only** — No data leaves your machine. No external APIs or servers involved.
- **No credentials stored** — The CLI does not store any WhatsApp credentials or tokens.

## License

MIT License. See [LICENSE](LICENSE) for details.
