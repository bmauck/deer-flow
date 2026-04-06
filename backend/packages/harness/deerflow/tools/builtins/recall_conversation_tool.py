"""Tool for lazy-loading messages from previous conversations."""

import datetime
import json
import logging
from pathlib import Path
from typing import Annotated

from langchain.tools import InjectedToolCallId, ToolRuntime, tool
from langgraph.typing import ContextT

from deerflow.agents.thread_state import ThreadState

logger = logging.getLogger(__name__)

# Maximum number of human/AI messages to return from a previous thread.
_MAX_MESSAGES = 20


def _read_previous_threads(channel_name: str, chat_id: str, limit: int = 5) -> list[dict]:
    """Read previous thread_ids from the channel store JSON file."""
    from deerflow.config.paths import get_paths

    store_path = Path(get_paths().base_dir) / "channels" / "store.json"
    if not store_path.exists():
        return []
    try:
        data = json.loads(store_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []

    key = f"{channel_name}:{chat_id}"
    entry = data.get(key)
    if not entry:
        return []

    prev = entry.get("previous_threads", [])
    return list(reversed(prev[-limit:]))


def _format_messages(messages: list[dict], max_messages: int = _MAX_MESSAGES) -> str:
    """Extract the last N human/AI messages, skipping tool calls and system messages."""
    filtered = []
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        msg_type = msg.get("type")
        if msg_type not in ("human", "ai"):
            continue
        # Skip AI messages that are just tool calls with no text
        if msg_type == "ai":
            content = msg.get("content", "")
            if not content or (isinstance(content, list) and not any(
                (isinstance(b, dict) and b.get("type") == "text" and b.get("text"))
                or isinstance(b, str)
                for b in content
            )):
                continue
        filtered.append(msg)

    # Take the last N messages
    filtered = filtered[-max_messages:]

    lines = []
    for msg in filtered:
        role = "User" if msg["type"] == "human" else "Assistant"
        content = msg.get("content", "")
        if isinstance(content, list):
            parts = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    parts.append(block.get("text", ""))
                elif isinstance(block, str):
                    parts.append(block)
            content = "".join(parts)
        # Truncate very long messages
        if len(content) > 500:
            content = content[:500] + "..."
        lines.append(f"**{role}:** {content}")

    return "\n\n".join(lines) if lines else "(no messages found)"


@tool("recall_conversation", parse_docstring=True)
def recall_conversation_tool(
    runtime: ToolRuntime[ContextT, ThreadState],
    tool_call_id: Annotated[str, InjectedToolCallId],
    offset: int = 0,
) -> str:
    """Recall messages from a previous conversation with this user.

    Use this tool when the user references a past conversation — e.g. "last time",
    "our previous conversation", "what we were working on before", "what went wrong
    earlier", etc. This loads the actual messages from the prior thread so you can
    see what happened.

    Args:
        offset: Which previous conversation to load. 0 = most recent previous conversation, 1 = the one before that, etc.
    """
    context = runtime.context or {}
    channel_name = context.get("channel_name")
    chat_id = context.get("chat_id")

    if not channel_name or not chat_id:
        return "Cannot recall previous conversations: channel context not available."

    # Look up previous threads from the channel store JSON file.
    try:
        previous = _read_previous_threads(channel_name, chat_id, limit=5)
    except Exception as e:
        logger.error("Failed to look up previous threads: %s", e)
        return f"Failed to look up previous conversations: {e}"

    if not previous:
        return "No previous conversations found for this chat."

    if offset >= len(previous):
        return f"Only {len(previous)} previous conversation(s) available. Use offset 0-{len(previous) - 1}."

    target = previous[offset]
    target_thread_id = target["thread_id"]
    archived_at = target.get("archived_at", "unknown")

    # Fetch the thread state from the LangGraph server.
    try:
        from langgraph_sdk import get_sync_client

        client = get_sync_client(url="http://localhost:2024")
        state = client.threads.get_state(target_thread_id)
    except Exception as e:
        logger.error("Failed to fetch thread state for %s: %s", target_thread_id, e)
        return f"Previous conversation exists (thread {target_thread_id}) but could not load its messages: {e}"

    messages = []
    if isinstance(state, dict):
        values = state.get("values", state)
        messages = values.get("messages", [])

    if not messages:
        return f"Previous conversation (thread {target_thread_id}) has no messages."

    formatted = _format_messages(messages)

    ts = ""
    if isinstance(archived_at, (int, float)):
        ts = datetime.datetime.fromtimestamp(archived_at).strftime(" (%Y-%m-%d %H:%M)")

    return (
        f"## Previous conversation{ts}\n"
        f"Thread: {target_thread_id}\n\n"
        f"{formatted}"
    )
