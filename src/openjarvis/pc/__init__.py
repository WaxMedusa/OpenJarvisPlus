"""Machine-control primitives for the resident JARVIS operator.

This package is deliberately dependency-light and Windows-first: every
Windows-only module returns a clear ``(False, "...")`` style result on other
platforms instead of raising, so the rest of OpenJarvis keeps working on
macOS and Linux.
"""

from __future__ import annotations

from openjarvis.pc.audit import AuditLog
from openjarvis.pc.policy import AutonomyPolicy, Decision, PolicyMode
from openjarvis.pc.tasks import BackgroundTasks, TaskEvent

__all__ = [
    "AuditLog",
    "AutonomyPolicy",
    "BackgroundTasks",
    "Decision",
    "PolicyMode",
    "TaskEvent",
]
