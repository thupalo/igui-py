"""Result types shared by the SAP layer (replaces the 'first character' status codes
of iGUI 2.x: ' ', 'L', 'B', 'E')."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Status(Enum):
    OK = "ok"
    SAP_UNAVAILABLE = "sap_unavailable"  # SAP GUI not running / scripting disabled
    LOGIN_REQUIRED = "login_required"  # no session for the configured system
    ALL_BUSY = "all_busy"  # sessions exist but all are busy
    SCREEN_MISMATCH = "screen_mismatch"
    SAP_ERROR = "sap_error"  # SAP reported an error / a command failed


@dataclass
class Result:
    status: Status
    messages: list[str] = field(default_factory=list)
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.status is Status.OK

    def summary(self) -> str:
        parts = [self.detail] if self.detail else []
        parts += self.messages
        return "\n".join(p for p in parts if p)
