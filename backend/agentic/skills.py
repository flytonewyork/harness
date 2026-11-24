"""Clinical runtime SKILL.md loader, validator, and progressive-disclosure router.

Skills live under `skills/clinical/<skill-name>/SKILL.md` with YAML frontmatter and compact
markdown instructions. Instead of stuffing every clinic SOP into every LLM turn, the router
selects ONLY the always-on safety skill plus the 1–3 highest-relevance skills for the active
intent, pathway, and bilingual patient/clinician input — enforcing authority ceilings and
per-skill token budgets before context assembly.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

VALID_AUTONOMY_LEVELS = frozenset({"L0", "L1"})
ALLOWED_SKILL_AUTHORITY_CEILINGS = frozenset({"READ", "MEMORY", "CDS", "DRAFT"})
FORBIDDEN_SKILL_AUTHORITIES = frozenset({"SIGNED_WRITE", "ADMIN", "AUTONOMOUS_RX"})
FORBIDDEN_SKILL_TOOLS = frozenset({"emr.write_signed_orders", "rx.sign", "cert.issue_signed"})

_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n(.*)\Z", re.S)
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,63}$")
_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\u3000-\u303f\uff00-\uffef]")


class SkillValidationError(ValueError):
    """Raised when a clinical SKILL.md violates schema, safety, or authority invariants."""


def _quick_token_estimate(text: str) -> int:
    """Fast CJK-aware token estimate used to validate per-skill token caps."""
    if not text:
        return 0
    cjk_chars = len(_CJK_RE.findall(text))
    non_cjk = len(text) - cjk_chars
    return max(1, int(cjk_chars * 1.6 + non_cjk / 3.8 + 0.5))


@dataclass(frozen=True)
class SkillTriggers:
    always_on: bool = False
    intents: tuple[str, ...] = ()
    pathways: tuple[str, ...] = ()
    keywords_en: tuple[str, ...] = ()
    keywords_zh: tuple[str, ...] = ()


@dataclass(frozen=True)
class SkillSpec:
    name: str
    version: str
    autonomy_level: str
    authority_ceiling: str
    priority: int
    max_tokens: int
    triggers: SkillTriggers
    allowed_tools: tuple[str, ...]
    safety_floor_refs: tuple[str, ...]
    banned_claims: tuple[str, ...]
    body: str
    content_hash: str
    estimated_tokens: int
    source_path: str = ""

    def to_metadata(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "autonomy_level": self.autonomy_level,
            "authority_ceiling": self.authority_ceiling,
            "priority": self.priority,
            "max_tokens": self.max_tokens,
            "estimated_tokens": self.estimated_tokens,
            "always_on": self.triggers.always_on,
            "allowed_tools": list(self.allowed_tools),
            "safety_floor_refs": list(self.safety_floor_refs),
            "content_hash": self.content_hash,
        }


def parse_skill_markdown(raw_text: str, *, source_path: str = "<memory>") -> SkillSpec:
    """Parse and strictly validate a clinical SKILL.md document."""
    m = _FRONTMATTER_RE.match(raw_text.strip())
    if not m:
        raise SkillValidationError(f"{source_path}: missing YAML frontmatter delimited by '---'")
    fm_raw, body_raw = m.group(1), m.group(2).strip()
    try:
        meta = yaml.safe_load(fm_raw)
    except yaml.YAMLError as exc:
        raise SkillValidationError(f"{source_path}: invalid YAML frontmatter: {exc}") from exc
    if not isinstance(meta, dict):
        raise SkillValidationError(f"{source_path}: frontmatter must be a YAML mapping")

    name = str(meta.get("name") or "").strip()
    if not _SLUG_RE.match(name):
        raise SkillValidationError(f"{source_path}: invalid skill name '{name}'")

    version = str(meta.get("version") or "").strip()
    if not version:
        raise SkillValidationError(f"{source_path}: missing version")

    autonomy = str(meta.get("autonomy_level") or "").strip().upper()
    if autonomy not in VALID_AUTONOMY_LEVELS:
        raise SkillValidationError(
            f"{source_path}: autonomy_level '{autonomy}' must be one of {sorted(VALID_AUTONOMY_LEVELS)}"
        )

    ceiling = str(meta.get("authority_ceiling") or "").strip().upper()
    if ceiling in FORBIDDEN_SKILL_AUTHORITIES or ceiling not in ALLOWED_SKILL_AUTHORITY_CEILINGS:
        raise SkillValidationError(
            f"{source_path}: authority_ceiling '{ceiling}' violates asymmetric authority (allowed: {sorted(ALLOWED_SKILL_AUTHORITY_CEILINGS)})"
        )

    priority = int(meta.get("priority", 50))
    if not (1 <= priority <= 100):
        raise SkillValidationError(f"{source_path}: priority must be within 1..100")

    max_tokens = int(meta.get("max_tokens", 300))
    if not (50 <= max_tokens <= 800):
        raise SkillValidationError(f"{source_path}: max_tokens must be within 50..800")

    raw_tools = meta.get("allowed_tools") or []
    if not isinstance(raw_tools, list):
        raise SkillValidationError(f"{source_path}: allowed_tools must be a list")
    allowed_tools = tuple(str(t).strip() for t in raw_tools if str(t).strip())
    forbidden_found = [t for t in allowed_tools if t in FORBIDDEN_SKILL_TOOLS]
    if forbidden_found:
        raise SkillValidationError(
            f"{source_path}: skill attempts to grant forbidden signed-write tool(s): {forbidden_found}"
        )

    trig_raw = meta.get("triggers") or {}
    if not isinstance(trig_raw, dict):
        raise SkillValidationError(f"{source_path}: triggers must be a mapping")
    triggers = SkillTriggers(
        always_on=bool(trig_raw.get("always_on", False)),
        intents=tuple(str(x).strip().lower() for x in (trig_raw.get("intents") or []) if str(x).strip()),
        pathways=tuple(str(x).strip() for x in (trig_raw.get("pathways") or []) if str(x).strip()),
        keywords_en=tuple(str(x).strip().lower() for x in (trig_raw.get("keywords_en") or []) if str(x).strip()),
        keywords_zh=tuple(str(x).strip() for x in (trig_raw.get("keywords_zh") or []) if str(x).strip()),
    )

    safety_refs = tuple(str(x).strip() for x in (meta.get("safety_floor_refs") or []) if str(x).strip())
    banned_claims = tuple(str(x).strip() for x in (meta.get("banned_claims") or []) if str(x).strip())

    if not body_raw:
        raise SkillValidationError(f"{source_path}: skill markdown body cannot be empty")

    est_tokens = _quick_token_estimate(body_raw)
    if est_tokens > max_tokens:
        raise SkillValidationError(
            f"{source_path}: skill body estimated at {est_tokens} tokens exceeds max_tokens={max_tokens}"
        )

    content_hash = hashlib.sha256(raw_text.strip().encode("utf-8")).hexdigest()[:16]
    return SkillSpec(
        name=name,
        version=version,
        autonomy_level=autonomy,
        authority_ceiling=ceiling,
        priority=priority,
        max_tokens=max_tokens,
        triggers=triggers,
        allowed_tools=allowed_tools,
        safety_floor_refs=safety_refs,
        banned_claims=banned_claims,
        body=body_raw,
        content_hash=content_hash,
        estimated_tokens=est_tokens,
        source_path=source_path,
    )


def default_clinical_skills_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "skills" / "clinical"


class SkillRegistry:
    """In-memory validated catalog of clinical runtime skills with progressive-disclosure selection."""

    def __init__(self, skills: list[SkillSpec] | None = None) -> None:
        self._skills: dict[str, SkillSpec] = {}
        for sk in skills or []:
            self.register(sk)

    def register(self, skill: SkillSpec) -> None:
        if skill.name in self._skills:
            raise SkillValidationError(f"Duplicate skill name registered: '{skill.name}'")
        self._skills[skill.name] = skill

    @classmethod
    def load_from_directory(cls, root_dir: str | Path | None = None) -> "SkillRegistry":
        base = Path(root_dir) if root_dir is not None else default_clinical_skills_dir()
        reg = cls()
        if not base.exists():
            return reg
        for md_path in sorted(base.rglob("SKILL.md")):
            raw = md_path.read_text(encoding="utf-8")
            spec = parse_skill_markdown(raw, source_path=str(md_path))
            reg.register(spec)
        return reg

    def get(self, name: str) -> SkillSpec | None:
        return self._skills.get(name)

    def all(self) -> list[SkillSpec]:
        return sorted(self._skills.values(), key=lambda s: (-s.priority, s.name))

    def select_skills(
        self,
        *,
        intent: str = "",
        pathway_node_id: str | None = None,
        message_text: str = "",
        has_document: bool = False,
        explicit_skills: list[str] | None = None,
        max_skills: int = 3,
        token_budget: int = 1150,
    ) -> list[SkillSpec]:
        """Select always-on skills plus the highest-relevance matching skills within budget.

        Implements progressive disclosure: unrelated clinical skills stay out of the LLM context.
        """
        norm_intent = (intent or "").strip().lower()
        low_msg = (message_text or "").lower()
        selected: list[SkillSpec] = []
        used_tokens = 0
        seen: set[str] = set()

        def _try_add(sk: SkillSpec, *, mandatory: bool = False) -> bool:
            nonlocal used_tokens
            if sk.name in seen:
                return False
            if not mandatory and len(selected) >= max_skills:
                return False
            if not mandatory and used_tokens + sk.estimated_tokens > token_budget:
                return False
            selected.append(sk)
            seen.add(sk.name)
            used_tokens += sk.estimated_tokens
            return True

        # 1. Always-on safety skills are unconditionally mounted first
        for sk in self.all():
            if sk.triggers.always_on:
                _try_add(sk, mandatory=True)

        # 2. Explicitly requested skills (if present)
        for req_name in explicit_skills or []:
            sk = self._skills.get(req_name)
            if sk is not None:
                _try_add(sk)

        # 3. Score remaining skills deterministically by pathway, intent, document flag, and capped EN/ZH keywords
        scored: list[tuple[int, int, str, SkillSpec]] = []
        for sk in self._skills.values():
            if sk.name in seen:
                continue
            score = 0
            if pathway_node_id and pathway_node_id in sk.triggers.pathways:
                score += 60
            if norm_intent and norm_intent in sk.triggers.intents:
                score += 45
            if has_document and "document_ingest" in sk.triggers.intents:
                score += 40
            kw_hits = 0
            for kw in sk.triggers.keywords_en:
                if kw and kw in low_msg:
                    kw_hits += 1
            for kw_zh in sk.triggers.keywords_zh:
                if kw_zh and kw_zh in message_text:
                    kw_hits += 1
            # Cap keyword contribution so redundant substrings (e.g. 病假 + 病假紙) cannot crowd out CDS skills
            score += min(kw_hits, 2) * 20
            # Clinical safety / CDS skills receive a deterministic safety-priority boost once matched
            if score > 0 and (sk.authority_ceiling == "CDS" or sk.priority >= 90):
                score += 25
            if score > 0:
                scored.append((score, sk.priority, sk.name, sk))

        scored.sort(key=lambda row: (-row[0], -row[1], row[2]))
        for _, _, _, sk in scored:
            _try_add(sk)

        return selected

    @staticmethod
    def effective_allowed_tools(skills: list[SkillSpec]) -> set[str]:
        """Return safe union of allowed tools across active skills, stripping any forbidden tools."""
        tools: set[str] = set()
        for sk in skills:
            tools.update(sk.allowed_tools)
        return tools - FORBIDDEN_SKILL_TOOLS

    @staticmethod
    def combined_banned_claims(skills: list[SkillSpec]) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for sk in skills:
            for claim in sk.banned_claims:
                key = claim.lower()
                if key not in seen:
                    seen.add(key)
                    out.append(claim)
        return out

    @staticmethod
    def render_skills_prompt_block(skills: list[SkillSpec]) -> str:
        """Render active skills into a compact, hash-tagged prompt block."""
        if not skills:
            return ""
        chunks: list[str] = []
        for sk in skills:
            header = (
                f"[SKILL:{sk.name}@{sk.version} sha256:{sk.content_hash[:8]} "
                f"autonomy:{sk.autonomy_level} ceiling:{sk.authority_ceiling}]"
            )
            chunks.append(f"{header}\n{sk.body}")
        return "\n\n".join(chunks)


def load_protocol_banned_claims(protocols_dir: str | Path | None = None) -> list[str]:
    """Load banned_claims from existing protocols/*.yaml so legacy protocols and SKILL.md stay synced."""
    base = Path(protocols_dir) if protocols_dir is not None else (Path(__file__).resolve().parents[2] / "protocols")
    if not base.exists():
        return []
    seen: set[str] = set()
    claims: list[str] = []
    for yml_path in sorted(base.glob("*.yaml")):
        try:
            data = yaml.safe_load(yml_path.read_text(encoding="utf-8")) or {}
        except Exception:
            continue
        for item in data.get("banned_claims") or []:
            c = str(item).strip()
            if c and c.lower() not in seen:
                seen.add(c.lower())
                claims.append(c)
    return claims
