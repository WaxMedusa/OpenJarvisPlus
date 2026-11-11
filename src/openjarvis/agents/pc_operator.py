"""The resident PC operator agent.

``NativeReActAgent`` already gives us a Thought/Action/Observation loop, tool
dispatch, loop guards and event emission. This subclass adds the two things a
machine operator needs and a chatbot does not:

1. **A gated executor.** Every tool call is checked by :class:`AutonomyPolicy`,
   optionally confirmed with the user, and written to the audit log — including
   refusals, because "it tried to shut down the PC" matters after the fact.
2. **Operator rules in the system prompt.** Verify after mutating, prefer
   deterministic state over screenshots, report in speakable sentences.
"""

from __future__ import annotations

from typing import Any, List, Optional

from openjarvis.agents._stubs import AgentContext
from openjarvis.agents.native_react import NativeReActAgent
from openjarvis.core.types import Message, Role, ToolCall, ToolResult
from openjarvis.pc.audit import AuditLog
from openjarvis.pc.policy import AutonomyPolicy
from openjarvis.tools._stubs import ToolExecutor

OPERATOR_RULES = """\
You are JARVIS, the resident operator of this PC. You act on the machine, not just
describe it.

Operating rules:
- Prefer the deterministic tool for the job: steam_status/steam_update for games,
  winget_status/winget_upgrade for apps, app_open for launching, pc_shell for
  inspection. Only use the shell when no dedicated tool fits.
- Before any state-changing call, know the current state (call the matching
  status/list tool first).
- After a state-changing call, verify the result by re-reading state. Never say
  something worked unless a tool result shows it worked.
- If a tool is denied or fails, stop and explain what you tried, in one or two
  sentences. Do not retry a denied action.
- Do not invent paths, package ids, app names or results. If unsure, inspect.
- Never attempt destructive machine changes (disk format, shutdown, deleting
  user files, account changes). Those are refused and must be left to the user.
- Write the final answer for speech: 1-3 short sentences, no markdown, no code
  blocks, no bullet lists. The terminal shows detail separately.
"""


class GatedToolExecutor(ToolExecutor):
    """ToolExecutor that applies autonomy policy, confirmation and auditing."""

    def __init__(
        self,
        tools: List[Any],
        *,
        policy: AutonomyPolicy,
        confirm: Any,
        audit: Optional[AuditLog] = None,
        bus: Any = None,
        agent_id: str = "pc_operator",
    ) -> None:
        super().__init__(tools, bus=bus, agent_id=agent_id)
        self._policy = policy
        self._confirm = confirm
        self._audit = audit or AuditLog()

    def execute(self, tool_call: ToolCall) -> ToolResult:
        decision = self._policy.decide(tool_call.name, tool_call.arguments)
        if decision.kind == "deny":
            self._audit.record(
                tool_call.name,
                arguments=tool_call.arguments,
                ok=False,
                detail=f"denied: {decision.reason}",
                approval="denied",
            )
            return ToolResult(
                tool_name=tool_call.name,
                content=f"Blocked by policy: {decision.reason}",
                success=False,
                metadata={"policy": "deny"},
            )

        approval = "auto"
        if decision.kind == "ask":
            prompt = _approval_prompt(tool_call)
            approved = False
            try:
                approved = bool(self._confirm(prompt))
            except Exception:
                approved = False
            approval = "approved" if approved else "denied"
            if not approved:
                self._audit.record(
                    tool_call.name,
                    arguments=tool_call.arguments,
                    ok=False,
                    detail="user did not approve",
                    approval="denied",
                )
                return ToolResult(
                    tool_name=tool_call.name,
                    content="The user did not approve this action. Do not retry it.",
                    success=False,
                    metadata={"policy": "user_denied"},
                )

        result = super().execute(tool_call)
        self._audit.record(
            tool_call.name,
            arguments=tool_call.arguments,
            ok=result.success,
            detail=str(result.content)[:1200],
            approval=approval,
        )
        return result


def _approval_prompt(tool_call: ToolCall) -> str:
    arguments = (tool_call.arguments or "").strip()
    if len(arguments) > 500:
        arguments = arguments[:500] + "…"
    return (
        f"JARVIS wants to run {tool_call.name} with:\n{arguments or '(no arguments)'}"
    )


class PCOperatorAgent(NativeReActAgent):
    """ReAct agent with machine-operator rules and a gated tool executor."""

    agent_id = "pc_operator"
    _default_temperature = 0.2
    _default_max_tokens = 1024
    _default_max_turns = 14

    def __init__(
        self,
        engine: Any,
        model: str,
        *,
        tools: Optional[List[Any]] = None,
        policy: Optional[AutonomyPolicy] = None,
        confirm: Optional[Any] = None,
        audit: Optional[AuditLog] = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(engine, model, tools=tools, **kwargs)
        self._policy = policy or AutonomyPolicy()
        self._confirm = confirm or (lambda _prompt: False)
        self._audit = audit or AuditLog()
        self._executor = GatedToolExecutor(
            self._tools,
            policy=self._policy,
            confirm=lambda prompt: self._confirm(prompt),
            audit=self._audit,
            bus=kwargs.get("bus"),
        )

    # -- prompt ------------------------------------------------------------
    def _build_messages(
        self,
        input: str,
        context: Optional[AgentContext] = None,
        *,
        system_prompt: Optional[str] = None,
    ) -> List[Message]:
        messages = super()._build_messages(input, context, system_prompt=system_prompt)
        for index, message in enumerate(messages):
            if message.role != Role.SYSTEM:
                continue
            messages[index] = Message(
                role=Role.SYSTEM,
                content=f"{OPERATOR_RULES}\n\n{message.content}",
            )
            break
        return messages

    # -- policy accessors used by the wake session --------------------------
    def set_policy(self, policy: AutonomyPolicy) -> None:
        self._policy = policy
        self._executor._policy = policy

    def set_confirm(self, confirm: Any) -> None:
        self._confirm = confirm


__all__ = ["GatedToolExecutor", "OPERATOR_RULES", "PCOperatorAgent"]
