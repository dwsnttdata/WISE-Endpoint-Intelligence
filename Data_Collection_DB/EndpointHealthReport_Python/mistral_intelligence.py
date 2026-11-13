"""Optional aggregate-only Azure AI Foundry narrative for WISE snapshots."""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import re
from typing import Any


SYSTEM_PROMPT = """You are the intelligence layer for WISE Endpoint Intelligence.
Analyze only the Microsoft Intune and endpoint-management facts supplied by the deterministic Python analytics engine.
All supplied counts, denominators, percentages, status categories, and evidence are authoritative. Never recalculate, change, estimate or invent them.
Write a detailed executive summary in three paragraphs. Use only the exact numeric placeholder tokens listed in the supplied summary_placeholders object; do not invent, alter, or write numeric metrics yourself. Python replaces each token with its authoritative value.
In all executive_summary fields, never copy literal digits from the facts, even when correct. Write 'Coverage includes {{total_devices}} devices', not a literal count. Use words for durations and priorities. The only allowed digit-bearing product name is exactly 'Windows 11'; write other product versions in words. Do not write dates, numbered lists, numeric thresholds, or numeric risk scores in the summary.
For introduction, discuss total coverage, compliance, encryption, secured threat reporting, separate denominators, compliance coverage, and monthly Windows patch compliance where supplied. For findings, discuss evidenced issue categories, unknown threat reporting separately from reported threats, low storage with its measured denominator, and Windows patch/support limitations. For bottomLine, prioritize categories supported by the facts and state that Python's device-level risk classifications remain authoritative.
Generate one insight and one actionable recommendation for every category in issue_metrics. Use that category's exact key. Do not emit categories absent from issue_metrics. Recommendations must be safe and evidence-specific, and must not claim an incident, user impact, failed installation, policy cause, or remediation outcome that the facts do not establish.
Use the provided category key exactly for each insight and recommendation. Do not include device names, user names, email addresses, IDs, serials, dates, or other identifiers.
Risk priority in this output is a narrative priority for a category. Do not alter Python's per-device risk categories or fabricate per-device conclusions.
Return only valid JSON matching the requested schema."""

DEPLOYMENT_DEFAULT = "mistral-medium-3-5"
PRIORITIES = {"critical", "high", "medium", "low"}
ISSUE_KEYS = (
    "threat", "jailbroken", "certificate", "criticalStorage", "noncompliant",
    "unencrypted", "update", "lowStorage", "stale", "management", "threatGap",
    "ownership", "autopilot",
)
OUTPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "executive_summary": {
            "type": "object", "additionalProperties": False,
            "properties": {key: {"type": "string"} for key in ("introduction", "findings", "bottomLine")},
            "required": ["introduction", "findings", "bottomLine"],
        },
        "key_insights": {"type": "array", "maxItems": len(ISSUE_KEYS), "items": {
            "type": "object", "additionalProperties": False,
            "properties": {"key": {"type": "string", "enum": list(ISSUE_KEYS)}, "title": {"type": "string"}, "observation": {"type": "string"},
                            "implication": {"type": "string"}, "priority": {"type": "string", "enum": sorted(PRIORITIES)}},
            "required": ["key", "title", "observation", "implication", "priority"],
        }},
        "recommendations": {"type": "array", "maxItems": len(ISSUE_KEYS), "items": {
            "type": "object", "additionalProperties": False,
            "properties": {"key": {"type": "string", "enum": list(ISSUE_KEYS)}, "title": {"type": "string"}, "action": {"type": "string"},
                            "owner": {"type": "string"}, "priority": {"type": "string", "enum": sorted(PRIORITIES)}},
            "required": ["key", "title", "action", "owner", "priority"],
        }},
    },
    "required": ["executive_summary", "key_insights", "recommendations"],
}


class IntelligenceValidationError(ValueError):
    """Safe validation category for untrusted model output."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


def build_ai_facts(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Project only deterministic numeric aggregates and fixed category names."""
    summary = snapshot.get("summary") if isinstance(snapshot.get("summary"), dict) else {}
    counts = summary.get("exceptionCounts") if isinstance(summary.get("exceptionCounts"), dict) else {}
    metrics = summary.get("metrics") if isinstance(summary.get("metrics"), dict) else {}
    health = summary.get("health") if isinstance(summary.get("health"), list) else []
    collection = snapshot.get("coverage", {}).get("collectionStatus", {})
    status = collection.get("status") if isinstance(collection, dict) else None
    facts: dict[str, Any] = {
        "total_devices": _number(summary.get("totalDevices")),
        "evaluated_devices": _number(summary.get("evaluatedDevices")),
        "exception_devices": _number(summary.get("exceptionDevices")),
        "storage_measured_devices": _number(metrics.get("storageMeasured")),
        "collection_status": status if status in {"complete", "partial", "imported-unverified"} else "unavailable",
        "exception_counts": {key: _number(counts.get(key)) for key in ISSUE_KEYS if _number(counts.get(key)) is not None},
        "health_metrics": [
            {"category": item["key"], "numerator": _number(item.get("numerator")),
             "denominator": _number(item.get("denominator")), "percentage": _number(item.get("percentage"))}
            for item in health if isinstance(item, dict) and item.get("key") in
            {"compliance", "encryption", "patch", "threat", "activity", "management"}
        ],
    }
    health_by_key = {item.get("key"): item for item in health if isinstance(item, dict)}
    percentages = summary.get("percentages") if isinstance(summary.get("percentages"), dict) else {}
    def metric_text(metric: Any) -> str:
        if not isinstance(metric, dict) or type(metric.get("denominator")) is not int or metric["denominator"] <= 0:
            return "not available"
        percentage = _number(metric.get("percentage"))
        numerator = _number(metric.get("numerator"))
        if percentage is None or numerator is None:
            return "not available"
        return f"{percentage:.1f}% ({numerator:g} / {metric['denominator']})"
    tokens = {
        "total_devices": str(_number(summary.get("totalDevices")) or 0),
        "evaluated_devices": str(_number(summary.get("evaluatedDevices")) or 0),
        "compliance": metric_text(health_by_key.get("compliance")),
        "encryption": metric_text(health_by_key.get("encryption")),
        "secured_threat": metric_text(health_by_key.get("threat")),
        "compliance_coverage": metric_text(summary.get("complianceCoverage")),
        "monthly_patch": metric_text(health_by_key.get("patch")),
        "storage_measured": str(_number(metrics.get("storageMeasured")) or 0),
        "unknown_threat": str(_number(counts.get("threatGap")) or 0),
        "reported_threat": str(_number(counts.get("threat")) or 0),
        "unencrypted": str(_number(counts.get("unencrypted")) or 0),
        "stale": str(_number(counts.get("stale")) or 0),
        "jailbroken": str(_number(counts.get("jailbroken")) or 0),
    }
    for key in ISSUE_KEYS:
        metric = percentages.get(key)
        if key == "autopilot":
            numerator = _number(summary.get("autopilotExceptions"))
            denominator = _number(summary.get("autopilotRegistered"))
            metric = {"numerator": numerator, "denominator": denominator,
                      "percentage": round(numerator / denominator * 100, 1) if numerator is not None and denominator else None}
        if not isinstance(metric, dict):
            numerator = _number(counts.get(key))
            denominator = _number(metrics.get("storageMeasured")) if key in ("lowStorage", "criticalStorage") else _number(summary.get("totalDevices"))
            metric = {"numerator": numerator, "denominator": denominator,
                      "percentage": round(numerator / denominator * 100, 1) if numerator is not None and denominator else None}
        token_key = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", key).lower()
        tokens["issue_" + token_key] = metric_text(metric)
    facts["summary_placeholders"] = {"{{" + key + "}}": value for key, value in tokens.items()}
    facts["issue_metrics"] = {key: tokens["issue_" + re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", key).lower()] for key in ISSUE_KEYS
                               if tokens["issue_" + re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", key).lower()] != "not available" and
                               ((_number(counts.get(key)) or 0) > 0 or (key == "autopilot" and (_number(summary.get("autopilotExceptions")) or 0) > 0))}
    patch = snapshot.get("windowsPatchSummary")
    if isinstance(patch, dict):
        allowed = ("totalWindowsDevices", "evaluated", "compliant", "nonCompliant", "unsupported", "supportUnknown", "unknown")
        facts["windows_patch"] = {key: _number(patch.get(key)) for key in allowed if _number(patch.get(key)) is not None}
    policy = snapshot.get("policySummary")
    if isinstance(policy, dict):
        allowed_policy_counts = ("compliantDeviceCount", "nonCompliantDeviceCount", "errorDeviceCount",
                                 "conflictDeviceCount", "notApplicableDeviceCount", "unknownDeviceCount", "inGracePeriodCount")
        policy_counts = {key: _number(policy.get(key)) for key in allowed_policy_counts if _number(policy.get(key)) is not None}
        if policy_counts:
            facts["policy_status_counts"] = policy_counts
    return facts


def _number(value: Any) -> int | float | None:
    if type(value) not in (int, float) or not math.isfinite(value):
        return None
    return value


def _bounded_text(value: Any, maximum: int) -> str:
    if not isinstance(value, str):
        raise IntelligenceValidationError("invalid_text_type")
    text = re.sub(r"\s+", " ", value).strip()
    if not text:
        raise IntelligenceValidationError("empty_text")
    if len(text) > maximum:
        raise IntelligenceValidationError("text_too_long")
    if "\x00" in text:
        raise IntelligenceValidationError("invalid_text")
    return text


def _validate_template(value: Any, maximum: int) -> str:
    text = _bounded_text(value, maximum)
    remainder = re.sub(r"\{\{[a-z_]+\}\}", "", text)
    remainder = remainder.replace("Windows 11", "")
    if re.search(r"\d", remainder):
        raise IntelligenceValidationError("unapproved_numeric_claim")
    return text


def validate_intelligence(value: Any) -> dict[str, Any]:
    """Validate model output independently of structured-output enforcement."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as error:
            raise IntelligenceValidationError("invalid_json") from error
    if not isinstance(value, dict) or not {"executive_summary", "key_insights", "recommendations"}.issubset(value):
        raise IntelligenceValidationError("missing_top_level_fields")
    executive = value["executive_summary"]
    if not isinstance(executive, dict) or not {"introduction", "findings", "bottomLine"}.issubset(executive):
        raise IntelligenceValidationError("missing_summary_fields")
    result = {"executive_summary": {key: _validate_template(executive[key], 1200) for key in ("introduction", "findings", "bottomLine")}}
    for field, required in (("key_insights", ("key", "title", "observation", "implication", "priority")),
                            ("recommendations", ("key", "title", "action", "owner", "priority"))):
        rows = value[field]
        if not isinstance(rows, list):
            raise IntelligenceValidationError("invalid_" + field + "_list")
        if len(rows) > len(ISSUE_KEYS):
            raise IntelligenceValidationError("too_many_" + field)
        clean = []
        for row in rows:
            if not isinstance(row, dict) or not set(required).issubset(row):
                raise IntelligenceValidationError("missing_" + field + "_item_fields")
            if row["key"] not in ISSUE_KEYS:
                raise IntelligenceValidationError("invalid_issue_key")
            priority = row["priority"].strip().lower() if isinstance(row["priority"], str) else row["priority"]
            if priority not in PRIORITIES:
                raise IntelligenceValidationError("invalid_priority")
            clean.append({"key": row["key"], **{key: _bounded_text(row[key], 500 if key in ("action", "observation", "implication") else 350 if key == "owner" else 120) for key in required if key not in ("priority", "key")}, "priority": priority})
        result[field] = clean
    return result


def render_intelligence(value: dict[str, Any], facts: dict[str, Any]) -> dict[str, Any]:
    """Substitute only exact Python-derived summary tokens and strip unknown tokens."""
    placeholders = facts["summary_placeholders"]
    def render(text: str) -> str:
        def replace(match: re.Match[str]) -> str:
            token = match.group(0)
            if token not in placeholders:
                raise IntelligenceValidationError("unknown_summary_placeholder")
            return placeholders[token]
        return re.sub(r"\{\{[A-Za-z_]+\}\}", replace, text)
    result = {**value, "executive_summary": {key: render(text) for key, text in value["executive_summary"].items()}}
    return result


def _validate_issue_coverage(value: dict[str, Any], facts: dict[str, Any]) -> dict[str, Any]:
    expected = set(facts.get("issue_metrics", {}))
    for field in ("key_insights", "recommendations"):
        keys = [item["key"] for item in value[field]]
        if len(keys) != len(set(keys)) or set(keys) != expected:
            raise IntelligenceValidationError("incomplete_issue_coverage")
    return value


def _client() -> Any:
    endpoint = os.environ.get("MISTRAL_ENDPOINT", "").strip().rstrip("/")
    api_key = os.environ.get("MISTRAL_API_KEY", "")
    if not endpoint or not api_key:
        raise RuntimeError("Mistral settings are not configured")
    from openai import OpenAI
    return OpenAI(base_url=endpoint, api_key=api_key, timeout=45.0, max_retries=0)


def _request(facts: dict[str, Any], client: Any = None, *, validation_reason: str | None = None) -> dict[str, Any]:
    client = client or _client()
    placeholders = ", ".join(facts.get("summary_placeholders", {}))
    categories = ", ".join(facts.get("issue_metrics", {})) or "none"
    correction = ""
    if validation_reason:
        correction = ("\nThe previous response failed local validation: " + validation_reason +
                      ". Regenerate the entire JSON response. In executive_summary, replace every literal numeric fact with an exact allowed placeholder, "
                      "for example 'Coverage includes {{total_devices}} devices'. Do not copy literal digits from placeholder values. "
                      "Use only allowed tokens and include each required issue key exactly once in both arrays. Keep text concise within the schema bounds.")
    logger = logging.getLogger("wise.mistral")
    logger.info("WISE Foundry request started; validation_retry=%s", bool(validation_reason))
    response = client.chat.completions.create(
        model=os.environ.get("MISTRAL_DEPLOYMENT", DEPLOYMENT_DEFAULT),
        messages=[{"role": "system", "content": SYSTEM_PROMPT + "\nAllowed summary placeholder tokens: " + placeholders + "\nRequired issue keys for key_insights and recommendations: " + categories + "\nSchema: " + json.dumps(OUTPUT_SCHEMA, separators=(",", ":")) + correction},
                  {"role": "user", "content": json.dumps(facts, separators=(",", ":"))}],
        response_format={"type": "json_schema", "json_schema": {"name": "wise_endpoint_intelligence", "strict": True, "schema": OUTPUT_SCHEMA}},
        max_tokens=3000,
        temperature=0.2,
    )
    logger.info("WISE Foundry response received; validating structured narrative")
    content = response.choices[0].message.content
    return render_intelligence(_validate_issue_coverage(validate_intelligence(content), facts), facts)


def _connection_facts() -> dict[str, Any]:
    return {
        "total_devices": 1, "evaluated_devices": 1, "exception_devices": 0,
        "storage_measured_devices": 1, "collection_status": "complete",
        "exception_counts": {}, "health_metrics": [],
        "summary_placeholders": {"{{total_devices}}": "1", "{{evaluated_devices}}": "1", "{{compliance}}": "100.0% (1 / 1)", "{{encryption}}": "not available", "{{secured_threat}}": "not available", "{{compliance_coverage}}": "100.0% (1 / 1)", "{{monthly_patch}}": "not available", "{{storage_measured}}": "1", "{{unknown_threat}}": "0", "{{reported_threat}}": "0", "{{unencrypted}}": "0", "{{stale}}": "0", "{{jailbroken}}": "0"},
        "issue_metrics": {},
    }


def generate_intelligence(snapshot: dict[str, Any], client: Any = None) -> tuple[dict[str, Any] | None, str | None]:
    """Return validated optional narrative plus a sanitized failure category."""
    facts = build_ai_facts(snapshot)
    validation_reason = None
    for attempt in range(2):
        try:
            result = _request(facts, client, validation_reason=validation_reason)
            logging.getLogger("wise.mistral").info("WISE Foundry narrative validated; status=available")
            return {"status": "available", **result}, None
        except IntelligenceValidationError as error:
            logging.getLogger("wise.mistral").warning("WISE Foundry response rejected; reason=%s; attempt=%s", error.reason, attempt + 1)
            if attempt == 0:
                validation_reason = error.reason
                continue
            return None, error.reason
        except Exception as error:
            return None, _failure_category(error)
    return None, "invalid_response"


def _failure_category(error: Exception) -> str:
    """Classify failures without exposing exception strings, responses, or secrets."""
    if isinstance(error, TimeoutError):
        return "timeout"
    try:
        from openai import APIConnectionError, APITimeoutError, AuthenticationError, BadRequestError, NotFoundError, RateLimitError
    except ImportError:
        APIConnectionError = APITimeoutError = AuthenticationError = BadRequestError = NotFoundError = RateLimitError = ()
    categories = (
        (APITimeoutError, "timeout"), (AuthenticationError, "authentication"),
        (RateLimitError, "rate_limited"), (NotFoundError, "deployment_not_found"),
        (BadRequestError, "request_rejected"), (APIConnectionError, "connection"),
    )
    if isinstance(error, RuntimeError) and str(error) == "Mistral settings are not configured":
        return "not_configured"
    for error_type, category in categories:
        if error_type and isinstance(error, error_type):
            return category
    if isinstance(error, IntelligenceValidationError):
        return error.reason
    if isinstance(error, (ValueError, TypeError, KeyError, IndexError)):
        return "invalid_response"
    return "model_error"


def test_connection(client: Any = None) -> dict[str, Any]:
    """Test credentials/deployment using a fabricated, minimal aggregate prompt."""
    _request(_connection_facts(), client)
    return {"status": "ok", "deployment": os.environ.get("MISTRAL_DEPLOYMENT", DEPLOYMENT_DEFAULT),
            "validated_fields": sorted(OUTPUT_SCHEMA["required"])}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--test", action="store_true", help="Test Mistral using synthetic aggregate facts only")
    args = parser.parse_args(argv)
    if not args.test:
        parser.error("--test is required; this module never runs collection")
    try:
        print(json.dumps(test_connection(), separators=(",", ":")))
        return 0
    except Exception as error:
        print(json.dumps({"status": "failed", "error_type": type(error).__name__}, separators=(",", ":")))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())