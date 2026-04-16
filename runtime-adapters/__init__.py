"""
Runtime adapters for the Spotlight OSINT system.

This package provides runtime-specific implementations of the tool verb registry
defined in AGENTS.md for different execution environments (Claude Code, Hermes, etc.).

Usage:
    from runtime_adapters import load_adapter
    
    adapter = load_adapter("claude")  # or "hermes"
    adapter.validate()
"""

from runtime_adapters.claude import ClaudeAdapter, AdapterValidationError, VerbForbidden
from runtime_adapters.hermes import HermesAdapter

__all__ = ["load_adapter", "ClaudeAdapter", "HermesAdapter", "AdapterValidationError", "VerbForbidden"]

_ADAPTERS = {
    "claude": ClaudeAdapter,
    "hermes": HermesAdapter,
}

def load_adapter(runtime: str, sensitive_mode: bool = False):
    """Load a runtime adapter by name.
    
    Args:
        runtime: One of 'claude', 'hermes'
        sensitive_mode: Whether to enable sensitive mode (restricts fetch/search)
    
    Returns:
        Adapter instance
    
    Raises:
        ValueError: If runtime is not supported
    """
    if runtime not in _ADAPTERS:
        raise ValueError(f"Unsupported runtime: {runtime}. Supported: {list(_ADAPTERS.keys())}")
    return _ADAPTERS[runtime](sensitive_mode=sensitive_mode)
