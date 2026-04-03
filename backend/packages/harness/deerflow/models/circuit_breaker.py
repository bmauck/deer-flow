"""Circuit breaker for model health tracking and automatic fallback.

Tracks per-model health state using a CLOSED/OPEN/HALF_OPEN state machine.
When a local model (Ollama, llama.cpp) is unreachable, the circuit opens and
callers are directed to use the configured fallback model instead.
"""

import logging
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)


class CircuitState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class ModelCircuit:
    """Circuit breaker state for a single model."""

    state: CircuitState = CircuitState.CLOSED
    failure_count: int = 0
    last_failure_time: float = 0.0
    last_check_time: float = 0.0
    last_check_result: bool | None = None

    failure_threshold: int = 3
    recovery_timeout: float = 60.0
    health_check_interval: float = 10.0


class CircuitBreakerRegistry:
    """Thread-safe registry of circuit breakers for all models."""

    def __init__(self):
        self._circuits: dict[str, ModelCircuit] = {}
        self._lock = threading.Lock()

    def get_circuit(self, model_name: str) -> ModelCircuit:
        with self._lock:
            if model_name not in self._circuits:
                self._circuits[model_name] = ModelCircuit()
            return self._circuits[model_name]

    def record_failure(self, model_name: str) -> None:
        with self._lock:
            circuit = self._circuits.setdefault(model_name, ModelCircuit())
            circuit.failure_count += 1
            circuit.last_failure_time = time.monotonic()
            if circuit.failure_count >= circuit.failure_threshold and circuit.state != CircuitState.OPEN:
                logger.warning(
                    "Circuit breaker OPEN for model '%s' after %d failures",
                    model_name,
                    circuit.failure_count,
                )
                circuit.state = CircuitState.OPEN

    def record_success(self, model_name: str) -> None:
        with self._lock:
            circuit = self._circuits.setdefault(model_name, ModelCircuit())
            if circuit.state != CircuitState.CLOSED:
                logger.info("Circuit breaker CLOSED for model '%s' (recovered)", model_name)
            circuit.failure_count = 0
            circuit.state = CircuitState.CLOSED
            circuit.last_check_result = True

    def should_use_fallback(self, model_name: str) -> bool:
        """Return True if the circuit is OPEN and recovery timeout hasn't elapsed."""
        with self._lock:
            circuit = self._circuits.get(model_name)
            if circuit is None or circuit.state == CircuitState.CLOSED:
                return False
            if circuit.state == CircuitState.OPEN:
                elapsed = time.monotonic() - circuit.last_failure_time
                if elapsed >= circuit.recovery_timeout:
                    circuit.state = CircuitState.HALF_OPEN
                    logger.info("Circuit breaker HALF_OPEN for model '%s' (testing recovery)", model_name)
                    return False
                return True
            return False

    def reset(self, model_name: str | None = None) -> None:
        with self._lock:
            if model_name:
                self._circuits.pop(model_name, None)
            else:
                self._circuits.clear()


_registry: CircuitBreakerRegistry | None = None
_registry_lock = threading.Lock()


def get_circuit_breaker_registry() -> CircuitBreakerRegistry:
    """Get the global circuit breaker registry singleton."""
    global _registry
    with _registry_lock:
        if _registry is None:
            _registry = CircuitBreakerRegistry()
        return _registry


def _get_base_url(model_config) -> str | None:
    """Extract base_url from a ModelConfig (stored in pydantic extra fields)."""
    if hasattr(model_config, "base_url"):
        return model_config.base_url
    extras = getattr(model_config, "__pydantic_extra__", None)
    if extras:
        return extras.get("base_url")
    return None


def _is_local_model(model_config) -> bool:
    """Return True if this model uses a local endpoint (Ollama, llama.cpp, etc)."""
    return _get_base_url(model_config) is not None


def _build_health_url(base_url: str, provider_class_path: str) -> str:
    """Build a health check URL based on the provider type."""
    parsed = urlparse(base_url)
    origin = f"{parsed.scheme}://{parsed.netloc}"

    if "ChatOllama" in provider_class_path:
        return f"{origin}/api/tags"

    # OpenAI-compatible servers (llama.cpp, vLLM, etc.)
    return f"{origin}/health"


def check_model_health(model_config) -> bool:
    """Check if a model endpoint is reachable.

    API models (no base_url) are assumed healthy. Local models are probed
    with a lightweight HTTP GET. Results are cached for ``health_check_interval``
    seconds to avoid hammering endpoints.
    """
    registry = get_circuit_breaker_registry()
    circuit = registry.get_circuit(model_config.name)

    now = time.monotonic()
    if (
        circuit.last_check_time
        and now - circuit.last_check_time < circuit.health_check_interval
        and circuit.last_check_result is not None
    ):
        return circuit.last_check_result

    base_url = _get_base_url(model_config)
    if not base_url:
        circuit.last_check_time = now
        circuit.last_check_result = True
        return True

    health_url = _build_health_url(base_url, model_config.use)

    try:
        with httpx.Client(timeout=2.0) as client:
            response = client.get(health_url)
            healthy = response.status_code < 500
    except Exception:
        healthy = False

    circuit.last_check_time = now
    circuit.last_check_result = healthy

    if healthy:
        registry.record_success(model_config.name)
    else:
        registry.record_failure(model_config.name)
        logger.warning("Health check failed for model '%s' at %s", model_config.name, base_url)

    return healthy
