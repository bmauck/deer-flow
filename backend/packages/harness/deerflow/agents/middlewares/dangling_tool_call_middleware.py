"""Middleware to fix dangling and misordered tool calls in message history.

Handles two cases that cause Anthropic API 400 errors:
1. Dangling tool calls: AIMessage has tool_calls with no corresponding ToolMessage
   (e.g., due to user interruption or request cancellation).
2. Misordered tool results: ToolMessages exist but are not immediately after their
   AIMessage (e.g., due to summarization inserting a summary between them, or
   loop detection injecting a warning message).

This middleware intercepts the model call to reorder messages so that every
AIMessage with tool_calls is immediately followed by its ToolMessages, and
injects synthetic error ToolMessages for any truly missing results.
"""

import logging
from collections.abc import Awaitable, Callable
from typing import override

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelCallResult, ModelRequest, ModelResponse
from langchain_core.messages import ToolMessage

logger = logging.getLogger(__name__)


class DanglingToolCallMiddleware(AgentMiddleware[AgentState]):
    """Fixes tool call ordering and injects placeholders for missing tool results.

    Ensures every AIMessage with tool_calls is immediately followed by
    the corresponding ToolMessages in the correct order for the Anthropic API.
    """

    def _build_patched_messages(self, messages: list) -> list | None:
        """Return a reordered message list with proper tool_use/tool_result adjacency.

        1. Collects all ToolMessages into a lookup by tool_call_id.
        2. Walks messages; after each AI message with tool_calls, pulls the
           matching ToolMessages to be immediately after it (removing them
           from their original positions). Missing results get a synthetic
           error ToolMessage.
        3. Any non-AI, non-Tool messages (human, system) keep their relative
           order but don't break tool_use/tool_result pairs.

        Returns None if no reordering or patching is needed.
        """
        # Build lookup: tool_call_id -> ToolMessage
        tool_msg_by_id: dict[str, object] = {}
        for msg in messages:
            if isinstance(msg, ToolMessage):
                tool_msg_by_id[msg.tool_call_id] = msg

        # Collect tool_call_ids from all AI messages
        all_tc_ids: set[str] = set()
        for msg in messages:
            if getattr(msg, "type", None) != "ai":
                continue
            for tc in getattr(msg, "tool_calls", None) or []:
                tc_id = tc.get("id")
                if tc_id:
                    all_tc_ids.add(tc_id)

        if not all_tc_ids:
            return None

        # Track which ToolMessages are "claimed" by an AI message
        # (will be placed right after their AI message)
        claimed_tool_msg_ids: set[str] = set()
        for tc_id in all_tc_ids:
            if tc_id in tool_msg_by_id:
                claimed_tool_msg_ids.add(tc_id)

        # Check if reordering is needed: for each AI message with tool_calls,
        # are the ToolMessages immediately after it?
        needs_patch = False
        for i, msg in enumerate(messages):
            if getattr(msg, "type", None) != "ai":
                continue
            tool_calls = getattr(msg, "tool_calls", None) or []
            if not tool_calls:
                continue
            tc_ids = [tc.get("id") for tc in tool_calls if tc.get("id")]
            if not tc_ids:
                continue
            # Check that the next len(tc_ids) messages are the matching ToolMessages
            expected_pos = i + 1
            for tc_id in tc_ids:
                if tc_id not in tool_msg_by_id:
                    needs_patch = True  # missing result
                    break
                if expected_pos >= len(messages):
                    needs_patch = True
                    break
                next_msg = messages[expected_pos]
                if not isinstance(next_msg, ToolMessage) or next_msg.tool_call_id != tc_id:
                    needs_patch = True
                    break
                expected_pos += 1
            if needs_patch:
                break

        if not needs_patch:
            return None

        # Rebuild: place ToolMessages immediately after their AI message
        patched: list = []
        patch_count = 0
        reorder_count = 0
        for msg in messages:
            # Skip ToolMessages that belong to an AI message — they'll be
            # placed after their AI message instead
            if isinstance(msg, ToolMessage) and msg.tool_call_id in claimed_tool_msg_ids:
                continue

            patched.append(msg)

            if getattr(msg, "type", None) != "ai":
                continue

            for tc in getattr(msg, "tool_calls", None) or []:
                tc_id = tc.get("id")
                if not tc_id:
                    continue
                if tc_id in tool_msg_by_id:
                    patched.append(tool_msg_by_id[tc_id])
                    reorder_count += 1
                else:
                    patched.append(
                        ToolMessage(
                            content="[Tool call was interrupted and did not return a result.]",
                            tool_call_id=tc_id,
                            name=tc.get("name", "unknown"),
                            status="error",
                        )
                    )
                    patch_count += 1

        if patch_count:
            logger.warning(f"Injected {patch_count} placeholder ToolMessage(s) for dangling tool calls")
        if reorder_count:
            logger.warning(f"Reordered {reorder_count} ToolMessage(s) to fix adjacency")
        return patched

    @override
    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelCallResult:
        patched = self._build_patched_messages(request.messages)
        if patched is not None:
            request = request.override(messages=patched)
        return handler(request)

    @override
    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelCallResult:
        patched = self._build_patched_messages(request.messages)
        if patched is not None:
            request = request.override(messages=patched)
        return await handler(request)
