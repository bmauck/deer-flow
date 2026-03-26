"""Middleware for intercepting clarification requests and presenting them to the user."""

from collections.abc import Callable
from typing import override

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage
from langgraph.graph import END
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command

# Maximum number of clarification interrupts allowed per thread run before
# the middleware stops intercepting and lets the model proceed.  This prevents
# infinite loops where the model keeps asking the same clarification.
MAX_CLARIFICATIONS_PER_RUN = 1


class ClarificationMiddlewareState(AgentState):
    """Compatible with the `ThreadState` schema."""

    pass


class ClarificationMiddleware(AgentMiddleware[ClarificationMiddlewareState]):
    """Intercepts clarification tool calls and interrupts execution to present questions to the user.

    Includes a loop guard: if a clarification has already been asked in the
    current message history (i.e. the model is re-asking after the user already
    responded), the middleware skips interception and returns a ToolMessage
    telling the model to proceed without further clarification.
    """

    state_schema = ClarificationMiddlewareState

    def _is_chinese(self, text: str) -> bool:
        """Check if text contains Chinese characters.

        Args:
            text: Text to check

        Returns:
            True if text contains Chinese characters
        """
        return any("\u4e00" <= char <= "\u9fff" for char in text)

    def _format_clarification_message(self, args: dict) -> str:
        """Format the clarification arguments into a user-friendly message.

        Args:
            args: The tool call arguments containing clarification details

        Returns:
            Formatted message string
        """
        question = args.get("question", "")
        clarification_type = args.get("clarification_type", "missing_info")
        context = args.get("context")
        options = args.get("options", [])

        # Type-specific icons
        type_icons = {
            "missing_info": "❓",
            "ambiguous_requirement": "🤔",
            "approach_choice": "🔀",
            "risk_confirmation": "⚠️",
            "suggestion": "💡",
        }

        icon = type_icons.get(clarification_type, "❓")

        # Build the message naturally
        message_parts = []

        # Add icon and question together for a more natural flow
        if context:
            # If there's context, present it first as background
            message_parts.append(f"{icon} {context}")
            message_parts.append(f"\n{question}")
        else:
            # Just the question with icon
            message_parts.append(f"{icon} {question}")

        # Add options in a cleaner format
        if options and len(options) > 0:
            message_parts.append("")  # blank line for spacing
            for i, option in enumerate(options, 1):
                message_parts.append(f"  {i}. {option}")

        return "\n".join(message_parts)

    def _handle_clarification(self, request: ToolCallRequest) -> Command:
        """Handle clarification request and return command to interrupt execution.

        Args:
            request: Tool call request

        Returns:
            Command that interrupts execution with the formatted clarification message
        """
        # Extract clarification arguments
        args = request.tool_call.get("args", {})
        question = args.get("question", "")

        print("[ClarificationMiddleware] Intercepted clarification request")
        print(f"[ClarificationMiddleware] Question: {question}")

        # Format the clarification message
        formatted_message = self._format_clarification_message(args)

        # Get the tool call ID
        tool_call_id = request.tool_call.get("id", "")

        # Create a ToolMessage with the formatted question
        # This will be added to the message history
        tool_message = ToolMessage(
            content=formatted_message,
            tool_call_id=tool_call_id,
            name="ask_clarification",
        )

        # Return a Command that:
        # 1. Adds the formatted tool message
        # 2. Interrupts execution by going to __end__
        # Note: We don't add an extra AIMessage here - the frontend will detect
        # and display ask_clarification tool messages directly
        return Command(
            update={"messages": [tool_message]},
            goto=END,
        )

    def _already_clarified(self, request: ToolCallRequest) -> bool:
        """Check if clarification has already been asked in this thread's message history.

        Counts existing ask_clarification ToolMessages. If we've already hit the
        limit, the model is looping — tell it to proceed instead.
        """
        messages = getattr(request, "state", {}).get("messages", [])
        count = sum(
            1
            for m in messages
            if isinstance(m, (dict, ToolMessage))
            and (m.get("name") if isinstance(m, dict) else getattr(m, "name", None)) == "ask_clarification"
        )
        return count >= MAX_CLARIFICATIONS_PER_RUN

    def _skip_clarification(self, request: ToolCallRequest) -> ToolMessage:
        """Return a ToolMessage that tells the model to stop clarifying and just proceed."""
        tool_call_id = request.tool_call.get("id", "")
        print("[ClarificationMiddleware] Loop guard triggered — skipping repeat clarification")
        return ToolMessage(
            content="Clarification already asked. Proceed with your best judgment — do not ask again.",
            tool_call_id=tool_call_id,
            name="ask_clarification",
        )

    @override
    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command],
    ) -> ToolMessage | Command:
        if request.tool_call.get("name") != "ask_clarification":
            return handler(request)

        if self._already_clarified(request):
            return self._skip_clarification(request)

        return self._handle_clarification(request)

    @override
    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command],
    ) -> ToolMessage | Command:
        if request.tool_call.get("name") != "ask_clarification":
            return await handler(request)

        if self._already_clarified(request):
            return self._skip_clarification(request)

        return self._handle_clarification(request)
