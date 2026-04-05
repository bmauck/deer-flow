"""Built-in subagent configurations."""

from .bash_agent import BASH_AGENT_CONFIG
from .browser_agent import BROWSER_AGENT_CONFIG
from .coding_agent import CODING_AGENT_CONFIG
from .general_purpose import GENERAL_PURPOSE_CONFIG
from .ops_agent import OPS_AGENT_CONFIG

__all__ = [
    "GENERAL_PURPOSE_CONFIG",
    "BASH_AGENT_CONFIG",
    "BROWSER_AGENT_CONFIG",
    "CODING_AGENT_CONFIG",
    "OPS_AGENT_CONFIG",
]

# Registry of built-in subagents
BUILTIN_SUBAGENTS = {
    "general-purpose": GENERAL_PURPOSE_CONFIG,
    "bash": BASH_AGENT_CONFIG,
    "browser": BROWSER_AGENT_CONFIG,
    "coding": CODING_AGENT_CONFIG,
    "ops": OPS_AGENT_CONFIG,
}
