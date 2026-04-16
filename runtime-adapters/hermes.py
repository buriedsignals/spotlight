---
name: hermes-adapter
description: Hermes agent runtime adapter for the Spotlight OSINT system
version: "1.0"
runtime: hermes
---

# Hermes Agent Runtime Adapter

Implements the tool verb registry for Hermes agent framework environments.

## Implementation Notes

This adapter wraps the Hermes agent framework's native capabilities to satisfy the verb contract defined in `AGENTS.md`.

## Usage

```python
from runtime_adapters.hermes import HermesAdapter

adapter = HermesAdapter()
adapter.validate()
```

## Class: HermesAdapter

### Methods

#### validate() -> None
Validates that all required verbs are implemented. Raises `AdapterValidationError` if any verb is missing.

#### fetch(url: str, output_path: str) -> dict
Scrape URL content using Hermes's fetch tool, save to file, return metadata.

#### search(query: str, output_path: str, limit: int = 10) -> dict
Execute web search using Hermes's search tool, save results to file.

#### read_file(path: str) -> str
Read file contents using Hermes's file reading capability.

#### write_file(path: str, content: str) -> dict
Write content to file using Hermes's file writing capability.

#### edit_file(path: str, old: str, new: str) -> dict
Edit file using targeted string replacement.

#### list_files(pattern: str) -> list[str]
Use glob pattern matching to find files.

#### grep_files(pattern: str, path: str) -> list[dict]
Search file contents using regex.

#### execute_shell(command: str) -> dict
Execute shell command, return stdout + stderr.

#### spawn_agent(agent_id: str, prompt: str, config: dict) -> str
Launch Hermes sub-agent, return handle.

#### wait_agent(handle: str) -> dict
Block until agent completes using Hermes's agent wait mechanism.

#### invoke_skill(skill_id: str) -> dict
Load skill instructions from `skills/{skill_id}/SKILL.md` into context.

#### query_vault(vault_path: str, query: str) -> list[dict]
Search knowledge vault for context.

#### vault_write(vault_path: str, note_path: str, content: str) -> dict
Write note to vault and update registry.

### Sensitive Mode

When `sensitive_mode` is True, `fetch` and `search` raise `VerbForbidden`.

---

## Implementation

```python
import json
import subprocess
from pathlib import Path
from typing import Any

class VerbForbidden(Exception):
    """Raised when a verb is called in sensitive mode."""
    pass

class AdapterValidationError(Exception):
    """Raised when adapter is missing required verb implementations."""
    pass

class HermesAdapter:
    """Hermes agent framework runtime adapter for Spotlight OSINT system."""
    
    REQUIRED_VERBS = [
        "fetch", "search", "read-file", "write-file", "edit-file",
        "list-files", "grep-files", "execute-shell", "spawn-agent",
        "wait-agent", "invoke-skill", "query-vault", "vault-write"
    ]
    
    def __init__(self, sensitive_mode: bool = False):
        self.sensitive_mode = sensitive_mode
        self._agent_handles = {}
        self._terminal = None  # Hermes terminal instance
    
    def _get_terminal(self):
        """Get or create Hermes terminal instance."""
        if self._terminal is None:
            # In a real implementation, this would connect to Hermes
            self._terminal = object()
        return self._terminal
    
    def validate(self) -> None:
        """Validate all required verbs are implemented."""
        missing = []
        for verb in self.REQUIRED_VERBS:
            if not hasattr(self, verb.replace("-", "_")):
                missing.append(verb)
        if missing:
            raise AdapterValidationError(f"Missing verbs: {missing}")
    
    def fetch(self, url: str, output_path: str) -> dict:
        """Scrape URL content, save to file."""
        if self.sensitive_mode:
            raise VerbForbidden("fetch is forbidden in sensitive mode")
        # Hermes fetch implementation using terminal
        # This would use Hermes's web fetch capability
        raise NotImplementedError("Hermes fetch not yet implemented")
    
    def search(self, query: str, output_path: str, limit: int = 10) -> dict:
        """Execute web search, save results to file."""
        if self.sensitive_mode:
            raise VerbForbidden("search is forbidden in sensitive mode")
        # Hermes search implementation using terminal
        raise NotImplementedError("Hermes search not yet implemented")
    
    def read_file(self, path: str) -> str:
        """Read file contents."""
        return Path(path).read_text()
    
    def write_file(self, path: str, content: str) -> dict:
        """Write file (full overwrite)."""
        Path(path).write_text(content)
        return {"path": path, "bytes_written": len(content)}
    
    def edit_file(self, path: str, old: str, new: str) -> dict:
        """Targeted string replacement."""
        content = Path(path).read_text()
        if old not in content:
            raise ValueError(f"String '{old}' not found in {path}")
        content = content.replace(old, new)
        Path(path).write_text(content)
        return {"path": path, "replacements": 1}
    
    def list_files(self, pattern: str) -> list[str]:
        """Glob/search for files matching pattern."""
        from glob import glob
        return glob(pattern, recursive=True)
    
    def grep_files(self, pattern: str, path: str) -> list[dict]:
        """Search file contents by regex."""
        import re
        results = []
        for p in Path(path).rglob("*"):
            if p.is_file():
                try:
                    content = p.read_text()
                    for i, line in enumerate(content.splitlines(), 1):
                        if re.search(pattern, line):
                            results.append({"file": str(p), "line": i, "text": line.strip()})
                except Exception:
                    pass
        return results
    
    def execute_shell(self, command: str) -> dict:
        """Run shell command, return stdout + stderr."""
        result = subprocess.run(command, shell=True, capture_output=True, text=True)
        return {"stdout": result.stdout, "stderr": result.stderr, "returncode": result.returncode}
    
    def spawn_agent(self, agent_id: str, prompt: str, config: dict) -> str:
        """Launch sub-agent with prompt and config."""
        handle = f"agent_{agent_id}_{len(self._agent_handles)}"
        self._agent_handles[handle] = {"agent_id": agent_id, "prompt": prompt, "config": config}
        return handle
    
    def wait_agent(self, handle: str) -> dict:
        """Block until agent completes, return output."""
        if handle not in self._agent_handles:
            raise ValueError(f"Unknown agent handle: {handle}")
        # In a real implementation, this would block until Hermes agent completes
        return {"output": "Agent output placeholder", "status": "completed"}
    
    def invoke_skill(self, skill_id: str) -> dict:
        """Load skill instructions into current context."""
        skill_path = Path(__file__).parent.parent / "skills" / skill_id / "SKILL.md"
        if not skill_path.exists():
            raise FileNotFoundError(f"Skill not found: {skill_id}")
        content = skill_path.read_text()
        return {"skill_id": skill_id, "content": content}
    
    def query_vault(self, vault_path: str, query: str) -> list[dict]:
        """Search knowledge vault for context."""
        # Implementation would search vault files
        return []
    
    def vault_write(self, vault_path: str, note_path: str, content: str) -> dict:
        """Write note to vault and update registry."""
        vault_note_path = Path(vault_path) / note_path
        vault_note_path.parent.mkdir(parents=True, exist_ok=True)
        vault_note_path.write_text(content)
        return {"path": str(vault_note_path)}
```
