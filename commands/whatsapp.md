# /whatsapp — WhatsApp CLI Slash Command

## Description

Control WhatsApp directly from Claude Code. Read chats, send messages, search conversations, monitor for new messages, set up auto-replies, and export chat history.

## Triggers

- `/whatsapp`

## Arguments

Parse the user's input after `/whatsapp` to determine the subcommand:

| Input | Subcommand |
|---|---|
| `/whatsapp` or `/whatsapp chats` | List recent chats |
| `/whatsapp unread` | Show unread chats with message counts |
| `/whatsapp read <name>` | Show last messages from a chat |
| `/whatsapp send <name> <message>` | Send a message to a contact or group |
| `/whatsapp search <query>` | Search messages across all chats |
| `/whatsapp groups` | List all groups |
| `/whatsapp export <name> <path>` | Export chat history to a file |
| `/whatsapp monitor <name>` | Watch a chat for new messages |
| `/whatsapp auto-reply <name>` | Set up an auto-reply for a contact |

## Auto-Install

Ensure the CLI is installed before running any command:

```bash
which whatsapp-cli || (cd ${CLAUDE_PLUGIN_ROOT}/agent-harness && python3 -m venv .venv && source .venv/bin/activate && pip install -e . && echo "whatsapp-cli installed")
```

## Execution

After parsing arguments, activate the virtual environment and run the appropriate CLI command:

```bash
source ${CLAUDE_PLUGIN_ROOT}/agent-harness/.venv/bin/activate 2>/dev/null || true
```

### List recent chats

```bash
whatsapp-cli chats
```

### Show unread chats

```bash
whatsapp-cli unread
```

### Read messages from a chat

```bash
whatsapp-cli read "<name>" --limit 50
```

The `<name>` argument is the contact name or group name. Use fuzzy matching if an exact match is not found.

### Send a message

```bash
whatsapp-cli send "<name>" "<message>"
```

Sends a message via WhatsApp's URL scheme, which opens the WhatsApp desktop app and pre-fills the message. The user may need to confirm sending.

### Search messages

```bash
whatsapp-cli search "<query>" --limit 20
```

### List groups

```bash
whatsapp-cli groups
```

### Export a chat

```bash
whatsapp-cli export "<name>" "<path>"
```

Exports the chat history to the specified file path. Supported formats: `.txt`, `.json`, `.csv`.

### Monitor a chat

```bash
whatsapp-cli monitor "<name>" --interval 5
```

Polls for new messages every N seconds and prints them as they arrive. Use Ctrl+C to stop.

### Auto-reply

```bash
whatsapp-cli auto-reply "<name>" --message "<reply>"
```

Monitors a chat and automatically replies with the specified message when a new message is received.

## Output Format

Present results in a clean, readable format:
- Chat lists: table with name, last message preview, timestamp, unread count
- Messages: chronological list with sender, timestamp, and content
- Search results: grouped by chat with highlighted matches
- Groups: table with name, member count, last activity

## Error Handling

- If WhatsApp desktop is not running, prompt the user to open it.
- If the WhatsApp database is not found, suggest checking that WhatsApp is installed and has been opened at least once.
- If a contact is not found, suggest similar names using fuzzy matching.
