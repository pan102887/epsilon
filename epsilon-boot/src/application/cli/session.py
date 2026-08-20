"""TUI session state models."""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import uuid4


def new_session_id() -> str:
    """Create a local TUI session id."""
    return f"tui-{uuid4().hex}"


@dataclass
class TuiSessionState:
    """Mutable state for one TUI process."""

    session_id: str = field(default_factory=new_session_id)
    model: str | None = None
    approval_mode: str = "ask"
    should_exit: bool = False

    def reset_session(self) -> str:
        """Replace the current conversation session id and return the old one."""
        old_session_id = self.session_id
        self.session_id = new_session_id()
        return old_session_id
