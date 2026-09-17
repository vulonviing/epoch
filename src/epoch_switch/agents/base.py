"""BaseAgent — every agent inherits from this."""
from __future__ import annotations
import inspect
from abc import ABC, abstractmethod
from pathlib import Path

from epoch_switch.core.envelope import CaseEnvelope, Message
from epoch_switch.core.evidence_store import EvidenceStore
from epoch_switch.core.llm_client import get_llm


class BaseAgent(ABC):
    agent_id: str           # must set in subclass
    can_fill: list[str]     # position roles this agent is capable of

    @property
    def role_prompt_path(self) -> Path:
        return Path(inspect.getfile(type(self))).parent / f"{self.agent_id}.md"

    def execute(
        self,
        capsule: str,
        envelope: CaseEnvelope,
        evidence_store: EvidenceStore,
    ) -> Message:
        """Run the agent given its capsule prompt. Returns a typed Message."""
        llm = get_llm()

        system = self._system_preamble()
        user = self._user_prompt(capsule, envelope, evidence_store)

        result_dict, duration_ms = llm.complete_json(system, user)

        return self._parse_output(result_dict, envelope, duration_ms)

    def _system_preamble(self) -> str:
        return (
            "You are an AI agent in an environmental governance system. "
            "You will receive a capsule that contains your role, your position "
            "in this task, the specific case instructions, and any upstream "
            "evidence. Produce your output as a single JSON object that matches "
            "your output contract exactly. No extra keys, no prose outside JSON."
        )

    def _user_prompt(
        self,
        capsule: str,
        envelope: CaseEnvelope,
        evidence_store: EvidenceStore,
    ) -> str:
        return (
            f"CAPSULE:\n{capsule}\n\n"
            f"CASE_ID: {envelope.case_id}\n"
            f"REGULATION_REFS: {envelope.regulation_refs}\n"
            f"TIME_WINDOW: {envelope.time_window[0]} to {envelope.time_window[1]}\n\n"
            f"Produce your JSON output now."
        )

    @abstractmethod
    def _parse_output(
        self, result: dict, envelope: CaseEnvelope, duration_ms: int
    ) -> Message:
        """Convert the LLM JSON dict into a typed Message."""
        ...
