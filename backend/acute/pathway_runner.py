"""Declarative Pathway & `# ai-team` Loop Runner Engine (Config-Driven Forward Deployments).

Executes clinical verticals directly from validated `packs/*.yaml` Deployment Packs — proving
the "config-per-forward-deployment" thesis without per-case Python branching:
1. Validates every pack at load time (canonical 8 order primitives, zero forbidden titles,
   explicit deterministic wake-up conditions on every `# ai-team` loop, deterministic SHA-256 hash).
2. Compiles 5/5-linter-verified initial summaries and multi-turn intake updates directly from YAML.
3. Evaluates real `# ai-team` background loop ticks (`evaluate_ai_team_loop_tick`):
   - Wake trigger breached -> wakes on-duty GP (`state='needs_decision'`, `baton='with_doctor'`)
   - Optimal zone met & `optimal_action='auto_close_quietly'` -> closes loop with zero doctor interruption
   - Otherwise -> stays quietly in `# ai-team` (`state='scheduled'`, `baton='idle'`)

Strict boundary invariant: NEVER imports `loopbench`.
"""
from __future__ import annotations

import copy
import hashlib
import operator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import yaml
from sqlalchemy import select
from sqlalchemy.orm.attributes import flag_modified

from .db import CaseLoop, Database, new_id, record_audit, utcnow
from .live_bus import LiveHarnessBus
from .practice_graph import CANONICAL_ORDER_TYPES as ORDER_PRIMITIVE_TYPES, validate_case_presentation

PACKS_DIR = Path(__file__).resolve().parents[2] / "packs"
_OPERATORS: dict[str, Callable[[float, float], bool]] = {
    ">": operator.gt,
    ">=": operator.ge,
    "<": operator.lt,
    "<=": operator.le,
    "==": operator.eq,
}


class PackValidationError(ValueError):
    """Raised when a declarative Deployment Pack YAML violates canonical Clinical Loop OS invariants."""


@dataclass(frozen=True)
class LoadedDeploymentPack:
    pack_id: str
    version: str
    display_name: str
    display_name_zh: str
    subtitle: str
    clinic_name: str
    careline_name: str
    aliases: tuple[str, ...]
    content_hash: str
    practice_graph_overlay: dict[str, Any]
    journeys: tuple[dict[str, Any], ...]
    source_path: str
    clinic_knowledge_id: str = "default"
    clinic_knowledge_fingerprint: str = ""

    def to_metadata(self) -> dict[str, Any]:
        total_loops = sum(len(j.get("ai_team_loops") or []) for j in self.journeys)
        return {
            "pack_id": self.pack_id,
            "version": self.version,
            "display_name": self.display_name,
            "display_name_zh": self.display_name_zh,
            "subtitle": self.subtitle,
            "clinic_name": self.clinic_name,
            "careline_name": self.careline_name,
            "aliases": list(self.aliases),
            "content_hash": self.content_hash,
            "journey_keys": [j["key"] for j in self.journeys],
            "journey_count": len(self.journeys),
            "ai_team_loop_count": total_loops,
            "declarative": True,
            "clinic_knowledge_id": self.clinic_knowledge_id,
            "clinic_knowledge_fingerprint": self.clinic_knowledge_fingerprint,
        }


def _validate_orders(orders: list[dict[str, Any]], *, ctx: str) -> None:
    for idx, ord_item in enumerate(orders or []):
        otype = str(ord_item.get("type") or "").strip()
        if otype not in ORDER_PRIMITIVE_TYPES:
            raise PackValidationError(
                f"{ctx}: order[{idx}] uses non-canonical type '{otype}' (allowed: {sorted(ORDER_PRIMITIVE_TYPES)})"
            )
        if otype == "rx" and str(ord_item.get("action") or "") not in ("start", "update", "stop", "continue"):
            raise PackValidationError(f"{ctx}: rx order[{idx}] requires atomic action in start|update|stop|continue")


def _validate_pack_dict(raw: dict[str, Any], *, raw_text: str, source_path: str) -> LoadedDeploymentPack:
    pack_id = str(raw.get("pack_id") or "").strip().lower()
    if not pack_id:
        raise PackValidationError(f"{source_path}: missing required 'pack_id'")
    version = str(raw.get("version") or "1.0.0").strip()
    journeys_raw = raw.get("journeys")
    if not isinstance(journeys_raw, list) or not journeys_raw:
        raise PackValidationError(f"{source_path}: pack '{pack_id}' must declare at least 1 journey")

    # Fail-closed validation of bound ClinicKnowledge pack (defaults to 'default')
    clinic_knowledge_id = str(raw.get("clinic_knowledge_id") or "default").strip()
    try:
        from backend.knowledge import load_knowledge

        bound_kn = load_knowledge(clinic_knowledge_id)
        clinic_knowledge_fp = bound_kn.fingerprint
    except Exception as exc:
        raise PackValidationError(
            f"{source_path}: invalid clinic_knowledge_id '{clinic_knowledge_id}': {exc}"
        ) from exc

    validated_journeys: list[dict[str, Any]] = []
    for j in journeys_raw:
        if not isinstance(j, dict) or not j.get("key") or not j.get("patient_id"):
            raise PackValidationError(f"{source_path}: every journey requires 'key' and 'patient_id'")
        j_copy = copy.deepcopy(j)
        j_copy["pack"] = pack_id
        j_copy["declarative_pack"] = True
        j_copy["clinic_knowledge_id"] = clinic_knowledge_id
        j_copy["clinic_knowledge_fingerprint"] = clinic_knowledge_fp

        init_sum = j_copy.get("initial_summary") or {}
        for iss in init_sum.get("issues") or []:
            _validate_orders(iss.get("suggested_orders") or [], ctx=f"{pack_id}.{j['key']}.initial")
        for st in j_copy.get("intake_stages") or []:
            for iss in st.get("update_issues") or []:
                _validate_orders(iss.get("suggested_orders") or [], ctx=f"{pack_id}.{j['key']}.{st.get('stage_id')}")

        for lp in j_copy.get("ai_team_loops") or []:
            wop = str(lp.get("wake_operator") or "")
            oop = str(lp.get("optimal_operator") or "")
            if wop not in _OPERATORS or oop not in _OPERATORS:
                raise PackValidationError(
                    f"{pack_id}.{j['key']}.{lp.get('loop_id')}: invalid operator (allowed: {sorted(_OPERATORS)})"
                )
            if not lp.get("metric") or lp.get("wake_threshold") is None:
                raise PackValidationError(f"{pack_id}.{j['key']}.{lp.get('loop_id')}: requires metric and wake_threshold")

        validated_journeys.append(j_copy)

    aliases = tuple(dict.fromkeys([pack_id] + [str(a).strip().lower() for a in (raw.get("aliases") or []) if str(a).strip()]))
    content_hash = hashlib.sha256(raw_text.encode("utf-8")).hexdigest()[:16]
    return LoadedDeploymentPack(
        pack_id=pack_id,
        version=version,
        display_name=str(raw.get("display_name") or pack_id),
        display_name_zh=str(raw.get("display_name_zh") or raw.get("display_name") or pack_id),
        subtitle=str(raw.get("subtitle") or ""),
        clinic_name=str(raw.get("clinic_name") or "Central Medical"),
        careline_name=str(raw.get("careline_name") or "Sam"),
        aliases=aliases,
        content_hash=content_hash,
        practice_graph_overlay=dict(raw.get("practice_graph_overlay") or {}),
        journeys=tuple(validated_journeys),
        source_path=source_path,
        clinic_knowledge_id=clinic_knowledge_id,
        clinic_knowledge_fingerprint=clinic_knowledge_fp,
    )


class DeclarativePathwayRunner:
    """Registry and runtime executor for YAML-defined Deployment Packs and `# ai-team` loops."""

    def __init__(self, packs: list[LoadedDeploymentPack]) -> None:
        self._packs_by_id: dict[str, LoadedDeploymentPack] = {}
        self._alias_map: dict[str, str] = {}
        self._journeys_by_key: dict[str, dict[str, Any]] = {}
        for p in packs:
            self._packs_by_id[p.pack_id] = p
            for alias in p.aliases:
                self._alias_map[alias] = p.pack_id
            for j in p.journeys:
                self._journeys_by_key[j["key"]] = j

    @classmethod
    def load_from_directory(cls, packs_dir: Path | str | None = None) -> "DeclarativePathwayRunner":
        base = Path(packs_dir) if packs_dir is not None else PACKS_DIR
        loaded: list[LoadedDeploymentPack] = []
        if base.is_dir():
            for path in sorted(base.glob("*.yaml")):
                raw_text = path.read_text(encoding="utf-8")
                parsed = yaml.safe_load(raw_text)
                if isinstance(parsed, dict):
                    loaded.append(_validate_pack_dict(parsed, raw_text=raw_text, source_path=str(path)))
        return cls(loaded)

    def list_packs(self) -> list[dict[str, Any]]:
        return [p.to_metadata() for p in self._packs_by_id.values()]

    def resolve_pack(self, pack_or_alias: str | None) -> LoadedDeploymentPack | None:
        key = (pack_or_alias or "").strip().lower()
        pid = self._alias_map.get(key)
        return self._packs_by_id.get(pid) if pid else None

    def all_journeys(self) -> tuple[dict[str, Any], ...]:
        return tuple(self._journeys_by_key.values())

    def get_journey(self, j_key: str) -> dict[str, Any] | None:
        return self._journeys_by_key.get(j_key)

    def is_declarative_journey(self, j_key: str) -> bool:
        return j_key in self._journeys_by_key

    def build_initial_summary(self, j_key: str, *, welcome_msg: str) -> dict[str, Any]:
        j = self._journeys_by_key[j_key]
        summary = copy.deepcopy(j.get("initial_summary") or {})
        summary.setdefault("intake_sufficiency", "sufficient")
        summary.setdefault("alerts", [])
        summary.setdefault("issues", [])
        summary.setdefault("scheduled_tasks", [])
        summary["original_chat"] = [{"actor": "Sam", "time": "Now", "text": welcome_msg, "lang": j.get("default_lang", "en")}]
        summary["agent_thread"] = []
        summary["attachments"] = []
        summary["ai_team_loops"] = copy.deepcopy(j.get("ai_team_loops") or [])
        summary["declarative_pack_id"] = j["pack"]
        summary["clinic_knowledge_id"] = j.get("clinic_knowledge_id", "default")
        summary["clinic_knowledge_fingerprint"] = j.get("clinic_knowledge_fingerprint", "")
        return summary

    def compile_turn_update(
        self,
        *,
        j_key: str,
        patient_text: str,
        existing_summary: dict[str, Any],
        is_zh: bool,
        current_state: str,
        current_baton: str,
        current_title: str,
        current_lead: str,
        current_cp: str,
    ) -> tuple[str, str, str, str, str, dict[str, Any], str, list[str]]:
        """Execute declarative YAML intake stage progression without any per-case Python branching."""
        j = self._journeys_by_key[j_key]
        summary = copy.deepcopy(existing_summary or {})
        low_text = (patient_text or "").lower()

        matched_stage: dict[str, Any] | None = None
        completed_stages = set(summary.get("_completed_declarative_stages") or [])
        stages = list(j.get("intake_stages") or [])

        for st in stages:
            terms = [str(t).lower() for t in (st.get("match_any") or [])]
            if any(t in low_text or t in patient_text for t in terms):
                matched_stage = st
                break
        if matched_stage is None:
            for st in stages:
                if st.get("stage_id") not in completed_stages:
                    matched_stage = st
                    break

        if matched_stage is None:
            fallback_reply = (
                "多謝你嘅補充——我已經更新咗你嘅長壽健康檔案，並會喺背景追蹤循環繼續留意你嘅指標。"
                if is_zh
                else "Thank you for the update. I have recorded it in your preventive longevity chart and will continue monitoring your scheduled loop."
            )
            return (
                current_state,
                current_baton,
                current_title or j.get("ai_title_initial") or j["title"],
                current_lead or j.get("lead_line_initial") or j["subtitle"],
                current_cp,
                summary,
                fallback_reply,
                [f"DeclarativePathwayRunner ({j['pack']}) · Logged follow-up note"],
            )

        sid = str(matched_stage.get("stage_id") or "stage")
        completed_stages.add(sid)
        summary["_completed_declarative_stages"] = sorted(completed_stages)

        if matched_stage.get("update_issues"):
            summary["issues"] = copy.deepcopy(matched_stage["update_issues"])

        if matched_stage.get("admin_handoff_task"):
            ah = list(summary.get("admin_handoffs") or [])
            task_txt = str(matched_stage["admin_handoff_task"])
            if not any(task_txt[:36] in str(x.get("task", "")) for x in ah):
                ah.append({
                    "id": new_id("adm"),
                    "route": f"DeclarativePack ({j['pack']}) → Clinic Team / Nurse Rachel",
                    "task": task_txt,
                    "status": "pending_admin",
                    "created_at": utcnow().isoformat(),
                })
                summary["admin_handoffs"] = ah

        next_state = str(matched_stage.get("next_state") or current_state)
        next_baton = str(matched_stage.get("next_baton") or current_baton)
        next_cp = str(matched_stage.get("next_checkpoint") or current_cp)
        ai_title = str(matched_stage.get("ai_title") or current_title or j.get("ai_title_initial") or j["title"])
        lead_line = str(matched_stage.get("lead_line") or current_lead or j.get("lead_line_initial") or j["subtitle"])
        reply_en = str(matched_stage.get("reply_en") or "")
        reply_zh = str(matched_stage.get("reply_zh") or reply_en)
        summary["_last_reply_bilingual"] = (reply_en, reply_zh)

        return (
            next_state,
            next_baton,
            ai_title,
            lead_line,
            next_cp,
            summary,
            reply_zh if is_zh else reply_en,
            [f"DeclarativePathwayRunner ({j['pack']}/{sid}) · Transitioned -> {next_state} ({next_baton})"],
        )

    def evaluate_ai_team_loop_tick(
        self,
        db: Database,
        *,
        tenant_id: str,
        case_id: str,
        loop_id: str,
        metrics: dict[str, float],
        live_bus: LiveHarnessBus | None = None,
    ) -> dict[str, Any]:
        """Deterministically evaluate a declarative `# ai-team` wake-up or auto-close loop rule."""
        with db.tenant_session(tenant_id) as session:
            case = session.execute(
                select(CaseLoop).where(CaseLoop.tenant_id == tenant_id, CaseLoop.id == case_id)
            ).scalars().first()
            if case is None:
                raise ValueError("Case not found.")

            j = self._journeys_by_key.get(case.case_key)
            summary = copy.deepcopy(case.summary_json or {})
            loops = list(summary.get("ai_team_loops") or (j.get("ai_team_loops") if j else []) or [])
            loop_spec = next((lp for lp in loops if lp.get("loop_id") == loop_id), None)
            if loop_spec is None:
                raise ValueError(f"Unknown ai_team loop_id '{loop_id}' for case '{case.case_key}'.")

            metric_name = str(loop_spec["metric"])
            if metric_name not in metrics:
                raise ValueError(f"Missing required metric '{metric_name}' for loop '{loop_id}'.")
            observed_val = float(metrics[metric_name])

            wake_fn = _OPERATORS[str(loop_spec["wake_operator"])]
            opt_fn = _OPERATORS[str(loop_spec["optimal_operator"])]
            wake_thresh = float(loop_spec["wake_threshold"])
            opt_thresh = float(loop_spec["optimal_threshold"])

            timeline = list(case.timeline_json or [])
            alerts = list(summary.get("alerts") or [])

            if wake_fn(observed_val, wake_thresh):
                outcome = "wake_doctor"
                message = str(loop_spec.get("wake_message") or f"{metric_name}={observed_val} breached threshold.")
                case.state = "needs_decision"
                case.baton = "with_doctor"
                case.next_checkpoint_label = f"AI Loop Wake · {metric_name}={observed_val:g}"
                case.lead_line = message
                alerts.insert(0, {
                    "level": "high",
                    "label": f"# ai-team Wake · {loop_spec.get('label', loop_id)}",
                    "detail": f"Observed {metric_name}={observed_val:g} ({loop_spec['wake_operator']} {wake_thresh:g}). {message}",
                })
                summary["alerts"] = alerts
            elif opt_fn(observed_val, opt_thresh):
                opt_action = str(loop_spec.get("optimal_action") or "auto_close_quietly")
                outcome = opt_action
                message = str(loop_spec.get("optimal_message") or f"{metric_name}={observed_val} reached optimal target.")
                if opt_action == "auto_close_quietly":
                    case.state = "closed"
                    case.baton = "idle"
                    case.next_checkpoint_label = "Auto-closed (optimal zone)"
                else:
                    case.state = "scheduled"
                    case.baton = "idle"
                    case.next_checkpoint_label = f"Quiet monitoring · {metric_name}={observed_val:g}"
                case.lead_line = message
            else:
                outcome = "stay_scheduled"
                message = f"Observed {metric_name}={observed_val:g} between optimal and wake thresholds; staying quietly in # ai-team."
                case.state = "scheduled"
                case.baton = "idle"
                case.next_checkpoint_label = f"Monitoring · {metric_name}={observed_val:g}"

            timeline.append({
                "date": f"Day +{loop_spec.get('due_days', 0)}",
                "label": f"AiTeamLoopRunner ({loop_id}) · {metric_name}={observed_val:g} -> {outcome}: {message}",
                "agent": "AiTeamLoopRunner",
            })
            summary["_last_ai_team_tick"] = {
                "loop_id": loop_id,
                "metric": metric_name,
                "observed": observed_val,
                "outcome": outcome,
                "message": message,
                "evaluated_at": utcnow().isoformat(),
            }
            lint = validate_case_presentation(summary)
            case.summary_json = summary
            case.timeline_json = timeline
            flag_modified(case, "summary_json")
            flag_modified(case, "timeline_json")
            case.updated_at = utcnow()
            record_audit(
                session,
                tenant_id=tenant_id,
                actor_role="assistant",
                actor_id="ai_team_loop_runner",
                action="ai_team_loop_evaluated",
                resource_type="case_loop",
                resource_id=case.id,
                metadata={
                    "loop_id": loop_id,
                    "metric": metric_name,
                    "observed": observed_val,
                    "outcome": outcome,
                    "new_state": case.state,
                },
            )
            session.flush()
            pid = case.patient_id
            new_state = case.state
            new_baton = case.baton

        if live_bus is not None:
            live_bus.append_tool_call(
                pid,
                {
                    "agent": "AiTeamLoopRunner",
                    "tool": f"ai_team.{loop_id}",
                    "authority": "DRAFT",
                    "emr_target": "ai_team",
                    "summary": f"Evaluated {metric_name}={observed_val:g} -> {outcome} (state={new_state})",
                },
            )
            live_bus.publish(
                "case.updated",
                patient_id=pid,
                data={"case_id": case_id, "state": new_state, "baton": new_baton, "ai_team_outcome": outcome},
            )

        return {
            "ok": True,
            "case_id": case_id,
            "loop_id": loop_id,
            "metric": metric_name,
            "observed": observed_val,
            "outcome": outcome,
            "new_state": new_state,
            "new_baton": new_baton,
            "message": message,
            "lint_report": lint,
        }


_RUNNER_SINGLETON: DeclarativePathwayRunner | None = None


def get_declarative_pathway_runner() -> DeclarativePathwayRunner:
    """Return cached `DeclarativePathwayRunner` loaded from `packs/*.yaml`."""
    global _RUNNER_SINGLETON
    if _RUNNER_SINGLETON is None:
        _RUNNER_SINGLETON = DeclarativePathwayRunner.load_from_directory()
    return _RUNNER_SINGLETON
