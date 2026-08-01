#!/usr/bin/env python3
"""Validate the internal JSON record for a Feishu evaluation-item review."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


VALID_COMPLETENESS = {"complete", "basic", "incomplete", "unreviewable"}
VALID_DECISIONS = {"pass", "fail", "pending"}
VALID_KINDS = {"violation", "uncertainty", "advisory"}
VALID_SEVERITIES = {"blocker", "major", "minor", "info"}
VALID_FORCES = {"mandatory", "conditional", "advisory", "ambiguous"}
VALID_MATERIAL_STATUS = {
    "read",
    "partial",
    "denied",
    "missing",
    "encrypted",
    "unsupported",
    "failed",
    "skipped_irrelevant",
}
VALID_REVIEW_STATUS = {"completed", "blocked_rule_understanding"}
VALID_UNDERSTANDING_STATUS = {"ready", "blocked"}
DIMENSION_KEYS = {
    "authority_version",
    "scope_objects",
    "force_thresholds",
    "exceptions_conflicts",
    "evidence_delivery",
}


def nonempty(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def validate(data: Any) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    if not isinstance(data, dict):
        return ["root must be a JSON object"], warnings

    for field in (
        "entry_url",
        "review_status",
        "material_completeness",
        "overall_decision",
        "rule_understanding",
        "materials",
        "unread_materials",
        "rules",
        "items",
    ):
        if field not in data:
            errors.append(f"missing root field: {field}")

    completeness = data.get("material_completeness")
    overall = data.get("overall_decision")
    review_status = data.get("review_status")
    if review_status not in VALID_REVIEW_STATUS:
        errors.append(f"invalid review_status: {review_status!r}")
    if completeness not in VALID_COMPLETENESS:
        errors.append(f"invalid material_completeness: {completeness!r}")
    if overall not in VALID_DECISIONS:
        errors.append(f"invalid overall_decision: {overall!r}")
    if completeness in {"incomplete", "unreviewable"} and overall == "pass":
        errors.append("overall_decision cannot be pass when materials are incomplete")

    understanding = data.get("rule_understanding")
    understanding_ready = False
    if not isinstance(understanding, dict):
        errors.append("rule_understanding must be an object")
        understanding = {}
    else:
        for field in (
            "status",
            "score",
            "threshold",
            "question_count",
            "dimensions",
            "critical_ambiguities",
            "confirmed_points",
            "unresolved_points",
            "qa_log",
        ):
            if field not in understanding:
                errors.append(f"rule_understanding missing field: {field}")
        status = understanding.get("status")
        score = understanding.get("score")
        threshold = understanding.get("threshold")
        question_count = understanding.get("question_count")
        ambiguities = understanding.get("critical_ambiguities")
        dimensions = understanding.get("dimensions")
        qa_log = understanding.get("qa_log")
        if status not in VALID_UNDERSTANDING_STATUS:
            errors.append(f"invalid rule_understanding.status: {status!r}")
        if not isinstance(score, int) or isinstance(score, bool) or not 0 <= score <= 100:
            errors.append("rule_understanding.score must be an integer from 0 to 100")
        if threshold != 95:
            errors.append("rule_understanding.threshold must be 95")
        if (
            not isinstance(question_count, int)
            or isinstance(question_count, bool)
            or not 0 <= question_count <= 15
        ):
            errors.append("rule_understanding.question_count must be an integer from 0 to 15")
        if not isinstance(ambiguities, list):
            errors.append("rule_understanding.critical_ambiguities must be an array")
            ambiguities = []
        for field in ("confirmed_points", "unresolved_points"):
            if not isinstance(understanding.get(field), list):
                errors.append(f"rule_understanding.{field} must be an array")
        if not isinstance(qa_log, list):
            errors.append("rule_understanding.qa_log must be an array")
            qa_log = []
        if isinstance(question_count, int) and len(qa_log) != question_count:
            errors.append("rule_understanding.qa_log length must equal question_count")
        for index, entry in enumerate(qa_log):
            if not isinstance(entry, dict):
                errors.append(f"rule_understanding.qa_log[{index}] must be an object")
                continue
            for field in ("question", "answer", "impact"):
                if not nonempty(entry.get(field)):
                    errors.append(
                        f"rule_understanding.qa_log[{index}].{field} must be a non-empty string"
                    )
        if not isinstance(dimensions, dict):
            errors.append("rule_understanding.dimensions must be an object")
        else:
            if set(dimensions) != DIMENSION_KEYS:
                errors.append("rule_understanding.dimensions has missing or unexpected keys")
            dimension_score = 0
            limits = {
                "authority_version": 20,
                "scope_objects": 20,
                "force_thresholds": 25,
                "exceptions_conflicts": 20,
                "evidence_delivery": 15,
            }
            for key, limit in limits.items():
                value = dimensions.get(key)
                if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= limit:
                    errors.append(f"rule_understanding.dimensions.{key} must be 0..{limit}")
                else:
                    dimension_score += value
            if isinstance(score, int) and dimension_score != score:
                errors.append("rule_understanding.score must equal the sum of dimensions")
        understanding_ready = (
            status == "ready"
            and isinstance(score, int)
            and score >= 95
            and not ambiguities
        )
        if status == "ready" and not understanding_ready:
            errors.append("ready rule understanding requires score >=95 and no critical ambiguities")
        if status == "blocked" and isinstance(score, int) and score >= 95 and not ambiguities:
            warnings.append("rule understanding is blocked although the numeric gate appears ready")

    if review_status == "completed" and not understanding_ready:
        errors.append("completed review requires rule understanding to pass the 95% gate")
    if review_status == "blocked_rule_understanding":
        if overall != "pending":
            errors.append("blocked_rule_understanding requires overall_decision pending")
        if understanding_ready:
            errors.append("blocked_rule_understanding conflicts with a ready understanding gate")

    materials = data.get("materials", [])
    if not isinstance(materials, list):
        errors.append("materials must be an array")
        materials = []
    source_ids: set[str] = set()
    for index, material in enumerate(materials):
        prefix = f"materials[{index}]"
        if not isinstance(material, dict):
            errors.append(f"{prefix} must be an object")
            continue
        for field in ("source_id", "title", "type", "location", "status"):
            if not nonempty(material.get(field)):
                errors.append(f"{prefix}.{field} must be a non-empty string")
        source_id = material.get("source_id")
        if nonempty(source_id):
            if source_id in source_ids:
                errors.append(f"duplicate source_id: {source_id}")
            source_ids.add(source_id)
        if material.get("status") not in VALID_MATERIAL_STATUS:
            errors.append(f"{prefix}.status is invalid: {material.get('status')!r}")

    unread = data.get("unread_materials", [])
    if not isinstance(unread, list):
        errors.append("unread_materials must be an array")
        unread = []
    if completeness == "complete" and unread:
        warnings.append("material_completeness is complete but unread_materials is non-empty")

    rules = data.get("rules", [])
    if not isinstance(rules, list):
        errors.append("rules must be an array")
        rules = []
    rule_ids: set[str] = set()
    for index, rule in enumerate(rules):
        prefix = f"rules[{index}]"
        if not isinstance(rule, dict):
            errors.append(f"{prefix} must be an object")
            continue
        for field in ("rule_id", "source_id", "citation", "requirement"):
            if not nonempty(rule.get(field)):
                errors.append(f"{prefix}.{field} must be a non-empty string")
        rule_id = rule.get("rule_id")
        if nonempty(rule_id):
            if rule_id in rule_ids:
                errors.append(f"duplicate rule_id: {rule_id}")
            rule_ids.add(rule_id)
        if rule.get("source_id") not in source_ids:
            errors.append(f"{prefix}.source_id does not reference a known material")
        if rule.get("force") not in VALID_FORCES:
            errors.append(f"{prefix}.force is invalid: {rule.get('force')!r}")
        for field in ("applies_to", "evidence_required"):
            value = rule.get(field)
            if not isinstance(value, list) or not value:
                errors.append(f"{prefix}.{field} must be a non-empty array")

    items = data.get("items", [])
    if not isinstance(items, list):
        errors.append("items must be an array")
        items = []
    elif review_status == "completed" and not items:
        errors.append("items must be non-empty for a completed review")
    elif review_status == "blocked_rule_understanding" and items:
        errors.append("items must stay empty when review is blocked by rule understanding")
    item_ids: set[str] = set()
    item_decisions: list[str] = []
    for item_index, item in enumerate(items):
        prefix = f"items[{item_index}]"
        if not isinstance(item, dict):
            errors.append(f"{prefix} must be an object")
            continue
        item_id = item.get("item_id")
        if not nonempty(item_id):
            errors.append(f"{prefix}.item_id must be a non-empty string")
        elif item_id in item_ids:
            errors.append(f"duplicate item_id: {item_id}")
        else:
            item_ids.add(item_id)
        decision = item.get("decision")
        if decision not in VALID_DECISIONS:
            errors.append(f"{prefix}.decision is invalid: {decision!r}")
        else:
            item_decisions.append(decision)
        findings = item.get("findings")
        if not isinstance(findings, list):
            errors.append(f"{prefix}.findings must be an array")
            findings = []
        kinds: list[str] = []
        finding_ids: set[str] = set()
        for finding_index, finding in enumerate(findings):
            fp = f"{prefix}.findings[{finding_index}]"
            if not isinstance(finding, dict):
                errors.append(f"{fp} must be an object")
                continue
            for field in (
                "finding_id",
                "kind",
                "severity",
                "location",
                "evidence",
                "reasoning",
                "remediation",
            ):
                if not nonempty(finding.get(field)):
                    errors.append(f"{fp}.{field} must be a non-empty string")
            finding_id = finding.get("finding_id")
            if nonempty(finding_id):
                if finding_id in finding_ids:
                    errors.append(f"duplicate finding_id within {prefix}: {finding_id}")
                finding_ids.add(finding_id)
            kind = finding.get("kind")
            if kind not in VALID_KINDS:
                errors.append(f"{fp}.kind is invalid: {kind!r}")
            else:
                kinds.append(kind)
            if finding.get("severity") not in VALID_SEVERITIES:
                errors.append(f"{fp}.severity is invalid: {finding.get('severity')!r}")
            if kind == "violation":
                if finding.get("rule_id") not in rule_ids:
                    errors.append(f"{fp}.rule_id must reference a known rule")
                if not nonempty(finding.get("teacher_message")):
                    errors.append(f"{fp}.teacher_message is required for violations")
            if kind == "advisory" and finding.get("severity") == "blocker":
                errors.append(f"{fp}: advisory cannot have blocker severity")
        if decision == "fail" and "violation" not in kinds:
            errors.append(f"{prefix}: fail requires at least one violation")
        if decision == "pending" and "uncertainty" not in kinds:
            errors.append(f"{prefix}: pending requires at least one uncertainty")
        if decision == "pass" and any(kind in {"violation", "uncertainty"} for kind in kinds):
            errors.append(f"{prefix}: pass cannot contain violation or uncertainty")

    expected_overall = None
    if "fail" in item_decisions:
        expected_overall = "fail"
    elif "pending" in item_decisions or completeness in {"incomplete", "unreviewable"}:
        expected_overall = "pending"
    elif item_decisions:
        expected_overall = "pass"
    if review_status == "completed" and expected_overall and overall != expected_overall:
        errors.append(
            f"overall_decision {overall!r} conflicts with aggregated result {expected_overall!r}"
        )
    if not rules:
        warnings.append("rules is empty; a rule-driven review normally cannot be completed")
    return errors, warnings


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("review_json", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        data = json.loads(args.review_json.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"ERROR: cannot read JSON: {exc}", file=sys.stderr)
        return 2
    errors, warnings = validate(data)
    for warning in warnings:
        print(f"WARNING: {warning}")
    for error in errors:
        print(f"ERROR: {error}", file=sys.stderr)
    if errors:
        print(f"FAILED: {len(errors)} error(s), {len(warnings)} warning(s)", file=sys.stderr)
        return 1
    print(f"OK: review record is valid ({len(warnings)} warning(s))")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
