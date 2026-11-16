"""Collect endpoint intelligence for Azure hosting or optional local CLI use.

Python 3.11+. The Azure timer uses managed identity and deployment-time dependencies;
reports remain in memory until verified Blob archival and SharePoint publication.
Interactive CLI mode uses a private adjacent .wise-python/venv and memory-only
MSAL cache. Register the public client for delegated Graph permissions and the
http://localhost redirect (MSAL handles PKCE); broker additionally requires the
Windows broker redirect ms-appx-web://Microsoft.AAD.BrokerPlugin/<client-id>.
No device-code fallback, application secrets, provisioning, or persistent tokens.

Live defaults to collection and automatic publication; --confirm opts into approval.
--input-report is offline unless --publish is explicitly supplied. Full sanitized
source data goes to Blob in hosted mode; CLI local output is separately optional.
Only schema-v2 aggregates and exceptions go to SharePoint.
Existing dashboard, folder, summary schema and row must already be provisioned.
Publication preserves version history. Dashboard and summary are not atomic; a
summary failure after the dashboard commit is reported as partial publication.
The legacy static statistics page is not regenerated: its patch categories use
the former heuristic model and would misstate the current evidence-based results.
"""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import email.utils
import hashlib
from html.parser import HTMLParser
import importlib
import importlib.metadata
import json
import logging
import math
import os
from pathlib import Path
import random
import re
import subprocess
import sys
import tempfile
import time
from urllib.parse import quote, unquote, urljoin, urlsplit
import uuid
import venv


GRAPH = "https://graph.microsoft.com"
DEFAULT_TENANT = ""
DEFAULT_CLIENT = ""
DEFAULT_SITE = ""
WINDOWS_RELEASE_HEALTH_URL = "https://learn.microsoft.com/en-us/windows/release-health/release-information"
WINDOWS_11_RELEASE_HEALTH_URL = "https://learn.microsoft.com/en-us/windows/release-health/windows11-release-information"
READ_SCOPES = ["DeviceManagementManagedDevices.Read.All",
               "DeviceManagementConfiguration.Read.All",
               "DeviceManagementServiceConfig.Read.All",
               "DeviceManagementApps.Read.All", "Directory.Read.All", "User.Read.All"]
FORBIDDEN = re.compile(r"productkey|recoverykey|password|token|secret", re.I)
MAX_BYTES = 50 * 1024 * 1024
SECTIONS = {
    "inventory": "section1_deviceInventory", "users": "section2_userIdentity",
    "security": "section5_securityRisk", "encryption": "section6_encryptionStatus",
    "compliance": "section7_complianceStatus", "patch": "section9_patchingUpdateReadiness",
    "autopilot": "section10_enrollmentAutopilot",
    "incidents": "section11_operationalIncidentIntelligence",
}
SUMMARY_TITLE = "WISE Endpoint Intelligence Summary"
SUMMARY_MARKER = "WISE Endpoint Intelligence source-backed statistics v2"
ROW_TITLE = "WISE Endpoint Intelligence Report Statistics"
INVENTORY_SELECT = (
    "id,deviceName,azureADDeviceId,serialNumber,operatingSystem,osVersion,model,"
    "manufacturer,managedDeviceOwnerType,managementState,deviceEnrollmentType,"
    "managementAgent,enrolledDateTime,lastSyncDateTime,managementCertificateExpirationDate,"
    "totalStorageSpaceInBytes,freeStorageSpaceInBytes,userPrincipalName,userDisplayName,"
    "userId,emailAddress,deviceRegistrationState,easActivated,easDeviceId,isEncrypted,"
    "isSupervised,jailBroken,complianceState,complianceGracePeriodExpirationDateTime,"
    "partnerReportedThreatState,imei,meid,wiFiMacAddress,androidSecurityPatchLevel,subscriberCarrier"
)


class WiseError(Exception):
    """A safe-to-display operational error, without response bodies or credentials."""


class GraphError(WiseError):
    """HTTP failure retaining only status, Graph error code, and request ID."""

    def __init__(self, status: int, code: str = "GraphFailure", request_id: str = ""):
        self.status = status
        self.code = re.sub(r"[^A-Za-z0-9_.-]", "", str(code))[:100]
        self.request_id = re.sub(r"[^A-Za-z0-9-]", "", str(request_id))[:80]
        super().__init__(f"HTTP {status}; code={self.code}; request-id={self.request_id or 'unavailable'}")


def canonical_uuid(value: str) -> str:
    """Accept only canonical UUIDs, not tenant domains or authority URLs."""
    try:
        parsed = str(uuid.UUID(value))
    except (ValueError, TypeError, AttributeError) as error:
        raise WiseError("Tenant and client IDs must be canonical UUIDs.") from error
    if parsed != value.lower() or parsed == str(uuid.UUID(int=0)):
        raise WiseError("Tenant and client IDs must be nonzero canonical UUIDs.")
    return parsed


def sanitize(node):
    """Recursively remove credential/product-key fields, including raw responses."""
    if isinstance(node, dict):
        return {key: sanitize(value) for key, value in node.items() if not FORBIDDEN.search(key)}
    if isinstance(node, list):
        return [sanitize(value) for value in node]
    return node


def json_bytes(node) -> bytes:
    return json.dumps(node, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")


def parse_date(value) -> dt.datetime | None:
    """Parse ISO timestamps and PowerShell /Date(milliseconds)/, never year one."""
    if not isinstance(value, str):
        return None
    legacy = re.fullmatch(r"/Date\((-?\d+)(?:[+-]\d{4})?\)/", value)
    try:
        if legacy:
            parsed = dt.datetime.fromtimestamp(int(legacy[1]) / 1000, dt.timezone.utc)
        elif re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})", value, re.I):
            parsed = dt.datetime.fromisoformat(value.upper().replace("Z", "+00:00")).astimezone(dt.timezone.utc)
        else:
            return None
        return parsed if parsed.year > 1 else None
    except (ValueError, OverflowError, OSError):
        return None


def iso(value: dt.datetime | None) -> str | None:
    return value.isoformat(timespec="milliseconds").replace("+00:00", "Z") if value else None


def identifier(value) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower()
    if not normalized or re.fullmatch(r"0+|0{8}-0{4}-0{4}-0{4}-0{12}|unknown|n/a|none|null|default string|to be filled by o\.e\.m\.|system serial number", normalized):
        return None
    return normalized


def unique_index(records: list, field: str) -> dict:
    result = {}
    for record in records:
        key = identifier(record.get(field))
        if key:
            if key in result:
                raise WiseError(f"Duplicate case-insensitive {field} in source records.")
            result[key] = record
    return result


def rows(report: dict, section: str, field: str) -> list:
    value = report.get(SECTIONS.get(section, section), {}).get(field, [])
    if not isinstance(value, list) or any(not isinstance(row, dict) for row in value):
        raise WiseError(f"Expected object array: {section}.{field}.")
    return value


def validate_report(report: dict) -> None:
    """Validate core sections, collection date, tenant, nonempty counts and IDs."""
    if not isinstance(report, dict) or not isinstance(report.get("metadata"), dict):
        raise WiseError("Report metadata is missing.")
    metadata = report["metadata"]
    canonical_uuid(metadata.get("tenant"))
    if parse_date(metadata.get("collectionTimestamp")) is None:
        raise WiseError("Invalid report collectionTimestamp.")
    for section in [*SECTIONS.values(), "endpointRiskHealthSummary", "crossTenantSummary"]:
        if not isinstance(report.get(section), dict) or not report[section]:
            raise WiseError(f"Missing core section: {section}.")
    inventory = rows(report, "inventory", "devices")
    if not inventory or type(metadata.get("totalDevices")) is not int or metadata["totalDevices"] != len(inventory):
        raise WiseError("Inventory is empty or metadata count does not reconcile.")
    if type(report[SECTIONS["inventory"]].get("totalCount")) is not int or report[SECTIONS["inventory"]]["totalCount"] != len(inventory):
        raise WiseError("Inventory totalCount does not reconcile.")
    if any(identifier(record.get("deviceId")) is None for record in inventory):
        raise WiseError("Inventory deviceId is missing or invalid.")
    unique_index(inventory, "deviceId")
    for section, field in [("users", "uniqueUsers"), ("security", "endpointSecurityPolicies"),
                           ("encryption", "windowsDeviceEncryption"), ("encryption", "mobileDeviceEncryption"),
                           ("compliance", "perDeviceCompliance"), ("patch", "osVersionAssessment"),
                           ("patch", "updateRings"), ("autopilot", "autopilotDevices")]:
        if field not in report[SECTIONS[section]]:
            raise WiseError(f"Missing core array: {section}.{field}.")
        records = rows(report, section, field)
        if field in ("perDeviceCompliance", "osVersionAssessment", "windowsDeviceEncryption"):
            unique_index(records, "deviceId")


class _ReleaseTableParser(HTMLParser):
    """Extract tables from Microsoft's release-health page without dependencies."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables = []
        self._table = None
        self._row = None
        self._cell = None
        self.headings = []
        self._heading = None
        self._summary = None

    def handle_starttag(self, tag, attrs):
        if tag == "summary":
            self._summary = []
        elif tag in ("h2", "h3", "h4"):
            self._heading = []
        elif tag == "table":
            self._table = []
            self.tables.append(self._table)
        elif tag == "tr" and self._table is not None:
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []

    def handle_data(self, data):
        if self._summary is not None and data.strip():
            self._summary.append(data.strip())
        if self._heading is not None and data.strip():
            self._heading.append(data.strip())
        if self._cell is not None and data.strip():
            self._cell.append(data.strip())

    def handle_endtag(self, tag):
        if tag == "summary" and self._summary is not None:
            self.headings.append(" ".join(self._summary))
            self._summary = None
        elif tag in ("h2", "h3", "h4") and self._heading is not None:
            self.headings.append(" ".join(self._heading))
            self._heading = None
        elif tag in ("td", "th") and self._cell is not None:
            self._row.append(" ".join(self._cell))
            self._cell = None
        elif tag == "tr" and self._row is not None:
            self._table.append(self._row)
            self._row = None
        elif tag == "table":
            self._table = None


def fetch_windows_release_catalog(transport=None) -> dict:
    """Read dated Microsoft build/support tables; fail closed if they change."""
    if transport is None:
        transport = importlib.import_module("requests").get
    branches = {}
    releases = collections.defaultdict(list)
    retrieved = []
    request = transport or importlib.import_module("requests").get
    for url in (WINDOWS_RELEASE_HEALTH_URL, WINDOWS_11_RELEASE_HEALTH_URL):
        try:
            response = request(url, timeout=30)
            response.raise_for_status()
            parser = _ReleaseTableParser()
            parser.feed(response.text)
        except Exception:
            raise WiseError("Microsoft Windows release catalog is unavailable; patch status is not evaluated.") from None
        retrieved.append(dict(url=url, modifiedAt=response.headers.get("Last-Modified")))
        for heading in parser.headings:
            match = re.search(r"Version\s+([^()]+)\s+\(OS build\s+(\d+)\).*End of updates", heading, re.I)
            if match:
                branches.setdefault(match[2], []).append(dict(version=match[1].strip(), support=["End of updates"]))
        for table in parser.tables:
            if not table:
                continue
            header = [cell.casefold() for cell in table[0]]
            if "latest build" in header and "version" in header:
                version_index, latest_index = header.index("version"), header.index("latest build")
                support_indices = [index for index, label in enumerate(header) if "support" in label or "end of updates" in label]
                for row in table[1:]:
                    if len(row) <= max([version_index, latest_index, *support_indices]):
                        continue
                    match = re.fullmatch(r"(?:10\.0\.)?(\d+)\.(\d+)", row[latest_index])
                    if not match:
                        continue
                    support = [row[index] for index in support_indices]
                    branches.setdefault(match[1], []).append(dict(version=row[version_index], support=support))
            elif header[:4] == ["servicing option", "update type", "availability date", "build"] and "kb article" in header:
                kb_index = header.index("kb article")
                for row in table[1:]:
                    if len(row) <= kb_index or not re.fullmatch(r"\d{4}-\d{2} B", row[1]):
                        continue
                    match = re.fullmatch(r"(\d+)\.(\d+)", row[3])
                    released = parse_date(row[2] + "T00:00:00Z")
                    if match and released:
                        releases[match[1]].append(dict(updateType=row[1], releaseDate=iso(released), build=int(match[2]),
                                                       kbArticle=row[kb_index], servicingOption=row[0]))
    if not branches or not releases or not {"19045", "26200", "26100"}.issubset(branches):
        raise WiseError("Microsoft release catalog structure changed; patch status is not evaluated.")
    return dict(source=[item["url"] for item in retrieved], retrievedAt=iso(dt.datetime.now(dt.timezone.utc)),
                branches=branches, releases=dict(releases))


def assess_windows_update(record: dict, catalog: dict, collected: dt.datetime) -> dict:
    """Compare a device against the latest released monthly B build on its branch."""
    if str(record.get("operatingSystem", "")).casefold() != "windows":
        return dict(patchStatus="Not applicable", supportStatus="Not applicable")
    parts = str(record.get("osVersion", "")).split(".")
    if len(parts) < 4 or parts[0:2] != ["10", "0"] or not parts[2].isdigit() or not parts[3].isdigit():
        return dict(patchStatus="Unknown build", supportStatus="Unknown")
    branch, installed_build = parts[2], int(parts[3])
    branch_records = catalog["branches"].get(branch)
    if not branch_records:
        return dict(patchStatus="Unknown build", supportStatus="Unknown", branchBuild=branch)
    if all(all(value.strip().casefold() == "end of updates" for value in item["support"]) for item in branch_records):
        return dict(patchStatus="Unsupported OS", supportStatus="Unsupported / ESU not verified", branchBuild=branch,
                    releaseVersion=branch_records[0]["version"], installedBuild=installed_build,
                    releaseCatalogSource=catalog["source"], releaseCatalogRetrievedAt=catalog["retrievedAt"])
    branch_releases = catalog["releases"].get(branch, [])
    eligible = [item for item in branch_releases if item["updateType"].startswith(collected.strftime("%Y-%m")) and
                dt.datetime.fromisoformat(item["releaseDate"].replace("Z", "+00:00")) <= collected]
    if not eligible:
        return dict(patchStatus="No monthly B release yet", supportStatus="Needs review", branchBuild=branch)
    required = max(eligible, key=lambda item: (item["build"], item["releaseDate"]))
    support_values = [value.strip().casefold() for release in branch_records for value in release["support"]]
    support_dates = [parse_date(match[0] + "T00:00:00Z") for value in support_values if (match := re.match(r"(\d{4}-\d{2}-\d{2})", value))]
    has_open_support = any(value > collected for value in support_dates)
    unsupported = bool(support_values) and all(value == "end of updates" for value in support_values)
    unsupported = unsupported or (bool(support_dates) and not has_open_support and "end of updates" not in support_values)
    edition_specific = ("end of updates" in support_values and has_open_support) or any("only" in value for value in support_values)
    status = "Unsupported OS" if unsupported else "Support status unknown" if edition_specific else "Compliant" if installed_build >= required["build"] else "Non-compliant"
    support_status = "Unsupported / ESU not verified" if unsupported else "Edition-specific; verify edition" if edition_specific else "Supported" if has_open_support else "Support status unavailable"
    return dict(patchStatus=status, supportStatus=support_status, branchBuild=branch, releaseVersion=branch_records[0]["version"],
                installedBuild=installed_build, requiredBuild=required["build"], requiredKB=required["kbArticle"],
                requiredReleaseDate=required["releaseDate"], requiredUpdateType=required["updateType"],
                releaseCatalogSource=catalog["source"], releaseCatalogRetrievedAt=catalog["retrievedAt"])


def _evidence_fields(record: dict, fields: tuple) -> dict:
    return {
        field: record[field] for field in fields if field in record
        and (record[field] is None or type(record[field]) in (str, int, bool))}


def _compliance_policy_evidence(records: list) -> list:
    identities = collections.Counter(identifier(record.get("id")) for record in records)
    result = []
    for record in records:
        projected = _evidence_fields(record, ("id", "displayName", "state"))
        if identities[identifier(record.get("id"))] > 1:
            projected.update(_evidence_fields(record, ("userId",) if isinstance(record.get("userId"), str)
                                              else ("userPrincipalName",)))
        if "settingStates" in record:
            settings = rows({"policy": record}, "policy", "settingStates")
            setting_ids = collections.Counter((setting.get("setting"), setting.get("settingName")) for setting in settings
                                             if all(value is None or isinstance(value, str) for value in
                                                    (setting.get("setting"), setting.get("settingName"))))
            projected["settingStates"] = []
            for setting in settings:
                detail = _evidence_fields(setting, ("setting", "settingName", "state", "errorCode", "errorDescription"))
                setting_id = (detail.get("setting"), detail.get("settingName"))
                if setting_ids[setting_id] > 1:
                    detail.update(_evidence_fields(setting, ("userId",) if isinstance(setting.get("userId"), str)
                                                   else ("userPrincipalName",)))
                projected["settingStates"].append(detail)
        result.append(projected)
    return result


def build_operational(report: dict) -> dict:
    """Pure schema-v2 port of build-operational/build-snapshot; no I/O or imports."""
    validate_report(report)
    source = sanitize(report)
    inventory = rows(source, "inventory", "devices")
    users = rows(source, "users", "uniqueUsers")
    compliance = unique_index(rows(source, "compliance", "perDeviceCompliance"), "deviceId")
    updates = unique_index(rows(source, "patch", "osVersionAssessment"), "deviceId")
    extra_details = unique_index(rows(source, "rawData", "extraDetails"), "deviceId")
    encryption = unique_index(rows(source, "encryption", "windowsDeviceEncryption"), "deviceId")
    raw_encryption = unique_index(rows(source, "rawData", "encryption"), "id")
    autopilot = rows(source, "autopilot", "autopilotDevices")
    rings = rows(source, "patch", "updateRings")
    patch_section = source[SECTIONS["patch"]]
    patch_summary = patch_section.get("windowsPatchSummary", {})
    user_ids = {identifier(user.get("userId")) for user in users} - {None}
    collected = parse_date(source["metadata"]["collectionTimestamp"])
    collection_date = iso(collected)
    metrics = collections.Counter()
    metrics.update({"devices": len(inventory), "users": len(users), "autopilot": len(autopilot), "updateRings": len(rings)})
    metric_keys = ["compliant", "noncompliant", "configManager", "complianceOther", "encrypted", "unencrypted",
                   "encryptionUnknown", "stale", "notStale", "staleUnknown", "activityKnown", "activityUnknown",
                   "storageMeasured", "storageUnknown", *[f"storageBelow{limit}" for limit in [2, 5, 10, 15]],
                   *[f"threat.{state}" for state in ["secured", "low", "medium", "high", "unknown", "deactivated"]],
                   *[f"user.{state}" for state in ["missing", "matched", "orphan"]]]
    metrics.update(dict.fromkeys(metric_keys, 0))
    distributions = {name: collections.Counter() for name in ["os", "manufacturer", "compliance", "patch", "threat", "management"]}
    exceptions, exception_counts = [], collections.Counter()
    verified_patch_rows = unique_index(patch_section.get("windowsPatchDevices", []), "deviceId")
    evaluated = managed = 0
    threat_map = {"secured": "secured", "lowseverity": "low", "mediumseverity": "medium", "highseverity": "high", "deactivated": "deactivated"}
    for record in inventory:
        key = identifier(record["deviceId"])
        detail, update = compliance.get(key), updates.get(key)
        last_sync = parse_date(record.get("lastSyncDateTime"))
        elapsed = (collected - last_sync).total_seconds() if collected and last_sync else None
        days = math.floor(elapsed / 86400) if elapsed is not None and elapsed >= 0 else None
        total, free = record.get("totalStorageSpaceGB"), record.get("freeStorageSpaceGB")
        measured = all(type(value) in (int, float) and math.isfinite(value) for value in [total, free])
        measured = measured and total > 0 and 0 <= free <= total
        percent = free / total * 100 if measured else None
        user_id = identifier(record.get("userId"))
        user_status = "missing" if not user_id else "matched" if user_id in user_ids else "orphan"
        threat = threat_map.get(str(record.get("partnerReportedThreatState", "")).lower(), "unknown")
        verified_patch = verified_patch_rows.get(key)
        patch = (verified_patch or {}).get("patchStatus") or "Unknown"
        if verified_patch and update:
            update.update(verified_patch)
        stale = record.get("staleFlag") if type(record.get("staleFlag")) is bool else None
        derived = dict(collectionDate=collection_date, lastSyncDate=iso(last_sync), daysInactive=days,
                       activityStatus="unknown" if days is None else "known", staleFlag=stale,
                       userStatus=user_status, freeStoragePercent=percent, threatStatus=threat, patchStatus=patch)
        state = str(record.get("complianceState", "")).lower()
        metrics[{"compliant": "compliant", "noncompliant": "noncompliant", "configmanager": "configManager"}.get(state, "complianceOther")] += 1
        evaluated += state in ["compliant", "noncompliant", "ingraceperiod", "error", "unknown", "conflict"]
        encrypted = record.get("isEncrypted")
        metrics["encrypted" if encrypted is True else "unencrypted" if encrypted is False else "encryptionUnknown"] += 1
        metrics["stale" if stale is True else "notStale" if stale is False else "staleUnknown"] += 1
        metrics["activityUnknown" if days is None else "activityKnown"] += 1
        metrics["storageMeasured" if measured else "storageUnknown"] += 1
        metrics[f"user.{user_status}"] += 1
        metrics[f"threat.{threat}"] += 1
        for limit in [2, 5, 10, 15]:
            metrics[f"storageBelow{limit}"] += percent is not None and percent < limit
        managed += record.get("managementState") == "managed"
        labels = dict(os=record.get("operatingSystem"), manufacturer=record.get("manufacturer"),
                      compliance=record.get("complianceState"), patch=patch, threat=threat, management=record.get("managementAgent"))
        for name, label in labels.items():
            distributions[name][label if isinstance(label, str) and label.strip() else "Unknown"] += 1
        tests = [
            ("noncompliant", state == "noncompliant"), ("unencrypted", encrypted is False), ("stale", stale is True),
            ("lowStorage", percent is not None and percent < 10), ("criticalStorage", percent is not None and percent < 2),
            ("threat", threat in ["low", "medium", "high"]), ("threatGap", threat in ["unknown", "deactivated"]),
            ("update", patch in ["Non-compliant", "Unsupported OS"]),
            ("ownership", user_status in ["missing", "orphan"]), ("certificate", record.get("managementCertificateExpired") is True),
            ("management", record.get("managementState") in ["retirePending", "wipePending", "wipeFailed"]),
            ("jailbroken", str(record.get("jailBroken", "")).lower() == "true")]
        flags = [name for name, condition in tests if condition]
        exception_counts.update(flags)
        if flags:
            extra = extra_details.get(key, {})
            if "compliancePolicyStates" in extra:
                detail = {**(detail or {}), "policyStates": _compliance_policy_evidence(
                    rows({"device": extra}, "device", "compliancePolicyStates"))}
            encryption_detail = _evidence_fields(encryption.get(key, {}), (
                "encryptionStatus", "encryptionReadiness", "tpmVersion"))
            encryption_detail.update(_evidence_fields(raw_encryption.get(key, {}), (
                "encryptionReadinessState", "encryptionState", "encryptionPolicySettingState", "tpmSpecificationVersion")))
            for evidence, field in ((encryption.get(key, {}), "bitLockerState"),
                                    (raw_encryption.get(key, {}), "advancedBitLockerStates")):
                value = evidence.get(field)
                if field in evidence and (value is None or isinstance(value, str)
                                          or isinstance(value, list) and all(isinstance(item, str) for item in value)):
                    encryption_detail[field] = value
            exceptions.append({**record, "complianceDetail": detail, "updateDetail": update, "derived": derived,
                               **({"encryptionDetail": encryption_detail} if encryption_detail else {}),
                               "issues": flags, "usedStorageSpaceGB": max(0, total - free) if all(type(value) in (int, float) and math.isfinite(value) for value in [total, free]) else None})
    def metric(key, label, numerator, denominator, denominator_label):
        return dict(key=key, label=label, numerator=numerator, denominator=denominator,
                    denominatorLabel=denominator_label, percentage=round(numerator / denominator * 100, 1) if denominator else None)
    health = [
        metric("compliance", "Compliance", metrics["compliant"], evaluated, "Intune compliance cohort; excludes Configuration Manager"),
        metric("encryption", "Encryption", metrics["encrypted"], metrics["encrypted"] + metrics["unencrypted"], "Devices with explicit encryption state"),
        metric("patch", "Windows monthly quality patch compliance", patch_summary.get("compliant", 0), patch_summary.get("evaluated", 0), "Supported Windows devices with a published Microsoft monthly B baseline"),
        metric("threat", "Secured threat reports", metrics["threat.secured"], sum(metrics[f"threat.{name}"] for name in ["secured", "low", "medium", "high"]), "Secured or severity-reported devices; unknown/deactivated excluded"),
        metric("activity", "Not flagged stale", metrics["notStale"], metrics["notStale"] + metrics["stale"], "Devices with an explicit source stale flag"),
        metric("management", "Managed state", managed, len(inventory), "All source devices")]
    ap_exceptions = [row for row in autopilot if str(row.get("enrollmentState", "")).lower() != "enrolled"]
    percentages = {name: metric(name, name, count, metrics["storageMeasured"] if name in ["lowStorage", "criticalStorage"] else len(inventory),
                               "Devices with valid storage measurements" if name in ["lowStorage", "criticalStorage"] else "All source devices")
                   for name, count in exception_counts.items()}
    return dict(schemaVersion=2, metadata=source["metadata"],
                summary=dict(totalDevices=len(inventory), health=health, exceptionCounts=dict(exception_counts),
                             percentages=percentages, metrics=dict(metrics), evaluatedDevices=evaluated, exceptionDevices=len(exceptions),
                             complianceCoverage=metric("coverage", "Compliance coverage", evaluated, len(inventory), "All source devices"),
                             autopilotRegistered=len(autopilot), autopilotEnrolled=len(autopilot) - len(ap_exceptions), autopilotExceptions=len(ap_exceptions)),
                devices=exceptions, autopilot=ap_exceptions, updateRings=rings, windowsPatchSummary=patch_summary,
                windowsPatchDevices=patch_section.get("windowsPatchDevices", []),
                distributions={name: [dict(label=label, count=count) for label, count in sorted(counts.items(), key=lambda item: (-item[1], item[0].casefold()))] for name, counts in distributions.items()},
                policySummary=source[SECTIONS["compliance"]].get("tenantComplianceSummary"),
                coverage=dict(sourceSha256=hashlib.sha256(json_bytes(source)).hexdigest(), collectionDate=collection_date,
                              sourceHashEncoding="SHA-256 of compact UTF-8 sanitized Python JSON, insertion order; not source file bytes.",
                              mode="Management aggregates and operational exception records only. No full device inventory or user directory.",
                              storage="Device-wide inventory; drive letter is not identified. Percentages use source free/total GB.",
                              risk="No authoritative composite score; legacy local risk is heuristic only.",
                              compliance="Unknown and conflict retained in evaluated cohort; configManager excluded.",
                              stale="Explicit source flags at collection time; invalid/future last-sync is unknown activity.",
                              autopilot="Only registrations not explicitly enrolled appear in exception details.",
                              patching="Windows quality percentage compares the installed servicing-branch build with Microsoft's monthly B release published on or before collection time. Unsupported, unknown, non-Windows and unevaluated devices are excluded from the evaluated denominator.",
                              collectionStatus=source.get("collectionStatus", {"status": "imported-unverified", "note": "Legacy exports may silently omit failed sources."})))


class GraphClient:
    """Inject session.request-compatible transport and a callable bearer provider.

    request(method, path, *, headers=None, data=None, json_body=None) returns a
    response object. Paths are Graph-relative (beta default) or exact Graph URLs.
    get/get_paged return validated JSON objects/arrays. download returns bytes.
    Transport timeout defaults to 60 seconds; redirects are explicitly validated.
    """

    def __init__(self, token_provider, transport=None, sleep=time.sleep):
        try:
            self.request_timeout = int(os.environ.get("WISE_GRAPH_REQUEST_TIMEOUT_SECONDS", "60"))
        except ValueError:
            raise WiseError("WISE_GRAPH_REQUEST_TIMEOUT_SECONDS must be an integer from 10 to 300.") from None
        if not 10 <= self.request_timeout <= 300:
            raise WiseError("WISE_GRAPH_REQUEST_TIMEOUT_SECONDS must be an integer from 10 to 300.")
        if transport is None:
            transport = importlib.import_module("requests").Session().request
        self.transport, self.token_provider, self.sleep = transport, token_provider, sleep

    @staticmethod
    def graph_url(path: str) -> str:
        relative = path.lstrip('/')
        url = path if "://" in path else f"{GRAPH}/{relative if relative.startswith(('v1.0/', 'beta/')) else 'beta/' + relative}"
        parsed = urlsplit(url)
        if parsed.scheme != "https" or parsed.netloc.lower() != "graph.microsoft.com" or not parsed.path.startswith(("/beta/", "/v1.0/")) or parsed.fragment:
            raise WiseError("Refusing non-Graph URL or invalid Graph API path.")
        return url

    @staticmethod
    def failure(response) -> GraphError:
        try:
            payload = response.json()
            code = payload.get("error", {}).get("code", "GraphFailure")
        except (ValueError, AttributeError, TypeError):
            code = "GraphFailure"
        return GraphError(response.status_code, code, response.headers.get("request-id", ""))

    @staticmethod
    def retry_delay(header: str | None, attempt: int) -> float:
        try:
            seconds = float(header)
        except (ValueError, TypeError):
            try:
                seconds = (email.utils.parsedate_to_datetime(header) - dt.datetime.now(dt.timezone.utc)).total_seconds()
            except (ValueError, TypeError, OverflowError):
                seconds = 2 ** attempt + random.random()
        return max(0, seconds) if math.isfinite(seconds) else 120

    def request(self, method: str, path: str, *, headers=None, data=None, json_body=None):
        url = self.graph_url(path)
        method = method.upper()
        if headers and any(key.lower() in ["authorization", "host"] for key in headers):
            raise WiseError("Caller cannot override Graph authorization or host.")
        configuration_read = method == "GET" and bool(re.search(r"/managedDevices/[^/]+/deviceConfigurationStates(?:\?|$)", url))
        attempts = 10 if configuration_read else 6
        configuration_wait = 0.0
        if configuration_read:
            self.sleep(2)
        for attempt in range(attempts):
            request_headers = {"Authorization": f"Bearer {self.token_provider()}", "Accept": "application/json", **(headers or {})}
            try:
                response = self.transport(method, url, headers=request_headers, data=data, json=json_body, timeout=self.request_timeout, allow_redirects=False)
            except Exception as error:
                if method in ["GET", "HEAD"] and attempt < attempts - 1:
                    self.sleep(self.retry_delay(None, attempt))
                    continue
                raise WiseError("Graph transport failed; write outcome may be uncertain." if method not in ["GET", "HEAD"] else "Graph transport failed.") from None
            if 200 <= response.status_code < 300:
                return response
            retryable = response.status_code == 429 or (response.status_code in [502, 503, 504] and method in ["GET", "HEAD", "PUT"])
            if retryable and attempt < attempts - 1:
                delay = self.retry_delay(response.headers.get("Retry-After"), attempt)
                if configuration_read and response.status_code == 429:
                    delay = max(delay, min(120, 10 * 2 ** attempt) + random.random())
                    if configuration_wait + delay > 180:
                        raise self.failure(response)
                    configuration_wait += delay
                logging.getLogger("wise.graph").warning("Graph retry: category=%s; HTTP=%s; attempt=%s/%s; delaySeconds=%.1f", "configurationDetails" if configuration_read else "graphRequest", response.status_code, attempt + 1, attempts, delay)
                self.sleep(delay)
                continue
            raise self.failure(response)
        raise WiseError("Retry budget exhausted.")

    def get(self, path: str) -> dict:
        try:
            value = self.request("GET", path).json()
        except (ValueError, TypeError):
            raise WiseError("Graph returned invalid JSON.") from None
        if not isinstance(value, dict):
            raise WiseError("Graph returned a non-object JSON response.")
        return value

    def get_paged(self, path: str) -> list:
        result, seen = [], set()
        next_link = self.graph_url(path)
        for page_number in range(10000):
            if next_link in seen:
                raise WiseError("Graph pagination loop detected.")
            seen.add(next_link)
            payload = self.get(next_link)
            if not isinstance(payload.get("value"), list) or any(not isinstance(item, dict) for item in payload["value"]):
                raise WiseError("Graph collection must contain a value object array.")
            result.extend(payload["value"])
            next_link = payload.get("@odata.nextLink")
            if not next_link:
                return result
            if not isinstance(next_link, str):
                raise WiseError("Invalid Graph nextLink.")
            next_link = self.graph_url(next_link)
        raise WiseError("Graph pagination exceeded 10000 pages.")

    def download(self, path: str) -> bytes:
        """Follow only HTTPS Microsoft file redirects, never forwarding bearer tokens."""
        url = self.graph_url(path)
        for redirect in range(6):
            is_graph = urlsplit(url).netloc.lower() == "graph.microsoft.com"
            headers = {"Authorization": f"Bearer {self.token_provider()}"} if is_graph else {}
            for attempt in range(6):
                try:
                    response = self.transport("GET", url, headers=headers, timeout=self.request_timeout, allow_redirects=False)
                except Exception:
                    if attempt < 5:
                        self.sleep(self.retry_delay(None, attempt))
                        continue
                    raise WiseError("File verification download failed.") from None
                if response.status_code in [429, 502, 503, 504] and attempt < 5:
                    self.sleep(self.retry_delay(response.headers.get("Retry-After"), attempt))
                    continue
                break
            if response.status_code in [301, 302, 303, 307, 308]:
                url = urljoin(url, response.headers.get("Location", ""))
                parsed = urlsplit(url)
                host = parsed.hostname or ""
                trusted = host == "graph.microsoft.com" or any(host.endswith(suffix) for suffix in [".sharepoint.com", ".sharepointonline.com", ".1drv.com"])
                if parsed.scheme != "https" or parsed.port not in [None, 443] or parsed.username or parsed.password or not trusted:
                    raise WiseError("Unsafe file download redirect blocked.")
                if host == "graph.microsoft.com":
                    self.graph_url(url)
                continue
            if not 200 <= response.status_code < 300:
                raise self.failure(response)
            if len(response.content) > MAX_BYTES:
                raise WiseError("Downloaded snapshot exceeds 50 MiB.")
            return response.content
        raise WiseError("Too many file download redirects.")


class AuthSession:
    """One public-client identity, memory cache, silent refresh before browser login."""

    def __init__(self, tenant_id: str, client_id: str, scopes: list[str], username=None, auth="browser"):
        self.tenant_id = canonical_uuid(tenant_id)
        self.scopes, self.username, self.auth = scopes, username, auth
        if auth == "client-credentials":
            secret = os.environ.get("WISE_CLIENT_SECRET", "")
            if not secret.strip():
                raise WiseError("Missing required app setting WISE_CLIENT_SECRET. Supply the secret value, not its ID.")
            identity = importlib.import_module("azure.identity")
            self.credential = identity.ClientSecretCredential(tenant_id=self.tenant_id, client_id=canonical_uuid(client_id), client_secret=secret)
            return
        if auth == "managed-identity":
            identity = importlib.import_module("azure.identity")
            managed_client_id = os.environ.get("WISE_MANAGED_IDENTITY_CLIENT_ID")
            self.credential = identity.ManagedIdentityCredential(client_id=canonical_uuid(managed_client_id)) if managed_client_id else identity.ManagedIdentityCredential()
            return
        msal = importlib.import_module("msal")
        options = {"enable_broker_on_windows": True} if auth == "broker" else {}
        self.app = msal.PublicClientApplication(canonical_uuid(client_id), authority=f"https://login.microsoftonline.com/{self.tenant_id}", token_cache=msal.TokenCache(), **options)
        self.account_id = None

    def token(self) -> str:
        if self.auth in ["managed-identity", "client-credentials"]:
            try:
                return self.credential.get_token("https://graph.microsoft.com/.default").token
            except Exception:
                raise WiseError("Application Graph authentication failed; check tenant, credential configuration and application permissions. No interactive fallback.") from None
        accounts = self.app.get_accounts(username=self.username) if self.username else self.app.get_accounts()
        if self.account_id:
            accounts = [account for account in accounts if account.get("home_account_id") == self.account_id]
        if len(accounts) > 1:
            raise WiseError("Multiple accounts available; specify --username. No account was guessed.")
        account = accounts[0] if accounts else None
        result = self.app.acquire_token_silent(self.scopes, account=account) if account else None
        verified_account = bool(account and self.account_id and account.get("home_account_id") == self.account_id and hasattr(self, "identity"))
        if result and "access_token" in result and not result.get("id_token_claims") and not verified_account:
            result = None
        if not result or "access_token" not in result:
            options = dict(scopes=self.scopes, login_hint=self.username, timeout=180)
            if self.auth == "broker":
                options["parent_window_handle"] = self.app.CONSOLE_WINDOW_HANDLE
            if account:
                options["login_hint"] = account.get("username") or self.username
            result = self.app.acquire_token_interactive(**options)
        if not result or "access_token" not in result:
            raise WiseError("Delegated authentication failed or timed out; check public-client redirects, consent and policy. No device-code fallback.")
        claims = result.get("id_token_claims", {})
        if claims:
            if str(claims.get("tid", "")).lower() != self.tenant_id:
                raise WiseError("Authenticated tenant does not match configured tenant.")
            identity = (claims.get("oid"), claims.get("sub"))
            if hasattr(self, "identity") and self.identity != identity:
                raise WiseError("Authenticated identity changed during the run.")
            if not any(identity):
                raise WiseError("Authenticated identity claims are unavailable.")
            self.identity = identity
            available = self.app.get_accounts()
            matching = [account for account in available if account.get("local_account_id") == claims.get("oid")]
            if len(matching) == 1:
                self.account_id = matching[0].get("home_account_id")
        elif not verified_account:
            raise WiseError("Authenticated identity claims are unavailable.")
        granted = set(str(result.get("scope", "")).lower().split())
        if granted and not {scope.lower() for scope in self.scopes}.issubset(granted):
            raise WiseError("Required delegated Graph scopes were not granted.")
        return result["access_token"]


def bootstrap_dependencies(auth: str = "browser", no_install: bool = False) -> None:
    """Live-only bootstrap; wait for our private venv, never install globally."""
    if auth == "managed-identity":
        try:
            for module in ["requests", "azure.identity", "azure.storage.blob"]:
                importlib.import_module(module)
        except ImportError:
            raise WiseError("Azure execution requires deployment-time installation of requirements.txt; runtime installation and interactive sign-in are disabled in managed-identity mode.") from None
        return
    if auth == "broker" and os.name != "nt":
        raise WiseError("--auth broker is supported only on Windows.")
    target = Path(__file__).resolve().parent / ".wise-python" / "venv"
    interpreter = target / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    in_private = Path(sys.prefix).resolve() == target.resolve()
    requirements = ["msal>=1.31,<2", "requests>=2.32,<3", "azure-identity>=1.17,<2", "azure-storage-blob>=12.23,<13"]
    if auth == "broker":
        requirements.append("msal[broker]>=1.31,<2")
    def ready():
        for name, minimum, ceiling in [("msal", (1, 31), 2), ("requests", (2, 32), 3), ("azure-identity", (1, 17), 2), ("azure-storage-blob", (12, 23), 13)]:
            try:
                version = tuple(int(part) for part in importlib.metadata.version(name).split(".")[:2])
            except (importlib.metadata.PackageNotFoundError, ValueError):
                return False
            if version < minimum or version[0] >= ceiling:
                return False
        if auth == "broker":
            try:
                importlib.metadata.version("pymsalruntime")
            except importlib.metadata.PackageNotFoundError:
                return False
        return True
    if in_private and ready():
        return
    instructions = f'Use the private interpreter "{interpreter}" -m pip install --index-url https://pypi.org/simple ' + " ".join(requirements)
    if no_install:
        raise WiseError("Live mode requires the ready private environment. " + instructions + "; then run this script with that interpreter.")
    if os.environ.get("WISE_BOOTSTRAPPED"):
        raise WiseError("Private dependency bootstrap did not produce a usable environment. " + instructions)
    if not interpreter.exists():
        venv.EnvBuilder(with_pip=True).create(target)
    try:
        subprocess.check_call([str(interpreter), "-m", "pip", "install", "--index-url", "https://pypi.org/simple", *requirements], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except (OSError, subprocess.CalledProcessError):
        raise WiseError("Private dependency installation failed. " + instructions) from None
    environment = dict(os.environ, WISE_BOOTSTRAPPED="1")
    print("  Running in private Python environment; waiting for the report process to finish...", flush=True)
    return_code = subprocess.call([str(interpreter), "-u", str(Path(__file__).resolve()), *sys.argv[1:]], env=environment)
    raise SystemExit(return_code)


def collect_report(client: GraphClient, tenant_id: str, extra_details: bool = False) -> dict:
    """Collect every PS data source, preserve sanitized raw data and coverage."""
    tenant_id = canonical_uuid(tenant_id)
    now = dt.datetime.now(dt.timezone.utc)
    timestamp = iso(now)
    statuses = []
    throttled_categories = set()
    def fetch(name, path, paged=True, unsupported=False):
        category = name.split(":", 1)[0] if name.startswith(("complianceDetails:", "configurationDetails:", "hardware:")) else None
        if category in throttled_categories:
            statuses.append(dict(source=name, status="unavailable", httpStatus=429, code="OptionalDetailsThrottled"))
            return [] if paged else {}
        try:
            data = client.get_paged(path) if paged else client.get(path)
            count = len(data) if paged else 1
            statuses.append(dict(source=name, status="ok", records=count))
            print(f"  Graph source {name}: ok ({count} records).", flush=True)
            return data
        except GraphError as error:
            if error.status == 401:
                raise WiseError(f"Graph authentication was rejected (HTTP 401; source={name}; request-id={error.request_id or 'unavailable'}). Collection stopped; no archive or SharePoint publication started. For local browser mode check the delegated app, signed-in account and tenant access; Azure application authentication is configured separately.") from None
            transient = category is not None and error.status in [429, 500, 502, 503, 504]
            if transient:
                throttled_categories.add(category)
                logging.getLogger("wise.graph").warning("Graph %s remain unavailable (HTTP %s); skipping remaining requests in this category for this collection", category, error.status)
            status = "unavailable" if transient or (unsupported and error.status in [400, 404]) else "error"
            statuses.append(dict(source=name, status=status, httpStatus=error.status, code=error.code, requestId=error.request_id))
            print(f"  Graph source {name}: {status} (HTTP {error.status}, {error.code or 'no error code'}).", flush=True)
        except WiseError as error:
            statuses.append(dict(source=name, status="unavailable" if category else "error", code="InvalidOrUnavailableResponse", diagnostic=str(error)))
            print(f"  Graph source {name}: error ({error}).", flush=True)
        return [] if paged else {}
    def section(source, **values):
        return dict(sourceSystem=source, collectionTimestamp=timestamp, **values)
    default_devices = fetch("managedDevices.default", "deviceManagement/managedDevices")
    selected_devices = fetch("managedDevices.selected", "deviceManagement/managedDevices?$select=" + INVENTORY_SELECT)
    selected_index = unique_index(selected_devices, "id")
    if set(selected_index) != set(unique_index(default_devices, "id")):
        statuses.append(dict(source="managedDevices.reconciliation", status="error", code="InventoryChangedDuringCollection"))
    raw_devices = [{**record, **selected_index.get(identifier(record.get("id")), {})} for record in default_devices]
    inventory = []
    for raw in raw_devices:
        record = {key: value for key, value in raw.items() if key not in ["id", "@odata.context"]}
        record["deviceId"] = raw.get("id")
        for field in ["enrolledDateTime", "lastSyncDateTime", "managementCertificateExpirationDate", "complianceGracePeriodExpirationDateTime"]:
            parsed = parse_date(raw.get(field))
            record[field] = iso(parsed) if parsed and (field != "lastSyncDateTime" or parsed <= now) else None
        total, free = raw.get("totalStorageSpaceInBytes"), raw.get("freeStorageSpaceInBytes")
        valid = all(type(value) in (int, float) and math.isfinite(value) for value in [total, free])
        valid = valid and total > 0 and 0 <= free <= total
        record.update(totalStorageSpaceGB=total / 2**30 if valid else None,
                      freeStorageSpaceGB=free / 2**30 if valid else None, freeStoragePercent=free / total * 100 if valid else None)
        last = parse_date(record["lastSyncDateTime"])
        days = (now - last).days if last else None
        record.update(daysInactive=days, staleFlag=days > 30 if days is not None else None)
        certificate = parse_date(record["managementCertificateExpirationDate"])
        if certificate and certificate < now:
            record["managementCertificateExpired"] = True
        if valid and free / total * 100 < 35:
            record["lowDiskSpaceFlag"] = True
        build = windows_build(record)
        if build and build < 22000:
            record["criticalFlag_Windows10EOL"] = True
        if not record.get("userPrincipalName"):
            record["orphanDevice"] = True
        inventory.append(record)
    grouped = collections.defaultdict(list)
    for record in inventory:
        grouped[identifier(record.get("userId")) or record.get("userPrincipalName") or None].append(record)
    users = []
    for key, group in grouped.items():
        first = group[0]
        user = dict(userDisplayName=first.get("userDisplayName") if key else "UNASSIGNED",
                    userPrincipalName=first.get("userPrincipalName"), userId=first.get("userId"),
                    deviceCount=len(group), associatedDevices=[record.get("deviceName") for record in group])
        if key is None:
            user["orphanDeviceFlag"] = True
        if len(group) > 1:
            user["multiDeviceFlag"] = True
        noncompliant = [record.get("deviceName") for record in group if str(record.get("complianceState", "")).lower() == "noncompliant"]
        if noncompliant:
            user["nonCompliantDevices"] = noncompliant
        users.append(user)
    threats, raw_malware = [], []
    for record in inventory:
        if str(record.get("operatingSystem", "")).lower() == "windows":
            malware = fetch("malware:" + record["deviceId"], f"deviceManagement/managedDevices/{quote(record['deviceId'], safe='')}/windowsProtectionState/detectedMalwareState", unsupported=True)
            raw_malware.append(dict(deviceId=record["deviceId"], records=malware))
            threats.extend(dict(device=record.get("deviceName"), deviceId=record["deviceId"], threat=entry.get("displayName"), state=entry.get("threatState"))
                           for entry in malware if entry.get("threatState") in ["active", "actionFailed", "quarantined", "removalFailed"])
    overview = fetch("managedDeviceOverview", "deviceManagement/managedDeviceOverview", False)
    intents = fetch("endpointSecurityIntents", "deviceManagement/intents?$select=id,displayName,isAssigned,templateId,lastModifiedDateTime")
    policies = []
    for entry in intents:
        policy = {**entry, "policyId": entry.get("id")}
        modified = parse_date(entry.get("lastModifiedDateTime"))
        if modified and now.year - modified.year >= 3:
            policy.update(ageYears=now.year - modified.year, flag="Policy last modified 3+ calendar years ago")
        if entry.get("isAssigned") is False:
            policy["flag"] = "Not assigned"
        policies.append(policy)
    raw_encryption = fetch("managedDeviceEncryptionStates", "deviceManagement/managedDeviceEncryptionStates")
    encryption = []
    for entry in raw_encryption:
        state = str(entry.get("encryptionState", "")).lower()
        readiness = str(entry.get("encryptionReadinessState", "")).lower()
        policy_details = entry.get("policyDetails")
        policy_states = ([detail.get("state") for detail in policy_details if isinstance(detail, dict) and detail.get("state")]
                         if isinstance(policy_details, list) else [policy_details.get("state")] if isinstance(policy_details, dict) and policy_details.get("state") else [])
        encryption.append(dict(deviceId=entry.get("managedDeviceId") or entry.get("id"), deviceName=entry.get("deviceName"),
                               encryptionStatus="Encrypted" if state == "encrypted" else "Not encrypted" if state in ["notencrypted", "unencrypted"] else "Unknown",
                               bitLockerState=entry.get("advancedBitLockerStates") or policy_states or "Unknown",
                               encryptionReadiness="Ready" if readiness == "ready" else "Not ready" if readiness == "notready" else "Unknown",
                               tpmVersion=entry.get("tpmSpecificationVersion"), userPrincipalName=entry.get("userPrincipalName")))
    mobile = [dict(deviceName=record.get("deviceName"), deviceId=record["deviceId"], isEncrypted=record.get("isEncrypted"), platform=record.get("operatingSystem"))
              for record in inventory if record.get("operatingSystem") in ["iOS", "Android"]]
    enc_summary = dict(windowsEncryptedCount=sum(entry["encryptionStatus"] == "Encrypted" for entry in encryption),
                       windowsNotEncryptedCount=sum(entry["encryptionStatus"] == "Not encrypted" for entry in encryption),
                       windowsUnknownCount=sum(entry["encryptionStatus"] == "Unknown" for entry in encryption),
                       windowsBitLockerPolicyMismatchCount=sum("mismatch" in str(entry["bitLockerState"]).lower() for entry in encryption),
                       windowsNotEncryptionReadyCount=sum(entry["encryptionReadiness"] == "Not ready" for entry in encryption),
                       mobileEncryptedCount=sum(entry["isEncrypted"] is True for entry in mobile))
    policy_summary = fetch("policyComplianceSummary", "deviceManagement/deviceCompliancePolicyDeviceStateSummary", False)
    compliance = []
    for record in inventory:
        entry = dict(deviceName=record.get("deviceName"), deviceId=record["deviceId"], complianceState=record.get("complianceState"), platform=record.get("operatingSystem"), userPrincipalName=record.get("userPrincipalName"))
        if record.get("managementState") == "retirePending":
            entry["managementState"] = "retirePending"
        grace = parse_date(record.get("complianceGracePeriodExpirationDateTime"))
        if grace and str(record.get("complianceState", "")).lower() != "compliant":
            entry.update(gracePeriodExpiry=grace.date().isoformat(), gracePeriodExpired=grace < now)
        compliance.append(entry)
    configurations = fetch("deviceConfigurations", "deviceManagement/deviceConfigurations")
    rings = []
    for entry in configurations:
        if "windowsUpdateForBusinessConfiguration" in entry.get("@odata.type", ""):
            ring = dict(ringId=entry.get("id"), displayName=entry.get("displayName"), qualityDeferralDays=entry.get("qualityUpdatesDeferralPeriodInDays"),
                        featureDeferralDays=entry.get("featureUpdatesDeferralPeriodInDays"), qualityPaused=entry.get("qualityUpdatesPaused"), featurePaused=entry.get("featureUpdatesPaused"),
                        automaticUpdateMode=entry.get("automaticUpdateMode"), microsoftUpdateServiceAllowed=entry.get("microsoftUpdateServiceAllowed"), driversExcluded=entry.get("driversExcluded"),
                        deadlineForQualityUpdatesInDays=entry.get("deadlineForQualityUpdatesInDays"), deadlineForFeatureUpdatesInDays=entry.get("deadlineForFeatureUpdatesInDays"),
                        deadlineGracePeriodInDays=entry.get("deadlineGracePeriodInDays"), postponeRebootUntilAfterDeadline=entry.get("postponeRebootUntilAfterDeadline"),
                        qualityUpdatesPauseExpiryDateTime=iso(parse_date(entry.get("qualityUpdatesPauseExpiryDateTime"))),
                        featureUpdatesPauseExpiryDateTime=iso(parse_date(entry.get("featureUpdatesPauseExpiryDateTime"))))
            for source_key, target_key in [("allowWindows11Upgrade", "allowWindows11Upgrade"), ("businessReadyUpdatesOnly", "channel"), ("featureUpdatesPauseStartDate", "featureUpdatesPauseStartDate")]:
                if source_key in entry:
                    ring[target_key] = entry[source_key]
            if ring.get("featurePaused"):
                ring["flag"] = "Feature updates paused; inspect reported pause start date"
            rings.append(ring)
    feature_profiles = fetch("windowsFeatureUpdateProfiles", "deviceManagement/windowsFeatureUpdateProfiles?$expand=assignments")
    feature_profiles = [dict(profileId=profile.get("id"), displayName=profile.get("displayName"), featureUpdateVersion=profile.get("featureUpdateVersion"),
                             endOfSupportDate=iso(parse_date(profile.get("endOfSupportDate"))), deployableContentDisplayName=profile.get("deployableContentDisplayName"),
                             installFeatureUpdatesOptional=profile.get("installFeatureUpdatesOptional"), rolloutSettings=profile.get("rolloutSettings"),
                             assignments=profile.get("assignments", [])) for profile in feature_profiles]
    quality_profiles = fetch("windowsQualityUpdateProfiles", "deviceManagement/windowsQualityUpdateProfiles?$expand=assignments")
    quality_profiles = [dict(profileId=profile.get("id"), displayName=profile.get("displayName"), releaseDateDisplayName=profile.get("releaseDateDisplayName"),
                             deployableContentDisplayName=profile.get("deployableContentDisplayName"), expeditedUpdateSettings=profile.get("expeditedUpdateSettings"),
                             assignments=profile.get("assignments", [])) for profile in quality_profiles]
    def profile_assignments(profiles):
        result = []
        for profile in profiles:
            for assignment in profile.get("assignments", []):
                target = assignment.get("target", {})
                if not isinstance(target, dict):
                    continue
                group_id = target.get("groupId") or target.get("entraObjectId")
                state = target.get("deviceAndAppManagementAssignmentFilterType")
                result.append(dict(profileId=profile["profileId"], profileName=profile["displayName"], profileVersion=profile.get("featureUpdateVersion") or profile.get("releaseDateDisplayName"),
                                   targetType=target.get("@odata.type"), groupId=group_id, filterType=state))
        return result
    feature_assignments = profile_assignments(feature_profiles)
    quality_assignments = profile_assignments(quality_profiles)
    catalog = None
    try:
        catalog = fetch_windows_release_catalog(getattr(client, "get_release_catalog", None))
        statuses.append(dict(source="microsoftWindowsReleaseCatalog", status="ok", records=sum(len(items) for items in catalog["releases"].values()), retrievedAt=catalog["retrievedAt"]))
    except WiseError:
        statuses.append(dict(source="microsoftWindowsReleaseCatalog", status="unavailable", code="ReleaseCatalogUnavailable"))
    assessments = []
    collected_at = parse_date(timestamp)
    for record in inventory:
        assessment = assess_windows_update(record, catalog, collected_at) if catalog and collected_at else dict(patchStatus="Unknown build", supportStatus="Unknown")
        assessments.append(dict(deviceId=record["deviceId"], deviceName=record.get("deviceName"), operatingSystem=record.get("operatingSystem"),
                                osVersion=record.get("osVersion"), **assessment))
    windows_assessments = [assessment for assessment in assessments if assessment.get("operatingSystem") == "Windows"]
    evaluated_patches = [assessment for assessment in windows_assessments if assessment.get("patchStatus") in ["Compliant", "Non-compliant"] and assessment.get("supportStatus") == "Supported"]
    windows_patch_summary = dict(totalWindowsDevices=len(windows_assessments), evaluated=len(evaluated_patches),
                                 compliant=sum(assessment["patchStatus"] == "Compliant" for assessment in evaluated_patches),
                                 nonCompliant=sum(assessment["patchStatus"] == "Non-compliant" for assessment in evaluated_patches),
                                 unsupported=sum(assessment.get("patchStatus") == "Unsupported OS" for assessment in windows_assessments),
                                 supportUnknown=sum(assessment.get("patchStatus") == "Support status unknown" for assessment in windows_assessments),
                                 unknown=sum(assessment.get("patchStatus") == "Unknown build" for assessment in windows_assessments),
                                 supportStatusUnavailable=sum(assessment.get("supportStatus") == "Support status unavailable" for assessment in windows_assessments),
                                 noMonthlyRelease=sum(assessment.get("patchStatus") == "No monthly B release yet" for assessment in windows_assessments),
                                 collectionDate=timestamp, catalogSource=catalog["source"] if catalog else None,
                                 catalogRetrievedAt=catalog["retrievedAt"] if catalog else None)
    identity_by_id = {record["deviceId"]: record for record in inventory}
    windows_patch_devices = []
    for assessment in windows_assessments:
        record = identity_by_id[assessment["deviceId"]]
        windows_patch_devices.append({key: assessment.get(key) for key in ["deviceId", "deviceName", "osVersion", "patchStatus", "supportStatus", "branchBuild",
                                                                           "releaseVersion", "installedBuild", "requiredBuild", "requiredKB", "requiredReleaseDate", "requiredUpdateType"]} |
                                     {"model": record.get("model"),
                                      "lastSyncDateTime": record.get("lastSyncDateTime")})
    raw_autopilot = fetch("autopilot", "deviceManagement/windowsAutopilotDeviceIdentities")
    aad_index = collections.defaultdict(list)
    for record in inventory:
        if identifier(record.get("azureADDeviceId")):
            aad_index[identifier(record["azureADDeviceId"])].append(record)
    autopilot = []
    for entry in raw_autopilot:
        result = {**entry, "autopilotId": entry.get("id"), "azureADDeviceId": entry.get("azureActiveDirectoryDeviceId"),
                  "lastContacted": iso(parse_date(entry.get("lastContactedDateTime"))), "assignedUser": entry.get("userPrincipalName")}
        matches = aad_index.get(identifier(result["azureADDeviceId"]), [])
        if len(matches) == 1:
            result["correlatedDeviceName"] = matches[0].get("deviceName")
        if entry.get("enrollmentState") == "notContacted" and entry.get("userPrincipalName"):
            result["flag"] = "Assigned but never enrolled"
        autopilot.append(result)
    ap_summary = dict(totalRegistered=len(autopilot), enrolled=sum(entry.get("enrollmentState") == "enrolled" for entry in autopilot), notContacted=sum(entry.get("enrollmentState") == "notContacted" for entry in autopilot))
    protection = {key: overview.get(key) for key in ["enrolledDeviceCount", "mdmEnrolledCount", "dualEnrolledDeviceCount"]}
    protection.update(osSummary=overview.get("deviceOperatingSystemSummary") or {}, exchangeAccessSummary=overview.get("deviceExchangeAccessStateSummary") or {})
    protection["osSummary"] = {**protection["osSummary"], "androidWorkProfileCount": protection["osSummary"].get("androidDedicatedCount")}
    protection["exchangeAccessSummary"] = {**protection["exchangeAccessSummary"], "unavailableDeviceCount": protection["exchangeAccessSummary"].get("unknownDeviceCount")}
    report = dict(metadata=dict(collectionTimestamp=timestamp, reportTitle="Complete Endpoint Intelligence Dataset", tenant=tenant_id,
                                generatedBy="wise_endpoint_intelligence.py via Microsoft Graph API", sourceSystems=["Microsoft Intune", "Microsoft Entra ID", "Intune Reports Engine", "Intune Endpoint Security", "Windows Autopilot Registry"], totalDevices=len(inventory)))
    report.update({SECTIONS["inventory"]: section("Microsoft Intune - Managed Devices", totalCount=len(inventory), devices=inventory),
                   SECTIONS["users"]: section("Intune device user metadata; no separate Entra directory query", uniqueUsers=users),
                   SECTIONS["security"]: section("Intune Endpoint Security + Malware Report", malwareReport=dict(totalDetections=len(threats), activeThreats=threats, coverage="See collectionStatus; zero detections is not proof every device was assessed"), deviceProtectionOverview=protection, endpointSecurityPolicies=policies),
                   SECTIONS["encryption"]: section("Intune Encryption Report", summary=enc_summary, windowsDeviceEncryption=encryption, mobileDeviceEncryption=mobile),
                   SECTIONS["compliance"]: section("Intune Compliance Report", tenantComplianceSummary=policy_summary or None, perDeviceCompliance=compliance),
                   SECTIONS["patch"]: section("Microsoft Windows release catalog + Intune Windows Update policies", updateRings=rings,
                                               featureUpdateProfiles=feature_profiles, qualityUpdateProfiles=quality_profiles,
                                               featureUpdateAssignments=feature_assignments, qualityUpdateAssignments=quality_assignments,
                                               windowsPatchSummary=windows_patch_summary, windowsPatchDevices=windows_patch_devices,
                                               osVersionAssessment=assessments),
                   SECTIONS["autopilot"]: section("Windows Autopilot Registry", autopilotSummary=ap_summary, autopilotDevices=autopilot)})
    findings = []
    for policy in policies:
        if "security baseline" in str(policy.get("displayName", "")).lower() and policy.get("isAssigned") is False:
            findings.append(dict(finding="Security baseline reported unassigned", affectedScope=policy.get("policyId"), riskLevel="HIGH"))
        if policy.get("ageYears", 0) >= 3 and policy.get("isAssigned") is True:
            findings.append(dict(finding="Assigned endpoint security policy last modified 3+ years ago", affectedScope=policy.get("policyId"), riskLevel="MEDIUM"))
    if enc_summary["windowsNotEncryptedCount"]:
        findings.append(dict(finding="Windows encryption report states unencrypted devices; policy enforcement not established", affectedScope=str(enc_summary["windowsNotEncryptedCount"]) + " devices", riskLevel="HIGH"))
    for record in inventory:
        if record.get("orphanDevice"):
            findings.append(dict(finding="Device has no assigned user metadata", affectedScope=record["deviceId"], riskLevel="MEDIUM"))
    if ap_summary["notContacted"]:
        findings.append(dict(finding="Autopilot registrations report notContacted", affectedScope=str(ap_summary["notContacted"]) + " devices", riskLevel="LOW"))
    stale = [dict(deviceName=record.get("deviceName"), deviceId=record["deviceId"], userPrincipalName=record.get("userPrincipalName"), lastSync=record.get("lastSyncDateTime"), daysInactive=record["daysInactive"], severity="CRITICAL" if record["daysInactive"] > 180 else "HIGH" if record["daysInactive"] > 90 else "MEDIUM") for record in inventory if record.get("staleFlag") is True]
    report[SECTIONS["incidents"]] = section("Intune Device Actions + Management State", pendingDeviceActions=[dict(deviceName=record.get("deviceName"), deviceId=record["deviceId"], managementState="retirePending", note="Retire action initiated but not completed") for record in inventory if record.get("managementState") == "retirePending"], staleDevices=sorted(stale, key=lambda entry: -entry["daysInactive"]), policyGapFindings=findings)
    report["endpointRiskHealthSummary"] = section("Correlated sources; heuristic, not authoritative", sortOrder="Heuristic risk then device name", devices=derive_risk(inventory, encryption), methodology="Legacy-style heuristic; unknown/configManager compliance is not noncompliant. LOW is not proof of health.")
    report["crossTenantSummary"] = cross_summary(report)
    raw_data = dict(managedDevices=raw_devices, encryption=raw_encryption, complianceSummary=policy_summary, protectionOverview=overview,
                    securityIntents=intents, deviceConfigurations=configurations, featureUpdateProfiles=feature_profiles,
                    qualityUpdateProfiles=quality_profiles, featureUpdateAssignments=feature_assignments,
                    qualityUpdateAssignments=quality_assignments, autopilot=raw_autopilot, malware=raw_malware)
    if extra_details:
        # Collect in priority order across all devices: policy results and hardware first,
        # then configuration states, which Graph throttles hardest. A configuration 429
        # storm must not starve the policy results the dashboard shows per device.
        bases = {record["deviceId"]: "deviceManagement/managedDevices/" + quote(record["deviceId"], safe="") for record in inventory}
        compliance = {key: fetch("complianceDetails:" + key, base + "/deviceCompliancePolicyStates", unsupported=True) for key, base in bases.items()}
        hardware = {key: fetch("hardware:" + key, base + "?$select=id,hardwareInformation", False, True) for key, base in bases.items()}
        configuration = {key: fetch("configurationDetails:" + key, base + "/deviceConfigurationStates", unsupported=True) for key, base in bases.items()}
        # Recovery pass: throttling is temporary. Once the other passes have given Graph time to
        # recover, retry the policy results that were throttled; a repeat throttle stops it again.
        pending = {"complianceDetails:" + key for key in bases if any(entry["source"] == "complianceDetails:" + key and entry["status"] == "unavailable" and entry.get("httpStatus") in [429, 500, 502, 503, 504] for entry in statuses)}
        if pending:
            logging.getLogger("wise.graph").info("Graph complianceDetails recovery pass for %s throttled devices", len(pending))
            throttled_categories.discard("complianceDetails")
            statuses[:] = [entry for entry in statuses if entry["source"] not in pending]
            for key, base in bases.items():
                if "complianceDetails:" + key in pending:
                    compliance[key] = fetch("complianceDetails:" + key, base + "/deviceCompliancePolicyStates", unsupported=True)
        raw_data["extraDetails"] = [dict(deviceId=key, compliancePolicyStates=compliance[key], deviceConfigurationStates=configuration[key], hardwareInformation=hardware[key]) for key in bases]
    report["rawData"] = raw_data
    report["collectionStatus"] = dict(status="error" if any(entry["status"] == "error" for entry in statuses) else "partial" if any(entry["status"] == "unavailable" for entry in statuses if entry["source"] != "microsoftWindowsReleaseCatalog") else "complete", sources=statuses,
                                     caveats=["Unsupported optional endpoints are not clean results.", "Windows patch compliance is a Microsoft monthly build-baseline comparison, not confirmation of an Intune installation attempt.", "Feature update target/applicability is not inferred from policy assignments.", "Storage uses exact bytes / 2**30, not rounded PowerShell GB."])
    report["metadata"]["executionCompletedAtUtc"] = iso(dt.datetime.now(dt.timezone.utc))
    return sanitize(report)


def windows_build(record: dict) -> int | None:
    if str(record.get("operatingSystem", "")).lower() != "windows":
        return None
    parts = str(record.get("osVersion", "")).split(".")
    return int(parts[2]) if len(parts) >= 3 and parts[2].isdigit() and int(parts[2]) > 0 else None


def patch_status(record: dict) -> str:
    """Legacy reports have no verified release catalog; never guess a status."""
    return "Unknown" if str(record.get("operatingSystem", "")).casefold() == "windows" else "Not applicable"


def derive_risk(inventory: list, encryption: list) -> list:
    index = unique_index(encryption, "deviceId")
    result = []
    for record in inventory:
        enc = index.get(identifier(record["deviceId"]), {})
        noncompliant = str(record.get("complianceState", "")).lower() == "noncompliant"
        unencrypted = enc.get("encryptionStatus") == "Not encrypted" or record.get("isEncrypted") is False
        indicators = []
        for flag, label in [(record.get("staleFlag") is True, f"Stale {record.get('daysInactive')}d"), (record.get("criticalFlag_Windows10EOL"), "Windows 10 legacy EOL flag"),
                            (noncompliant, "Non-Compliant"), (unencrypted, "Unencrypted"), ("mismatch" in str(enc.get("bitLockerState", "")).lower(), "BitLocker mismatch"),
                            (record.get("managementCertificateExpired"), "Mgmt cert EXPIRED"), (record.get("lowDiskSpaceFlag"), "Low device-wide free storage"),
                            (record.get("orphanDevice"), "No assigned user metadata"), (record.get("managementState") == "retirePending", "retirePending")]:
            if flag:
                indicators.append(label)
        level = "CRITICAL" if record.get("criticalFlag_Windows10EOL") or (record.get("managementCertificateExpired") and noncompliant) else "HIGH" if noncompliant and record.get("staleFlag") else "MEDIUM" if noncompliant or unencrypted or record.get("orphanDevice") else "LOW"
        result.append(dict(riskLevel=level, deviceName=record.get("deviceName"), deviceId=record["deviceId"], primaryUser=record.get("userDisplayName") or "UNASSIGNED", userPrincipalName=record.get("userPrincipalName"),
                           complianceStatus="Compliant" if str(record.get("complianceState", "")).lower() == "compliant" else "Non-Compliant" if noncompliant else record.get("complianceState") or "Unknown",
                           encryptionStatus="Not Encrypted" if unencrypted else "Encrypted" if record.get("isEncrypted") is True or enc.get("encryptionStatus") == "Encrypted" else "Unknown", openIncidents=int(record.get("managementState") == "retirePending"), riskIndicators=indicators))
    result.sort(key=lambda entry: (["CRITICAL", "HIGH", "MEDIUM", "LOW"].index(entry["riskLevel"]), str(entry.get("deviceName", "")).casefold()))
    return [dict(priority=number, **entry) for number, entry in enumerate(result, 1)]


def cross_summary(report: dict) -> dict:
    inventory = rows(report, "inventory", "devices")
    count = lambda predicate: sum(bool(predicate(record)) for record in inventory)
    percent = lambda numerator, denominator: round(numerator / denominator * 100, 1) if denominator else None
    windows = count(lambda record: record.get("operatingSystem") == "Windows")
    cloud = count(lambda record: "cloud pc" in str(record.get("model", "")).lower())
    virtual = count(lambda record: "virtual machine" in str(record.get("model", "")).lower())
    compliant = count(lambda record: str(record.get("complianceState", "")).lower() == "compliant")
    noncompliant = count(lambda record: str(record.get("complianceState", "")).lower() == "noncompliant")
    encryption = report[SECTIONS["encryption"]]["summary"]
    policies = rows(report, "security", "endpointSecurityPolicies")
    years = [parsed.year for policy in policies if policy.get("isAssigned") is True and (parsed := parse_date(policy.get("lastModifiedDateTime")))]
    known = [record for record in inventory if record.get("daysInactive") is not None]
    longest = max(known, key=lambda record: record["daysInactive"], default={})
    return dict(collectionTimestamp=report["metadata"]["collectionTimestamp"],
                fleet=dict(totalManagedDevices=len(inventory), windowsDevices=windows, iosDevices=count(lambda record: record.get("operatingSystem") == "iOS"), androidDevices=count(lambda record: record.get("operatingSystem") == "Android"), cloudPCs=cloud, physicalWindows=max(0, windows-cloud-virtual), virtualMachines=virtual),
                compliance=dict(compliantCount=compliant, compliantPercent=percent(compliant, len(inventory)), nonCompliantCount=noncompliant, nonCompliantPercent=percent(noncompliant, len(inventory)), otherOrUnknownCount=len(inventory)-compliant-noncompliant),
                encryption={**encryption, "windowsEncryptedPercent": percent(encryption["windowsEncryptedCount"], windows), "windowsNotEncryptedPercent": percent(encryption["windowsNotEncryptedCount"], windows)},
                staleness=dict(devicesInactiveOver30Days=count(lambda record: record.get("staleFlag") is True), devicesInactiveOver90Days=count(lambda record: (record.get("daysInactive") or 0) > 90), devicesInactiveOver180Days=count(lambda record: (record.get("daysInactive") or 0) > 180), longestInactiveDays=longest.get("daysInactive"), longestInactiveDeviceToken=longest.get("deviceName")),
                security=dict(activeMalwareDetections=report[SECTIONS["security"]]["malwareReport"]["totalDetections"], unassignedSecurityBaselines=sum(policy.get("isAssigned") is False for policy in policies), oldestAssignedSecurityPolicyYear=min(years) if years else None),
                autopilot=dict(registeredDevices=report[SECTIONS["autopilot"]]["autopilotSummary"]["totalRegistered"], neverEnrolled=report[SECTIONS["autopilot"]]["autopilotSummary"]["notContacted"]),
                criticalFlags=dict(windows10EOLDevices=count(lambda record: record.get("criticalFlag_Windows10EOL")), expiredManagementCertificates=count(lambda record: record.get("managementCertificateExpired")), expiredGracePeriods=sum(record.get("gracePeriodExpired") is True for record in rows(report, "compliance", "perDeviceCompliance")), unassignedCloudPCs=count(lambda record: record.get("orphanDevice")), retirePendingDevices=count(lambda record: record.get("managementState") == "retirePending")))


def anonymize_report(report: dict) -> dict:
    """Pseudonymize known PII and embedded references; no reversal map is saved.

    This is not a guarantee of anonymity for arbitrary vendor free text. Raw
    Graph opaque IDs and tenant provenance are retained for validation; do not
    publish rawData. The complete source input file is never modified.
    """
    classes = dict(deviceId="DEVID", managedDeviceId="DEVID", deviceName="DEVNAME", correlatedDeviceName="DEVNAME", associatedDevices="DEVNAME", nonCompliantDevices="DEVNAME", device="DEVNAME", azureADDeviceId="AADID", azureActiveDirectoryDeviceId="AADID", serialNumber="SN", userPrincipalName="UPN", emailAddress="UPN", assignedUser="UPN", userDisplayName="USRNAME", primaryUser="USRNAME", userId="USRID", wiFiMacAddress="MAC", imei="IMEI", meid="MEID", easDeviceId="EASID", autopilotId="APID")
    mapping, replacements, counts = {}, {}, collections.Counter()
    def structural(value, name=""):
        if isinstance(value, dict):
            return {key: structural(child, key) for key, child in value.items()}
        if isinstance(value, list):
            return [structural(child, name) for child in value]
        if name == "subscriberCarrier" and value:
            return "REDACTED"
        if name in classes and isinstance(value, str) and value and value != "UNASSIGNED":
            category = classes[name]
            key = (category, value.casefold())
            if key not in mapping:
                counts[category] += 1
                mapping[key] = f"{category}_{counts[category]:03d}"
            replacements[value] = mapping[key]
            return mapping[key]
        return value
    output = structural(sanitize(report))
    pattern = re.compile("|".join(re.escape(value) for value in sorted(replacements, key=len, reverse=True))) if replacements else None
    def embedded(value):
        if isinstance(value, dict):
            return {key: embedded(child) for key, child in value.items()}
        if isinstance(value, list):
            return [embedded(child) for child in value]
        return pattern.sub(lambda match: replacements[match[0]], value) if pattern and isinstance(value, str) else value
    return embedded(output)


def summary_fields(report: dict, snapshot: dict) -> dict:
    """All 24 existing statistic fields plus provenance, never mixed policy cohorts."""
    metrics = snapshot["summary"]["metrics"]
    inventory = rows(report, "inventory", "devices")
    states = collections.Counter(str(record.get("complianceState", "")).lower() for record in inventory)
    patches = collections.Counter(record.get("patchStatus") for record in rows(report, "patch", "osVersionAssessment"))
    fields = dict(Title=ROW_TITLE, CollectionTimestamp=report["metadata"]["collectionTimestamp"], ReportTitle=report["metadata"].get("reportTitle", ""), Tenant=report["metadata"]["tenant"],
                  GeneratedBy=report["metadata"].get("generatedBy", ""), SourceSha256=snapshot["coverage"]["sourceSha256"], SourceSystems="; ".join(report["metadata"].get("sourceSystems", [])),
                  SourceSummaryJson=json_bytes(dict(compliance=snapshot["policySummary"], autopilot=report[SECTIONS["autopilot"]].get("autopilotSummary"))).decode())
    names = dict(TotalDevices="devices", TotalUsers="users", CompliantDevices="compliant", NonCompliantDevices="noncompliant", ConfigManagerDevices="configManager", EncryptedDevices="encrypted", UnencryptedDevices="unencrypted", StaleDevices="stale", NonStaleDevices="notStale", StorageMeasuredDevices="storageMeasured", CriticalStorageDevices="storageBelow2", LowStorageDevices="storageBelow10", AutopilotRegistered="autopilot")
    fields.update({name: metrics[key] for name, key in names.items()})
    fields.update(InGracePeriod=states["ingraceperiod"], UnknownDevices=states["unknown"], ThreatDevices=sum(metrics[f"threat.{key}"] for key in ["low", "medium", "high"]), UnknownThreatDevices=metrics["threat.unknown"] + metrics["threat.deactivated"],
                  OrphanDevices=metrics["user.missing"], AutopilotEnrolled=snapshot["summary"]["autopilotEnrolled"], AutopilotNotContacted=sum(record.get("enrollmentState") == "notContacted" for record in rows(report, "autopilot", "autopilotDevices")),
                  PatchCurrentOrRecent=patches["Current"] + patches["Recent"], PatchSlightlyBehind=patches["Slightly behind"], PatchVeryOutdated=patches["Very outdated"], PatchCritical=patches["CRITICAL"])
    return fields


def validate_site_url(site_url: str) -> str:
    parsed = urlsplit(site_url)
    if parsed.scheme != "https" or not re.fullmatch(r"[a-z0-9-]+\.sharepoint\.com", parsed.netloc, re.I) or not re.fullmatch(r"/(sites|teams)/[^/]+/?", parsed.path) or parsed.query or parsed.fragment or "%" in parsed.path:
        raise WiseError("Site must be an HTTPS tenant.sharepoint.com/sites/name or /teams/name URL.")
    return site_url.rstrip("/")


def publish_dashboard(client: GraphClient, snapshot: dict, site_url: str, tenant_id: str, *, report: dict | None = None, update_summary: bool = True) -> dict:
    """Validate destination, stage/read/hash, conditional final PUT, read/hash, summary.

    Client token provider must validate authenticated tenant (AuthSession does).
    Nothing is provisioned. HTTP 412 or uncertain writes abort without rollback.
    """
    tenant_id = canonical_uuid(tenant_id)
    site_url = validate_site_url(site_url)
    if canonical_uuid(snapshot.get("metadata", {}).get("tenant")) != tenant_id:
        raise WiseError("Source and authenticated tenant differ; cross-tenant publishing is blocked.")
    if report is not None and canonical_uuid(report.get("metadata", {}).get("tenant")) != tenant_id:
        raise WiseError("Full report and authenticated tenant differ; summary publication is blocked.")
    if snapshot.get("schemaVersion") != 2 or snapshot.get("summary", {}).get("exceptionDevices") != len(snapshot.get("devices", [])) or any(not row.get("issues") for row in snapshot.get("devices", [])) or "users" in snapshot or "sourceSections" in snapshot:
        raise WiseError("Invalid exception-only snapshot.")
    if snapshot.get("coverage", {}).get("collectionStatus", {}).get("status") == "error":
        raise WiseError("Collection errors block publication. Inspect the local report.")
    parsed = urlsplit(site_url)
    root = client.get("v1.0/sites/root?$select=id,siteCollection")
    if str(root.get("siteCollection", {}).get("hostname", "")).lower() != parsed.hostname:
        raise WiseError("Destination hostname differs from authenticated tenant root; multi-geo requires separate review.")
    site = client.get(f"v1.0/sites/{parsed.hostname}:{quote(parsed.path, safe='/')}?$select=id,webUrl")
    if str(site.get("webUrl", "")).rstrip("/").casefold() != site_url.casefold() or not site.get("id"):
        raise WiseError("Graph resolved a different canonical site URL; use the actual destination.")
    base = "v1.0/sites/" + quote(site["id"], safe=",")
    lists = client.get_paged(base + "/lists?$select=id,displayName,description,webUrl,list,system")
    libraries = [entry for entry in lists if entry.get("list", {}).get("template") == "documentLibrary" and unquote(urlsplit(entry.get("webUrl", "")).path).rstrip("/").casefold() == (parsed.path + "/SiteAssets").casefold()]
    if len(libraries) != 1:
        raise WiseError("SiteAssets library not uniquely discoverable, including hidden/system lists. Provisioning is not performed.")
    drive = client.get(base + "/lists/" + quote(libraries[0]["id"], safe="") + "/drive")
    drive_base = "v1.0/drives/" + quote(drive["id"], safe="")
    target = client.get(drive_base + "/root:/WISEEndpointIntelligence/dashboard.json")
    if not target.get("id") or not target.get("eTag") or "file" not in target:
        raise WiseError("Existing dashboard file metadata or eTag is missing.")
    target_path = drive_base + "/items/" + quote(target["id"], safe="")
    try:
        current = json.loads(client.download(target_path + "/content"))
    except (ValueError, UnicodeError):
        raise WiseError("Existing dashboard is not valid JSON.") from None
    if not isinstance(current, dict) or current.get("schemaVersion") != 2 or canonical_uuid(current.get("metadata", {}).get("tenant")) != tenant_id:
        raise WiseError("Existing dashboard schema/tenant ownership check failed.")
    summary_target, fields = None, None
    if update_summary:
        if report is None:
            raise WiseError("Summary update requires the full local report.")
        candidates = [entry for entry in lists if entry.get("displayName") == SUMMARY_TITLE]
        if len(candidates) != 1 or candidates[0].get("description") != SUMMARY_MARKER:
            raise WiseError("Existing summary list ownership marker not verified.")
        summary_base = base + "/lists/" + quote(candidates[0]["id"], safe="")
        fields = summary_fields(report, snapshot)
        columns = client.get_paged(summary_base + "/columns")
        column_names = {entry.get("name") for entry in columns if not entry.get("readOnly")}
        if set(fields) - column_names:
            raise WiseError("Summary schema is missing writable required fields; no fields were provisioned.")
        items = client.get_paged(summary_base + "/items?$expand=fields&$top=2")
        if len(items) != 1 or items[0].get("fields", {}).get("Title") != ROW_TITLE or canonical_uuid(items[0].get("fields", {}).get("Tenant")) != tenant_id or not items[0].get("eTag"):
            raise WiseError("Expected one owned summary row for the authenticated tenant, with eTag.")
        summary_target = (summary_base + "/items/" + quote(items[0]["id"], safe=""), items[0]["eTag"])
    payload = json_bytes(sanitize(snapshot))
    if len(payload) > MAX_BYTES:
        raise WiseError("Snapshot exceeds 50 MiB single-upload limit.")
    expected = hashlib.sha256(payload).digest()
    stage_path = drive_base + "/root:/WISEEndpointIntelligence/dashboard." + uuid.uuid4().hex + ".staging.json:/content"
    staged_id = None
    committed = False
    try:
        staged = client.request("PUT", stage_path, data=payload, headers={"Content-Type": "application/json"}).json()
        staged_id = staged.get("id")
        if not staged_id:
            raise WiseError("Staging upload returned no file ID; inspect folder for orphan staging files.")
        if hashlib.sha256(client.download(drive_base + "/items/" + quote(staged_id, safe="") + "/content")).digest() != expected:
            raise WiseError("Staging SHA-256 verification failed; dashboard was not changed.")
        client.request("PUT", target_path + "/content", data=payload, headers={"Content-Type": "application/json", "If-Match": target["eTag"]})
        committed = True
        if hashlib.sha256(client.download(target_path + "/content")).digest() != expected:
            raise WiseError("Final dashboard readback SHA-256 mismatch.")
        if summary_target:
            item_path, etag = summary_target
            client.request("PATCH", item_path + "/fields", json_body=fields, headers={"If-Match": etag})
            saved = client.get(item_path + "?$expand=fields").get("fields", {})
            if any(saved.get(key) != value for key, value in fields.items()):
                raise WiseError("Summary readback did not match all submitted fields.")
    except Exception as error:
        if committed:
            raise WiseError("Partial publication: dashboard PUT completed, but final verification or summary update failed. Inspect current file and version history; no rollback attempted.") from None
        if isinstance(error, WiseError):
            raise
        raise WiseError("Publication failed before a confirmed commit; write outcome may be uncertain. Inspect staging and dashboard version history.") from None
    finally:
        if staged_id:
            try:
                client.request("DELETE", drive_base + "/items/" + quote(staged_id, safe=""))
            except Exception:
                print("Warning: staging cleanup failed; inspect the dashboard folder.", file=sys.stderr)
    return dict(dashboardVerified=True, summaryVerified=bool(summary_target), sha256=expected.hex(), staticStatisticsPageUpdated=False)


def write_outputs(report: dict, snapshot: dict, output_dir: Path, source_path: Path | None = None) -> tuple[Path, Path]:
    """Prepare both files, then atomically replace each; source files are protected."""
    output_dir = Path(output_dir)
    destinations = [output_dir / "EndpointHealthReport.json", output_dir / "dashboard.json"]
    if source_path and any(destination.resolve() == Path(source_path).resolve() or (destination.exists() and os.path.samefile(destination, source_path)) for destination in destinations):
        raise WiseError("Output would overwrite the original source report.")
    payloads = [json_bytes(sanitize(report)), json_bytes(sanitize(snapshot))]
    output_dir.mkdir(parents=True, exist_ok=True)
    temporary = []
    try:
        for payload in payloads:
            descriptor, name = tempfile.mkstemp(prefix=".wise-", suffix=".tmp", dir=output_dir)
            temporary.append(Path(name))
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
        for temporary_path, destination in zip(temporary, destinations):
            os.replace(temporary_path, destination)
        for destination, expected in zip(destinations, payloads):
            try:
                actual = destination.read_bytes()
                json.loads(actual)
            except (OSError, ValueError, UnicodeError):
                raise WiseError(f"Local output readback failed: {destination}") from None
            if actual != expected:
                raise WiseError(f"Local output readback mismatch: {destination}")
    finally:
        for temporary_path in temporary:
            temporary_path.unlink(missing_ok=True)
    return tuple(destinations)


def archive_reports(report: dict, snapshot: dict, account_url: str, container: str, prefix: str = "reports", *, credential=None) -> str:
    parsed = urlsplit(account_url)
    if parsed.scheme != "https" or not re.fullmatch(r"[a-z0-9]{3,24}\.blob\.core\.windows\.net", parsed.netloc) or parsed.path not in ["", "/"] or parsed.query or parsed.fragment:
        raise WiseError("Archive account URL must be https://<account>.blob.core.windows.net without credentials or query parameters.")
    if not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{1,61})[a-z0-9]", container) or "--" in container:
        raise WiseError("Invalid Blob archive container name.")
    if not re.fullmatch(r"[a-zA-Z0-9/_-]+", prefix) or any(part in ["", ".", ".."] for part in prefix.split("/")):
        raise WiseError("Archive prefix must contain nonempty path segments using letters, numbers, underscores or hyphens.")
    storage = importlib.import_module("azure.storage.blob")
    connection_string = os.environ.get("WISE_ARCHIVE_CONNECTION_STRING", "").strip()
    owned_credential = credential is None and not connection_string
    now = dt.datetime.now(dt.timezone.utc)
    run_path = f"{prefix}/{canonical_uuid(report['metadata']['tenant'])}/{now:%Y/%m/%d}/{now:%H%M%S}-{uuid.uuid4().hex}"
    try:
        if connection_string:
            service_client = storage.BlobServiceClient.from_connection_string(connection_string, retry_total=3, connection_timeout=30, read_timeout=60)
        else:
            if owned_credential:
                identity = importlib.import_module("azure.identity")
                managed_client_id = os.environ.get("WISE_MANAGED_IDENTITY_CLIENT_ID")
                credential = identity.DefaultAzureCredential(exclude_interactive_browser_credential=True, managed_identity_client_id=managed_client_id)
            service_client = storage.BlobServiceClient(account_url=account_url, credential=credential, retry_total=3, connection_timeout=30, read_timeout=60)
        with service_client as service:
            if connection_string and service.url.rstrip("/") != account_url.rstrip("/"):
                raise WiseError("Archive connection string endpoint differs from the configured archive account URL; publication blocked.")
            for filename, document in [("EndpointHealthReport.json", report), ("dashboard.json", snapshot)]:
                payload = json_bytes(sanitize(document))
                digest = hashlib.sha256(payload).hexdigest()
                with service.get_blob_client(container=container, blob=f"{run_path}/{filename}") as blob:
                    blob.upload_blob(payload, overwrite=False, content_settings=storage.ContentSettings(content_type="application/json"), metadata={"sha256": digest}, timeout=60)
                    verified_digest = hashlib.sha256()
                    for chunk in blob.download_blob(timeout=60).chunks():
                        verified_digest.update(chunk)
                    if verified_digest.hexdigest() != digest:
                        raise WiseError("Blob archive readback SHA-256 mismatch; SharePoint publication blocked.")
                print(f"  Blob archive verified: {run_path}/{filename} ({len(payload):,} bytes).", flush=True)
    except WiseError:
        raise
    except Exception:
        raise WiseError("Blob archive upload or verification failed; SharePoint publication blocked. A partial archive may remain; inspect Azure access, networking and storage logs.") from None
    finally:
        if owned_credential and credential is not None:
            credential.close()
    return run_path


def run_hosted_pipeline() -> dict:
    """Azure timer pipeline: in-memory collection, verified archive, publication."""
    logger = logging.getLogger("wise.pipeline")
    auth_mode = os.environ.get("WISE_GRAPH_AUTH", "managed-identity").strip()
    if auth_mode not in ["managed-identity", "client-credentials"]:
        raise WiseError("WISE_GRAPH_AUTH must be managed-identity or client-credentials.")
    client_id = DEFAULT_CLIENT
    if auth_mode == "client-credentials":
        if not os.environ.get("WISE_TENANT_ID") or not os.environ.get("WISE_CLIENT_ID"):
            raise WiseError("Client credentials require explicit WISE_TENANT_ID and WISE_CLIENT_ID app settings.")
        if not os.environ.get("WISE_ARCHIVE_CONNECTION_STRING", "").strip():
            raise WiseError("Client-credentials hosted mode requires WISE_ARCHIVE_CONNECTION_STRING for separate Blob authentication.")
        client_id = canonical_uuid(os.environ["WISE_CLIENT_ID"])
    tenant = canonical_uuid(os.environ.get("WISE_TENANT_ID", DEFAULT_TENANT))
    site = validate_site_url(os.environ.get("WISE_SITE_URL", DEFAULT_SITE))
    account_url = os.environ.get("WISE_ARCHIVE_ACCOUNT_URL", "").strip()
    if not account_url:
        raise WiseError("Missing required Azure app setting WISE_ARCHIVE_ACCOUNT_URL; no collection or publication started.")
    container = os.environ.get("WISE_ARCHIVE_CONTAINER", "wise-reports")
    prefix = os.environ.get("WISE_ARCHIVE_PREFIX", "reports")
    run_id = uuid.uuid4().hex
    stage = "authentication"
    auth = AuthSession(tenant, client_id, [], auth=auth_mode)
    archive_path = None
    try:
        logger.info("WISE run %s: verifying organization using %s", run_id, auth_mode)
        client = GraphClient(auth.token)
        organizations = client.get_paged("v1.0/organization?$select=id")
        if len(organizations) != 1 or canonical_uuid(organizations[0].get("id")) != tenant:
            raise WiseError("Authenticated organization differs from configured tenant.")
        stage = "collection"
        logger.info("WISE run %s: collecting source data in memory", run_id)
        report = collect_report(client, tenant, extra_details=True)
        stage = "validation"
        collection_status = report.get("collectionStatus", {})
        failed_sources = [source for source in collection_status.get("sources", []) if source.get("status") == "error"]
        for source in failed_sources:
            category = str(source.get("source", "unknown")).split(":", 1)[0]
            safe_category = category if re.fullmatch(r"[A-Za-z.]+", category) else "unknown"
            logger.error("WISE source failure: category=%s; HTTP=%s", safe_category, source.get("httpStatus") if type(source.get("httpStatus")) is int else "unavailable")
        logger.info("WISE collection result: status=%s; failedSources=%s", collection_status.get("status", "unavailable"), len(failed_sources))
        validate_report(report)
        if report.get("collectionStatus", {}).get("status") not in ["complete", "partial"]:
            raise WiseError("Collection status does not permit hosted publication.")
        report = sanitize(report)
        snapshot = build_operational(report)
        try:
            from mistral_intelligence import generate_intelligence
            intelligence, ai_failure = generate_intelligence(snapshot)
            if intelligence:
                snapshot["aiIntelligence"] = intelligence
                logger.info("WISE optional AI narrative available")
            else:
                snapshot["aiIntelligence"] = {"status": "unavailable", "reason": ai_failure or "model_error"}
                logger.warning("WISE optional AI narrative unavailable; reason=%s", ai_failure or "model_error")
        except Exception as error:
            snapshot["aiIntelligence"] = {"status": "unavailable", "reason": "integration_error"}
            logger.warning("WISE optional AI narrative unavailable; reason=integration_error; errorType=%s", type(error).__name__)
        stage = "blob_archive"
        logger.info("WISE run %s: archiving JSON and verifying Blob readback", run_id)
        archive_path = archive_reports(report, snapshot, account_url, container, prefix, credential=auth.credential if auth_mode == "managed-identity" else None)
        logger.info("WISE run %s: verified archive %s", run_id, archive_path)
        stage = "sharepoint_publication"
        logger.info("WISE run %s: publishing SharePoint dashboard and summary", run_id)
        result = publish_dashboard(client, snapshot, site, tenant, report=report, update_summary=True)
        if not result.get("dashboardVerified") or not result.get("summaryVerified"):
            raise WiseError("SharePoint publication did not verify both dashboard and summary.")
        logger.info("WISE run %s: completed; devices=%s; archive=%s", run_id, snapshot["summary"]["totalDevices"], archive_path)
        return dict(runId=run_id, archivePath=archive_path, devices=snapshot["summary"]["totalDevices"], dashboardVerified=True, summaryVerified=True)
    except Exception as error:
        logger.error("WISE run %s failed at %s; archive=%s; errorType=%s", run_id, stage, archive_path or "not verified", type(error).__name__)
        raise WiseError(f"Hosted WISE run {run_id} failed at {stage}; archive={archive_path or 'not verified'}. Inspect restricted Azure logs and destination state before retrying.") from None
    finally:
        auth.credential.close()


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--input-report", type=Path, help="Convert offline; never authenticate unless --publish is explicit")
    parser.add_argument("--output-dir", type=Path, default=Path(__file__).resolve().parent / "output" / "wise")
    parser.add_argument("--tenant-id", default=os.environ.get("WISE_TENANT_ID", DEFAULT_TENANT))
    parser.add_argument("--client-id", default=os.environ.get("WISE_CLIENT_ID", DEFAULT_CLIENT))
    parser.add_argument("--site-url", default=os.environ.get("WISE_SITE_URL", DEFAULT_SITE))
    parser.add_argument("--username", help="Optional delegated login hint; no default account guessing")
    parser.add_argument("--auth", choices=["browser", "broker", "managed-identity"], default="browser")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--collect-only", action="store_true", help="Read scopes only; never publish")
    mode.add_argument("--publish", action="store_true", help="Explicitly publish an imported report")
    parser.add_argument("--extra-details", action="store_true", help="Collect per-device policy/configuration states and available hardwareInformation")
    parser.add_argument("--anonymize", action="store_true", help="Pseudonymize known local/export PII; tenant provenance retained")
    parser.add_argument("--no-install", action="store_true", help="Require ready private venv; do not install or re-exec")
    parser.add_argument("--setup-only", action="store_true", help="Install/check the private Python environment, without authentication, collection or publication")
    approval = parser.add_mutually_exclusive_group()
    approval.add_argument("--yes", action="store_true", help="Explicit automatic-publication approval (also the default)")
    approval.add_argument("--confirm", action="store_true", help="Ask for approval before SharePoint writes instead of publishing automatically")
    parser.add_argument("--skip-summary", action="store_true", help="Update dashboard only, not existing summary row")
    parser.add_argument("--archive-account-url", default=os.environ.get("WISE_ARCHIVE_ACCOUNT_URL", ""), help="Blob archive destination verified before SharePoint publication")
    parser.add_argument("--archive-container", default=os.environ.get("WISE_ARCHIVE_CONTAINER", "wise-reports"))
    parser.add_argument("--archive-prefix", default=os.environ.get("WISE_ARCHIVE_PREFIX", "reports"))
    local_output = parser.add_mutually_exclusive_group()
    local_output.add_argument("--save-local", dest="save_local", action="store_true", default=None, help="Also save report JSON files on the executing machine")
    local_output.add_argument("--no-local-output", dest="save_local", action="store_false", help="Keep generated reports in memory; requires Blob-enabled publication")
    return parser


def log_collection_coverage(report: dict) -> None:
    coverage = report.get("collectionStatus", {})
    sources = coverage.get("sources", [])
    counts = collections.Counter(str(source.get("status", "unknown")) for source in sources if isinstance(source, dict))
    print(f"  Graph source coverage: {counts['ok']} ok, {counts['unavailable']} unavailable, {counts['error']} errors ({len(sources)} sources).")
    for source in sources:
        if isinstance(source, dict) and source.get("status") in ["unavailable", "error"]:
            detail = f" HTTP {source['httpStatus']}" if source.get("httpStatus") else ""
            print(f"  Source {source.get('source', 'unknown')}: {source['status']}{detail}.")


def main(argv: list[str] | None = None) -> int:
    """CLI entry; returns 0 success, 1 failure, 2 declined publication."""
    if sys.version_info < (3, 11):
        print("Python 3.11 or newer is required.", file=sys.stderr)
        return 1
    args = make_parser().parse_args(argv)
    publish = args.publish or (args.input_report is None and not args.collect_only)
    save_local = args.save_local if args.save_local is not None else not (publish and args.archive_account_url)
    auth = None
    try:
        if args.setup_only:
            bootstrap_dependencies(args.auth, args.no_install)
            print("Private Python environment is ready. No sign-in or SharePoint changes performed.")
            return 0
        tenant = canonical_uuid(args.tenant_id)
        client_id = canonical_uuid(args.client_id)
        site = validate_site_url(args.site_url)
        if not args.setup_only and not save_local and not (publish and args.archive_account_url):
            raise WiseError("Disabling local outputs requires Blob-enabled publication; otherwise no report would be retained.")
        if args.auth == "managed-identity" and publish and not args.setup_only and not args.archive_account_url:
            raise WiseError("Scheduled managed-identity publication requires WISE_ARCHIVE_ACCOUNT_URL or --archive-account-url so Blob history cannot be silently skipped.")
        client = None
        if args.input_report:
            print(f"[1/5] Loading imported report: {args.input_report}", flush=True)
            with args.input_report.open("r", encoding="utf-8-sig") as stream:
                report = json.load(stream, parse_constant=lambda value: (_ for _ in ()).throw(WiseError("Nonfinite JSON numbers are invalid.")))
        else:
            print(f"[1/5] Preparing Python dependencies and {args.auth} authentication...", flush=True)
            bootstrap_dependencies(args.auth, args.no_install)
            print("[2/5] Collecting current Intune, Autopilot, policy, and Microsoft release-catalog data...", flush=True)
            auth = AuthSession(tenant, client_id, READ_SCOPES + (["Sites.ReadWrite.All"] if publish else []), args.username, args.auth)
            client = GraphClient(auth.token)
            if args.auth == "managed-identity":
                organizations = client.get_paged("v1.0/organization?$select=id")
                if len(organizations) != 1 or canonical_uuid(organizations[0].get("id")) != tenant:
                    raise WiseError("Managed identity organization does not match configured tenant; collection blocked.")
            report = collect_report(client, tenant, args.extra_details)
            print("  Collection requests finished.", flush=True)
        if args.input_report:
            print("[2/5] Using imported report; no Graph collection was run.", flush=True)
        print("[3/5] Validating report and building dashboard snapshot...", flush=True)
        validate_report(report)
        report = anonymize_report(report) if args.anonymize else sanitize(report)
        snapshot = build_operational(report)
        if save_local:
            print("[4/5] Writing local JSON outputs and verifying file readback...", flush=True)
            output_paths = write_outputs(report, snapshot, args.output_dir, args.input_report)
            for path in output_paths:
                print(f"  Verified {path} ({path.stat().st_size:,} bytes).")
        else:
            print("[4/5] Keeping generated JSON in memory; no local report files written. Blob archive is next.", flush=True)
        print(f"  Snapshot totals: {snapshot['summary']['totalDevices']} devices; {snapshot['summary']['exceptionDevices']} exception records; {snapshot['summary']['autopilotExceptions']} Autopilot exceptions.")
        status = report.get("collectionStatus", {}).get("status", "imported-unverified")
        print(f"  Collection status: {status}. Reports contain sensitive endpoint data; restrict access.")
        log_collection_coverage(report)
        if status == "error":
            raise WiseError("Core/forbidden collection failures detected; publication blocked. " + ("Inspect the local report." if save_local else "No report files retained; rerun with --save-local for restricted diagnostics."))
        if not publish:
            print("Run complete: local outputs only; SharePoint was not updated.")
            return 0
        if canonical_uuid(report["metadata"]["tenant"]) != tenant:
            raise WiseError("Source tenant differs from configured authenticated tenant.")
        print(f"[5/5] Publishing to {site} (tenant {tenant})...", flush=True)
        if args.confirm:
            try:
                approved = input("Update existing dashboard" + (" and summary row" if not args.skip_summary else " only") + "? Type yes: ").strip().lower() == "yes"
            except EOFError:
                approved = False
            if not approved:
                print("Publication declined; " + ("local outputs retained." if save_local else "in-memory report discarded; nothing uploaded."))
                return 2
        if args.archive_account_url:
            if args.input_report:
                bootstrap_dependencies(args.auth, args.no_install)
            print("[Archive] Uploading dated JSON history to Blob Storage before SharePoint...", flush=True)
            archive_reports(report, snapshot, args.archive_account_url, args.archive_container, args.archive_prefix)
        else:
            print("  Blob archive not configured; set WISE_ARCHIVE_ACCOUNT_URL to enable it.", flush=True)
        if client is None:
            print(f"  Imported report: starting {args.auth} authentication for SharePoint publication...", flush=True)
            bootstrap_dependencies(args.auth, args.no_install)
            auth = AuthSession(tenant, client_id, ["Sites.ReadWrite.All"], args.username, args.auth)
            client = GraphClient(auth.token)
            if args.auth == "managed-identity":
                organizations = client.get_paged("v1.0/organization?$select=id")
                if len(organizations) != 1 or canonical_uuid(organizations[0].get("id")) != tenant:
                    raise WiseError("Managed identity organization does not match configured tenant; publication blocked.")
        publish_dashboard(client, snapshot, site, tenant, report=report, update_summary=not args.skip_summary)
        print("  SharePoint dashboard readback verified" + ("; summary row readback verified." if not args.skip_summary else "; summary row not requested."))
        print("  Legacy static statistics HTML was not regenerated. It uses retired patch-status categories and is not a current report output.")
        print("Run complete: collection, " + ("local output readback, " if save_local else "") + ("verified Blob archive, " if args.archive_account_url else "") + "and requested SharePoint publication succeeded.")
        return 0
    except KeyboardInterrupt:
        print("Cancelled. Inspect destination version history if interrupted during publication.", file=sys.stderr)
        return 1
    except (WiseError, OSError, ValueError, TypeError, KeyError, AttributeError, ImportError):
        error = sys.exc_info()[1]
        print(str(error) if isinstance(error, WiseError) else "Operation failed: invalid input, local I/O, dependency or response shape. No success claimed.", file=sys.stderr)
        return 1
    finally:
        if auth is not None and args.auth == "managed-identity":
            auth.credential.close()


if __name__ == "__main__":
    raise SystemExit(main())