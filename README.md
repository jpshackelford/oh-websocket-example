# oh-websocket-example

Example use of the OpenHands **V0 WebSocket API** for programmatic conversation management.

> **Note:** This CLI uses the legacy V0 API endpoints which are deprecated since version 1.0.0 and scheduled for removal April 1, 2026. The V0 API uses Socket.IO for WebSocket communication.

## Features

- **Create** new V0 conversations via REST API
- **List** existing conversations  
- **Send messages** to conversations via Socket.IO WebSocket
- **Automatically wake up** sleeping conversations before sending messages

## Installation

```bash
pip install -r requirements.txt
```

## Usage

### Set your API key

```bash
export OH_API_KEY="your-api-key"
```

Or pass it with `--api-key` on each command.

### Create a new conversation

```bash
python oh_message_cli.py create
# Output: conversation_id

# With an initial message
python oh_message_cli.py create -m "Hello, please help me with..."

# With a repository
python oh_message_cli.py create -r "owner/repo"
```

### List conversations

```bash
python oh_message_cli.py list
python oh_message_cli.py list -l 50  # limit to 50
```

### Send a message to a conversation

```bash
python oh_message_cli.py send -c <conversation_id> -m "Your message here"
```

If the conversation is stopped/sleeping, the CLI will:
1. Call `POST /api/conversations/{id}/start` to wake it up
2. Wait for the conversation to reach `RUNNING` status with `runtime_status: STATUS$READY`
3. Connect via Socket.IO WebSocket
4. Send the message via `oh_user_action` event

## V0 API Endpoints Used

| Endpoint | Purpose |
|----------|---------|
| `POST /api/conversations` | Create a new conversation |
| `GET /api/conversations` | List conversations |
| `GET /api/conversations/{id}` | Get conversation details |
| `POST /api/conversations/{id}/start` | Start/wake up a conversation |
| Socket.IO `/socket.io` | WebSocket for real-time events |

## WebSocket Protocol (V0)

The V0 API uses **Socket.IO** for WebSocket communication:

- **Connection URL:** `https://{runtime-host}?conversation_id={id}&latest_event_id=-1&session_api_key={key}`
- **Path:** `/socket.io`
- **Send events:** `oh_user_action` with action payload
- **Receive events:** `oh_event` with event data

### Message Action Format

```json
{
  "action": "message",
  "args": {
    "content": "Your message here",
    "wait_for_response": false
  }
}
```

## Options

| Option | Environment Variable | Default | Description |
|--------|---------------------|---------|-------------|
| `--api-key` | `OH_API_KEY` | - | OpenHands API key (required) |
| `--base-url` | `OH_BASE_URL` | `https://app.all-hands.dev` | Server base URL |
| `--wait-timeout` | - | 120 | Seconds to wait for conversation to start |

## License

MIT License - see [LICENSE](LICENSE) for details.
