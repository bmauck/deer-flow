"""Middleware to detect and break repetitive tool call loops.

P0 safety: prevents the agent from calling the same tool with the same
arguments indefinitely until the recursion limit kills the run.

Detection strategy:
  1. After each model response, hash the tool calls (name + args).
  2. Track recent hashes in a sliding window.
  3. If the same hash appears >= warn_threshold times, inject a
     "you are repeating yourself — wrap up" system message (once per hash).
  4. If it appears >= hard_limit times, strip all tool_calls from the
     response so the agent is forced to produce a final text answer.
"""

import hashlib
import json
import logging
import threading
from collections import OrderedDict, defaultdict
from typing import override

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import HumanMessage
from langgraph.runtime import Runtime

logger = logging.getLogger(__name__)

# Defaults — can be overridden via constructor
_DEFAULT_WARN_THRESHOLD = 3  # inject warning after 3 identical calls
_DEFAULT_HARD_LIMIT = 5  # force-stop after 5 identical calls
_DEFAULT_WINDOW_SIZE = 20  # track last N tool calls
_DEFAULT_MAX_TRACKED_THREADS = 100  # LRU eviction limit
_DEFAULT_TOTAL_CALL_WARN = 70  # warn after 70 total model→tool rounds
_DEFAULT_TOTAL_CALL_LIMIT = 90  # hard stop after 90 total rounds (~180 steps, before recursion_limit=200)


def _hash_tool_calls(tool_calls: list[dict]) -> str:
    """Deterministic hash of a set of tool calls (name + args).

    This is intended to be order-independent: the same multiset of tool calls
    should always produce the same hash, regardless of their input order.
    """
    # First normalize each tool call to a minimal (name, args) structure.
    normalized: list[dict] = []
    for tc in tool_calls:
        normalized.append(
            {
                "name": tc.get("name", ""),
                "args": tc.get("args", {}),
            }
        )

    # Sort by both name and a deterministic serialization of args so that
    # permutations of the same multiset of calls yield the same ordering.
    normalized.sort(
        key=lambda tc: (
            tc["name"],
            json.dumps(tc["args"], sort_keys=True, default=str),
        )
    )
    blob = json.dumps(normalized, sort_keys=True, default=str)
    return hashlib.md5(blob.encode()).hexdigest()[:12]


_WARNING_MSG = (
    "[LOOP DETECTED] You are repeating the same tool calls. "
    "Stop calling tools and produce your final answer now. "
    "If you cannot complete the task, summarize what you accomplished so far."
)

_HARD_STOP_MSG = (
    "[FORCED STOP] Repeated tool calls exceeded the safety limit. "
    "Producing final answer with results collected so far."
)

_BUDGET_WARNING_MSG = (
    "[BUDGET WARNING] You have used most of your tool-call budget for this turn. "
    "Wrap up now: summarize what you have so far and produce your final answer. "
    "Do not start new searches or tool calls unless absolutely necessary."
)

_BUDGET_STOP_MSG = (
    "[BUDGET EXCEEDED] Tool-call budget exhausted. "
    "Producing final answer with results collected so far."
)


class LoopDetectionMiddleware(AgentMiddleware[AgentState]):
    """Detects and breaks repetitive tool call loops.

    Args:
        warn_threshold: Number of identical tool call sets before injecting
            a warning message. Default: 3.
        hard_limit: Number of identical tool call sets before stripping
            tool_calls entirely. Default: 5.
        window_size: Size of the sliding window for tracking calls.
            Default: 20.
        max_tracked_threads: Maximum number of threads to track before
            evicting the least recently used. Default: 100.
    """

    def __init__(
        self,
        warn_threshold: int = _DEFAULT_WARN_THRESHOLD,
        hard_limit: int = _DEFAULT_HARD_LIMIT,
        window_size: int = _DEFAULT_WINDOW_SIZE,
        max_tracked_threads: int = _DEFAULT_MAX_TRACKED_THREADS,
    ):
        super().__init__()
        self.warn_threshold = warn_threshold
        self.hard_limit = hard_limit
        self.window_size = window_size
        self.max_tracked_threads = max_tracked_threads
        self.total_call_warn = _DEFAULT_TOTAL_CALL_WARN
        self.total_call_limit = _DEFAULT_TOTAL_CALL_LIMIT
        self._lock = threading.Lock()
        # Per-thread tracking using OrderedDict for LRU eviction
        self._history: OrderedDict[str, list[str]] = OrderedDict()
        self._warned: dict[str, set[str]] = defaultdict(set)
        self._total_warned: set[str] = set()

    def _get_thread_id(self, runtime: Runtime) -> str:
        """Extract thread_id from runtime context for per-thread tracking."""
        thread_id = runtime.context.get("thread_id") if runtime.context else None
        if thread_id:
            return thread_id
        return "default"

    def _evict_if_needed(self) -> None:
        """Evict least recently used threads if over the limit.

        Must be called while holding self._lock.
        """
        while len(self._history) > self.max_tracked_threads:
            evicted_id, _ = self._history.popitem(last=False)
            self._warned.pop(evicted_id, None)
            logger.debug("Evicted loop tracking for thread %s (LRU)", evicted_id)

    def _track_and_check(self, state: AgentState, runtime: Runtime) -> tuple[str | None, bool]:
        """Track tool calls and check for repetitive loops.

        Two detection layers:
          1. **Identical-call detection**: catches the agent calling the same tool
             with the same args repeatedly (warn at warn_threshold, stop at hard_limit).
          2. **Total-round budget**: counts all tool-call rounds (regardless of
             uniqueness) and forces a graceful stop before the LangGraph
             recursion_limit kills the run with no output.

        Returns:
            (warning_message_or_none, should_hard_stop)
        """
        messages = state.get("messages", [])
        if not messages:
            return None, False

        last_msg = messages[-1]
        if getattr(last_msg, "type", None) != "ai":
            return None, False

        tool_calls = getattr(last_msg, "tool_calls", None)
        if not tool_calls:
            return None, False

        thread_id = self._get_thread_id(runtime)
        call_hash = _hash_tool_calls(tool_calls)
        tool_names = [tc.get("name", "?") for tc in tool_calls]

        with self._lock:
            # Touch / create entry (move to end for LRU)
            if thread_id in self._history:
                self._history.move_to_end(thread_id)
            else:
                self._history[thread_id] = []
                self._evict_if_needed()

            history = self._history[thread_id]
            history.append(call_hash)
            if len(history) > self.window_size:
                history[:] = history[-self.window_size:]

            # --- Identical-call detection ---
            count = history.count(call_hash)

            if count >= self.hard_limit:
                logger.error(
                    "Loop hard limit reached — forcing stop (same call %d times)",
                    count,
                    extra={
                        "thread_id": thread_id,
                        "call_hash": call_hash,
                        "count": count,
                        "tools": tool_names,
                    },
                )
                return _HARD_STOP_MSG, True

            if count >= self.warn_threshold:
                warned = self._warned[thread_id]
                if call_hash not in warned:
                    warned.add(call_hash)
                    logger.warning(
                        "Repetitive tool calls detected (%d repeats) — logging only",
                        count,
                        extra={
                            "thread_id": thread_id,
                            "call_hash": call_hash,
                            "count": count,
                            "tools": tool_names,
                        },
                    )

            # --- Total-round budget (per-turn, stateless) ---
            # Count AI messages with tool_calls after the last HumanMessage.
            # This resets naturally each turn without needing external state.
            total = 0
            for msg in reversed(messages[:-1]):  # exclude current (already counted above)
                if getattr(msg, "type", None) == "human":
                    break
                if getattr(msg, "type", None) == "ai" and getattr(msg, "tool_calls", None):
                    total += 1
            total += 1  # count current round

            # Reset warning flag at the start of a new turn so it can fire again
            if total == 1:
                self._total_warned.discard(thread_id)

            if total >= self.total_call_limit:
                logger.error(
                    "Total tool-call rounds (%d) hit budget limit — forcing stop",
                    total,
                    extra={"thread_id": thread_id, "tools": tool_names},
                )
                return _BUDGET_STOP_MSG, True

            if total >= self.total_call_warn:
                # Only warn once per turn: check if we already warned in a previous round
                if thread_id not in self._total_warned:
                    self._total_warned.add(thread_id)
                    logger.warning(
                        "Approaching tool-call budget (%d/%d rounds used)",
                        total,
                        self.total_call_limit,
                        extra={"thread_id": thread_id, "tools": tool_names},
                    )
                    return _BUDGET_WARNING_MSG, False

        return None, False

    def _apply(self, state: AgentState, runtime: Runtime) -> dict | None:
        warning, hard_stop = self._track_and_check(state, runtime)

        if hard_stop:
            # Strip tool_calls from the last AIMessage to force text output.
            # The warning message (loop or budget) is appended to the content
            # so the model sees it and produces a final answer.
            messages = state.get("messages", [])
            last_msg = messages[-1]
            existing = last_msg.content or ""
            stop_msg = warning  # warning contains the actual stop message text
            if isinstance(existing, list):
                updated_content = existing + [{"type": "text", "text": f"\n\n{stop_msg}"}]
            else:
                updated_content = existing + f"\n\n{stop_msg}"
            stripped_msg = last_msg.model_copy(update={
                "tool_calls": [],
                "content": updated_content,
            })
            return {"messages": [stripped_msg]}

        # Warning-only case: do NOT modify messages. Injecting a
        # HumanMessage or patching the AI message's content both break
        # Anthropic's requirement that tool_result blocks appear
        # immediately after tool_use blocks. The warning is already
        # logged above; the hard_stop at total_call_limit will enforce
        # termination if the agent keeps going.

        return None

    @override
    def after_model(self, state: AgentState, runtime: Runtime) -> dict | None:
        return self._apply(state, runtime)

    @override
    async def aafter_model(self, state: AgentState, runtime: Runtime) -> dict | None:
        return self._apply(state, runtime)

    def reset(self, thread_id: str | None = None) -> None:
        """Clear tracking state. If thread_id given, clear only that thread."""
        with self._lock:
            if thread_id:
                self._history.pop(thread_id, None)
                self._warned.pop(thread_id, None)
                self._total_warned.discard(thread_id)
            else:
                self._history.clear()
                self._warned.clear()
                self._total_warned.clear()
