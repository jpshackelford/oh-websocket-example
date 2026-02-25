#!/usr/bin/env python3
"""
OpenHands V0 WebSocket Message CLI

A command-line utility to interact with OpenHands conversations via the V0 API.
Supports creating conversations, sending messages, and waking up sleeping conversations.

Usage:
    # Create a new conversation
    python oh_message_cli.py create --api-key <key> [--base-url <url>]

    # Send a message to existing conversation
    python oh_message_cli.py send -c <id> -m "Your message" --api-key <key>

    # List conversations
    python oh_message_cli.py list --api-key <key>
"""

import argparse
import asyncio
import json
import os
import sys
from urllib.parse import urljoin

import httpx
import socketio


class OpenHandsClient:
    """Client for interacting with OpenHands V0 API."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        timeout: float = 60.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self._headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

    async def create_conversation(
        self,
        initial_message: str | None = None,
        repository: str | None = None,
    ) -> dict:
        """Create a new V0 conversation."""
        url = f"{self.base_url}/api/conversations"
        payload = {}
        if initial_message:
            payload["initial_user_msg"] = initial_message
        if repository:
            payload["repository"] = repository

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(url, headers=self._headers, json=payload)
            response.raise_for_status()
            return response.json()

    async def get_conversation(self, conversation_id: str) -> dict | None:
        """Get conversation details including status."""
        url = f"{self.base_url}/api/conversations/{conversation_id}"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(url, headers=self._headers)
            if response.status_code == 404:
                return None
            response.raise_for_status()
            return response.json()

    async def list_conversations(self, limit: int = 20) -> dict:
        """List conversations."""
        url = f"{self.base_url}/api/conversations"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(
                url, headers=self._headers, params={"limit": limit}
            )
            response.raise_for_status()
            return response.json()

    async def start_conversation(self, conversation_id: str) -> dict:
        """Start/wake up a conversation via REST API."""
        url = f"{self.base_url}/api/conversations/{conversation_id}/start"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                url,
                headers=self._headers,
                json={"providers_set": []},
            )
            response.raise_for_status()
            return response.json()

    async def wait_for_conversation_ready(
        self,
        conversation_id: str,
        max_wait: float = 120.0,
        poll_interval: float = 2.0,
    ) -> dict:
        """Wait for conversation to be in RUNNING status with ready runtime."""
        import time

        start_time = time.time()
        while time.time() - start_time < max_wait:
            conversation = await self.get_conversation(conversation_id)
            if conversation is None:
                raise ValueError(f"Conversation {conversation_id} not found")

            status = conversation.get("status")
            runtime_status = conversation.get("runtime_status")

            print(
                f"  Conversation status: {status}, runtime_status: {runtime_status}",
                file=sys.stderr,
            )

            # Check if conversation is ready
            if status == "RUNNING" and runtime_status and "READY" in runtime_status:
                return conversation

            if status == "ERROR":
                raise RuntimeError(f"Conversation is in error state: {conversation}")

            await asyncio.sleep(poll_interval)

        raise TimeoutError(
            f"Conversation did not become ready within {max_wait} seconds"
        )


class WebSocketMessageSender:
    """Send messages via Socket.IO WebSocket."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        conversation_id: str,
        conversation_url: str | None = None,
        session_api_key: str | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.conversation_id = conversation_id
        self.conversation_url = conversation_url
        self.session_api_key = session_api_key
        self.sio: socketio.AsyncClient | None = None
        self._connected = asyncio.Event()
        self._message_sent = asyncio.Event()
        self._error: str | None = None

    async def connect(self) -> None:
        """Connect to the Socket.IO server."""
        self.sio = socketio.AsyncClient(logger=False, engineio_logger=False)

        @self.sio.event
        async def connect():
            print("  WebSocket connected", file=sys.stderr)
            self._error = None  # Clear any previous errors on successful connect
            self._connected.set()

        @self.sio.event
        async def disconnect():
            print("  WebSocket disconnected", file=sys.stderr)

        @self.sio.event
        async def connect_error(data):
            print(f"  WebSocket connection error: {data}", file=sys.stderr)
            self._error = str(data)
            self._connected.set()

        @self.sio.event
        async def oh_event(data):
            # Log events for debugging
            event_type = data.get("type", data.get("action", "unknown"))
            source = data.get("source", "unknown")
            print(f"  Received event: {event_type} from {source}", file=sys.stderr)

        # Determine the WebSocket URL - must match frontend logic exactly
        from urllib.parse import urlparse

        if self.conversation_url and not self.conversation_url.startswith("/"):
            # Use the conversation-specific URL
            parsed = urlparse(self.conversation_url)
            # Frontend uses just the host (not scheme://host)
            ws_host = parsed.netloc
            path_before_api = parsed.path.split("/api/conversations")[0] or "/"
            # Socket.IO server default path is /socket.io; prefix with pathBeforeApi for path mode
            socket_path = f"{path_before_api.rstrip('/')}/socket.io"
        else:
            parsed = urlparse(self.base_url)
            ws_host = parsed.netloc
            socket_path = "/socket.io"

        # Build query parameters - matching frontend exactly
        query = {
            "latest_event_id": -1,
            "conversation_id": self.conversation_id,
        }
        if self.session_api_key:
            query["session_api_key"] = self.session_api_key

        # Build query string for URL - python-socketio needs query params in URL
        query_str = "&".join(f"{k}={v}" for k, v in query.items())
        url_with_query = f"https://{ws_host}?{query_str}"

        print(f"  Connecting to {url_with_query} with path {socket_path}", file=sys.stderr)

        await self.sio.connect(
            url_with_query,
            transports=["websocket"],
            socketio_path=socket_path,
            headers={"Authorization": f"Bearer {self.api_key}"},
            wait_timeout=30,
        )

        # Wait for connection
        await asyncio.wait_for(self._connected.wait(), timeout=30.0)

        if self._error:
            raise ConnectionError(f"Failed to connect: {self._error}")

    async def send_message(self, content: str) -> None:
        """Send a message action via WebSocket."""
        if not self.sio or not self.sio.connected:
            raise RuntimeError("Not connected to WebSocket")

        # Create the message action in the format expected by V0 API
        message_action = {
            "action": "message",
            "args": {
                "content": content,
                "wait_for_response": False,
            },
        }

        print(f"  Sending message action...", file=sys.stderr)
        await self.sio.emit("oh_user_action", message_action)
        print("  Message sent successfully!", file=sys.stderr)

    async def disconnect(self) -> None:
        """Disconnect from the WebSocket."""
        if self.sio:
            await self.sio.disconnect()


async def create_conversation_cmd(
    base_url: str,
    api_key: str,
    initial_message: str | None = None,
    repository: str | None = None,
) -> None:
    """Create a new V0 conversation."""
    client = OpenHandsClient(base_url, api_key)

    print("Creating new V0 conversation...", file=sys.stderr)
    result = await client.create_conversation(
        initial_message=initial_message,
        repository=repository,
    )

    conversation_id = result.get("conversation_id")
    status = result.get("status")
    conversation_status = result.get("conversation_status")

    print(f"\nConversation created!", file=sys.stderr)
    print(f"  ID: {conversation_id}", file=sys.stderr)
    print(f"  Status: {status}", file=sys.stderr)
    print(f"  Conversation Status: {conversation_status}", file=sys.stderr)

    # Output just the conversation ID to stdout for easy scripting
    print(conversation_id)


async def list_conversations_cmd(
    base_url: str,
    api_key: str,
    limit: int = 20,
) -> None:
    """List conversations."""
    client = OpenHandsClient(base_url, api_key)

    print(f"Listing conversations (limit={limit})...", file=sys.stderr)
    result = await client.list_conversations(limit=limit)

    conversations = result.get("results", [])
    print(f"\nFound {len(conversations)} conversations:\n", file=sys.stderr)

    for conv in conversations:
        conv_id = conv.get("conversation_id", "N/A")
        title = conv.get("title", "Untitled")
        status = conv.get("status", "UNKNOWN")
        version = conv.get("conversation_version", "V0")
        created = conv.get("created_at", "N/A")

        print(f"  [{version}] {conv_id}", file=sys.stderr)
        print(f"       Title: {title}", file=sys.stderr)
        print(f"       Status: {status}", file=sys.stderr)
        print(f"       Created: {created}", file=sys.stderr)
        print("", file=sys.stderr)


async def send_message_cmd(
    base_url: str,
    api_key: str,
    conversation_id: str,
    message: str,
    wait_timeout: float = 120.0,
) -> None:
    """
    Send a message to a conversation.

    If the conversation is not running, it will be started first.
    """
    client = OpenHandsClient(base_url, api_key)

    # Step 1: Get conversation status
    print(f"Checking conversation {conversation_id}...", file=sys.stderr)
    conversation = await client.get_conversation(conversation_id)

    if conversation is None:
        raise ValueError(f"Conversation {conversation_id} not found")

    status = conversation.get("status")
    runtime_status = conversation.get("runtime_status")
    conversation_url = conversation.get("url")
    session_api_key = conversation.get("session_api_key")

    print(
        f"  Current status: {status}, runtime_status: {runtime_status}",
        file=sys.stderr,
    )

    # Step 2: Wake up conversation if needed
    if status != "RUNNING" or not runtime_status or "READY" not in str(runtime_status):
        print(f"Starting conversation...", file=sys.stderr)
        start_response = await client.start_conversation(conversation_id)
        print(f"  Start response: {start_response}", file=sys.stderr)

        # Wait for conversation to be ready
        print("Waiting for conversation to be ready...", file=sys.stderr)
        conversation = await client.wait_for_conversation_ready(
            conversation_id, max_wait=wait_timeout
        )
        conversation_url = conversation.get("url")
        session_api_key = conversation.get("session_api_key")

    # Step 3: Connect via WebSocket and send message
    print("Connecting via WebSocket...", file=sys.stderr)
    sender = WebSocketMessageSender(
        base_url=base_url,
        api_key=api_key,
        conversation_id=conversation_id,
        conversation_url=conversation_url,
        session_api_key=session_api_key,
    )

    try:
        await sender.connect()
        await sender.send_message(message)
        # Give some time for the message to be processed
        await asyncio.sleep(1.0)
    finally:
        await sender.disconnect()

    print(f"\nMessage successfully sent to conversation {conversation_id}", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(
        description="OpenHands V0 API CLI - Create conversations and send messages",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    # Global arguments
    parser.add_argument(
        "--api-key",
        default=os.environ.get("OH_API_KEY"),
        help="OpenHands API key (default: from OH_API_KEY environment variable)",
    )
    parser.add_argument(
        "--base-url",
        default=os.environ.get("OH_BASE_URL", "https://app.all-hands.dev"),
        help="OpenHands server base URL (default: https://app.all-hands.dev)",
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # Create command
    create_parser = subparsers.add_parser("create", help="Create a new V0 conversation")
    create_parser.add_argument(
        "-m", "--message",
        help="Initial message for the conversation",
    )
    create_parser.add_argument(
        "-r", "--repository",
        help="Repository to associate with the conversation (e.g., owner/repo)",
    )

    # List command
    list_parser = subparsers.add_parser("list", help="List conversations")
    list_parser.add_argument(
        "-l", "--limit",
        type=int,
        default=20,
        help="Maximum number of conversations to list (default: 20)",
    )

    # Send command
    send_parser = subparsers.add_parser("send", help="Send a message to a conversation")
    send_parser.add_argument(
        "-c", "--conversation-id",
        required=True,
        help="The conversation ID to send the message to",
    )
    send_parser.add_argument(
        "-m", "--message",
        required=True,
        help="The message content to send",
    )
    send_parser.add_argument(
        "--wait-timeout",
        type=float,
        default=120.0,
        help="Maximum time to wait for conversation to start (default: 120 seconds)",
    )

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    if not args.api_key:
        parser.error(
            "API key is required. Set OH_API_KEY environment variable or use --api-key"
        )

    try:
        if args.command == "create":
            asyncio.run(
                create_conversation_cmd(
                    base_url=args.base_url,
                    api_key=args.api_key,
                    initial_message=args.message,
                    repository=args.repository,
                )
            )
        elif args.command == "list":
            asyncio.run(
                list_conversations_cmd(
                    base_url=args.base_url,
                    api_key=args.api_key,
                    limit=args.limit,
                )
            )
        elif args.command == "send":
            asyncio.run(
                send_message_cmd(
                    base_url=args.base_url,
                    api_key=args.api_key,
                    conversation_id=args.conversation_id,
                    message=args.message,
                    wait_timeout=args.wait_timeout,
                )
            )
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
