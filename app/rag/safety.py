"""Deterministic RAG prompt-injection detection (hardening backlog B2).

Threat model: users upload PDFs whose text may contain injection
patterns ("ignore all previous instructions" …).  Those documents get
retrieved into other users' prompts, so the ingestion path evaluates the
full extracted text once, before chunking, and stamps a document-level
verdict:

* malicious — the document contains high-severity injection patterns;
  it is rejected unless safety is completely off
* suspicious — medium/low patterns matched; it is ingested and either
  hidden from retrieval (strict mode) or flagged (flag mode)
* clean — nothing matched

The engine is a pure, deterministic rule matcher (no LLM, no network).
``SafetyReviewer`` is the reserved extension point for a future
LLM-based review pass; this batch only defines the protocol.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable

SafetyLevel = Literal["clean", "suspicious", "malicious"]
SafetySeverity = Literal["high", "medium", "low"]


@dataclass(frozen=True)
class SafetyRule:
    """One deterministic injection pattern."""

    rule_id: str
    pattern: str
    severity: SafetySeverity
    description: str


@dataclass(frozen=True)
class SafetyVerdict:
    """Document-level evaluation result."""

    level: SafetyLevel
    hit_rule_ids: tuple[str, ...] = ()

    @property
    def is_malicious(self) -> bool:
        return self.level == "malicious"

    @property
    def is_suspicious(self) -> bool:
        return self.level == "suspicious"


# Rule set: patterns are matched case-insensitively on whitespace-
# normalized text.  High severity = explicit instruction overrides;
# medium = system-prompt disclosure attempts; low = repeated
# instruction-bombing heuristics.
SAFETY_RULES: tuple[SafetyRule, ...] = (
    SafetyRule(
        "ignore_above_zh",
        r"忽略(?:以上|之前|前面|上文)(?:的)?(?:所有)?(?:指令|内容|要求)",
        "high",
        "Chinese instruction override",
    ),
    SafetyRule(
        "ignore_above_en",
        r"ignore (?:all |any )?(?:previous|above|earlier|prior) instructions",
        "high",
        "English instruction override",
    ),
    SafetyRule(
        "ignore_everything_en",
        r"ignore everything (?:above|before|else)",
        "high",
        "English ignore-everything override",
    ),
    SafetyRule(
        "forget_all_zh",
        r"忘记(?:以上|之前|前面)?(?:所有)?(?:内容|指令|对话)",
        "high",
        "Chinese forget-all override",
    ),
    SafetyRule(
        "you_are_now_zh",
        r"从现在起你是|扮演一个(?:没有限制|不受约束)|你现在是",
        "high",
        "Chinese persona override",
    ),
    SafetyRule(
        "you_are_now_en",
        r"you are now (?:a |an )?(?:unrestricted|uncensored|free)",
        "high",
        "English persona override",
    ),
    SafetyRule(
        "do_not_follow_zh",
        r"不要遵守(?:上面|以上)?的?(?:规则|指令|要求)",
        "medium",
        "Chinese rule override",
    ),
    SafetyRule(
        "reveal_system_prompt_zh",
        r"(?:输出|打印|泄露|透露|返回|显示)(?:你的|系统)\s*(?:system\s+)?prompt",
        "medium",
        "Chinese system-prompt disclosure",
    ),
    SafetyRule(
        "reveal_system_prompt_en",
        r"(?:reveal|print|show|output|leak) (?:your |the )?system prompt",
        "medium",
        "English system-prompt disclosure",
    ),
    SafetyRule(
        "ignore_rules_zh",
        r"无视(?:上面|以上)?的?(?:规则|指令|要求)",
        "medium",
        "Chinese ignore-rules override",
    ),
)

_INSTRUCTION_BOMB_RULE_ID = "instruction_bomb_low"


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text).lower()


def evaluate_document(text: str) -> SafetyVerdict:
    """Evaluate full extracted text; returns the document-level verdict.

    Any high-severity hit → malicious.  Otherwise medium/low hits →
    suspicious.  Nothing → clean.
    """
    normalized = _normalize(text)
    high_hits: list[str] = []
    medium_hits: list[str] = []
    low_hits: list[str] = []
    for rule in SAFETY_RULES:
        if re.search(rule.pattern, normalized):
            if rule.severity == "high":
                high_hits.append(rule.rule_id)
            elif rule.severity == "medium":
                medium_hits.append(rule.rule_id)
            else:
                low_hits.append(rule.rule_id)
    if _has_repeated_sentence(normalized):
        low_hits.append(_INSTRUCTION_BOMB_RULE_ID)
    if high_hits:
        return SafetyVerdict(level="malicious", hit_rule_ids=tuple(high_hits))
    if medium_hits or low_hits:
        return SafetyVerdict(
            level="suspicious", hit_rule_ids=tuple(medium_hits + low_hits)
        )
    return SafetyVerdict(level="clean")


def _has_repeated_sentence(normalized: str) -> bool:
    """Instruction-bomb heuristic: any sentence repeated 3+ times.

    Sentence splitting is deterministic (punctuation) and avoids the
    regex backreference pitfalls of matching repeated CJK phrases.
    """
    sentences = [
        sentence.strip()
        for sentence in re.split(r"[。！？!?]", normalized)
        if sentence.strip()
    ]
    for index in range(len(sentences) - 2):
        if sentences[index] == sentences[index + 1] == sentences[index + 2]:
            return True
    return False


@runtime_checkable
class SafetyReviewer(Protocol):
    """Reserved LLM-review extension point (not implemented this batch).

    A future implementation receives the document text plus the
    deterministic rule hits and may escalate/clear the verdict; wire it
    into ``evaluate_document`` via an optional reviewer argument.
    """

    async def review(self, text: str, rule_hits: Sequence[str]) -> SafetyLevel: ...
