"""Dependency-free regression tests; run with unittest discovery or this file.

WISE_TEST_REPORT optionally selects an existing, read-only 17-device export.
WISE_TEST_OPERATIONAL optionally selects its existing JS schema-v2 artifact.
Neither fixture is generated here. All write/collection fixtures are fabricated.
Authentication, collection, and publication are mocked; no credentials or network
access are needed.
"""

from __future__ import annotations

import contextlib
import copy
import datetime as dt
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock


HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
SPEC = importlib.util.spec_from_file_location(
    "wise_endpoint_intelligence_under_test", HERE / "wise_endpoint_intelligence.py"
)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("Cannot locate the implementation beside the test file.")
wise = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(wise)
MISTRAL_SPEC = importlib.util.spec_from_file_location("mistral_intelligence_under_test", HERE / "mistral_intelligence.py")
if MISTRAL_SPEC is None or MISTRAL_SPEC.loader is None:
    raise RuntimeError("Cannot locate the Mistral adapter beside the test file.")
mistral = importlib.util.module_from_spec(MISTRAL_SPEC)
MISTRAL_SPEC.loader.exec_module(mistral)

TENANT = "11111111-1111-4111-8111-111111111111"
OTHER_TENANT = "22222222-2222-4222-8222-222222222222"
SITE = "https://fabricated.sharepoint.com/sites/EndpointIQ"
STAMP = "2026-10-23T12:00:00Z"
BEARER = "FABRICATED_BEARER_DO_NOT_LOG"


def setUpModule():
    global default_patches
    default_patches = [mock.patch.object(wise, "DEFAULT_TENANT", TENANT),
                       mock.patch.object(wise, "DEFAULT_CLIENT", OTHER_TENANT),
                       mock.patch.object(wise, "DEFAULT_SITE", SITE)]
    for patcher in default_patches:
        patcher.start()


def tearDownModule():
    for patcher in reversed(default_patches):
        patcher.stop()


class AutomaticPublicationTests(unittest.TestCase):
    def test_public_release_requires_explicit_tenant_configuration(self):
        with mock.patch.object(wise, "DEFAULT_TENANT", ""), mock.patch.object(wise, "DEFAULT_CLIENT", ""), mock.patch.object(wise, "DEFAULT_SITE", ""), mock.patch.dict(wise.os.environ, {}, clear=True), mock.patch.object(wise, "AuthSession") as auth, contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(wise.main([]), 1)
            auth.assert_not_called()

    def test_default_live_run_publishes_without_terminal_confirmation(self):
        report = fabricated_report()
        report["collectionStatus"] = {"status": "complete", "sources": []}
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(wise, "bootstrap_dependencies"), mock.patch.object(wise, "AuthSession"), mock.patch.object(wise, "GraphClient"), mock.patch.object(wise, "collect_report", return_value=report), mock.patch.object(wise, "archive_reports"), mock.patch.object(wise, "publish_dashboard") as publish, mock.patch("builtins.input", side_effect=AssertionError("Automatic mode must not prompt")), contextlib.redirect_stdout(io.StringIO()):
            result = wise.main(["--tenant-id", TENANT, "--output-dir", directory])
        self.assertEqual(result, 0)
        publish.assert_called_once()

    def test_opt_in_confirmation_can_decline_publication(self):
        report = fabricated_report()
        report["collectionStatus"] = {"status": "complete", "sources": []}
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(wise, "bootstrap_dependencies"), mock.patch.object(wise, "AuthSession"), mock.patch.object(wise, "GraphClient"), mock.patch.object(wise, "collect_report", return_value=report), mock.patch.object(wise, "publish_dashboard") as publish, mock.patch("builtins.input", return_value="no"), contextlib.redirect_stdout(io.StringIO()):
            result = wise.main(["--tenant-id", TENANT, "--output-dir", directory, "--confirm"])
        self.assertEqual(result, 2)
        publish.assert_not_called()

    def test_failed_collection_still_blocks_automatic_publication(self):
        report = fabricated_report()
        report["collectionStatus"] = {"status": "error", "sources": []}
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(wise, "bootstrap_dependencies"), mock.patch.object(wise, "AuthSession"), mock.patch.object(wise, "GraphClient"), mock.patch.object(wise, "collect_report", return_value=report), mock.patch.object(wise, "publish_dashboard") as publish, mock.patch("builtins.input", side_effect=AssertionError("Must not prompt")), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            result = wise.main(["--tenant-id", TENANT, "--output-dir", directory])
        self.assertEqual(result, 1)
        publish.assert_not_called()


class HostedPipelineTests(unittest.TestCase):
    def test_collection_failure_logs_safe_category_and_blocks_publication(self):
        report = fabricated_report()
        report["collectionStatus"] = {"status": "error", "sources": [{"source": "configurationDetails:PRIVATE_DEVICE_ID", "status": "error", "httpStatus": 429, "diagnostic": "PRIVATE_RESPONSE"}]}
        with mock.patch.dict(wise.os.environ, {"WISE_TENANT_ID": TENANT, "WISE_ARCHIVE_ACCOUNT_URL": "https://fabricated.blob.core.windows.net"}, clear=True), mock.patch.object(wise, "AuthSession"), mock.patch.object(wise, "GraphClient") as client, mock.patch.object(wise, "collect_report", return_value=report), mock.patch.object(wise, "archive_reports") as archive, mock.patch.object(wise, "publish_dashboard") as publish, self.assertLogs("wise.pipeline", level="INFO") as logs:
            client.return_value.get_paged.return_value = [{"id": TENANT}]
            with self.assertRaises(wise.WiseError):
                wise.run_hosted_pipeline()
        output = " ".join(logs.output)
        self.assertIn("category=configurationDetails; HTTP=429", output)
        self.assertNotIn("PRIVATE_DEVICE_ID", output)
        self.assertNotIn("PRIVATE_RESPONSE", output)
        archive.assert_not_called()
        publish.assert_not_called()

    def test_hosted_client_credentials_keep_blob_auth_separate(self):
        report = fabricated_report()
        report["collectionStatus"] = {"status": "complete", "sources": []}
        settings = {"WISE_GRAPH_AUTH": "client-credentials", "WISE_TENANT_ID": TENANT, "WISE_CLIENT_ID": OTHER_TENANT, "WISE_CLIENT_SECRET": "FABRICATED_SECRET", "WISE_ARCHIVE_CONNECTION_STRING": "FABRICATED_STORAGE", "WISE_ARCHIVE_ACCOUNT_URL": "https://fabricated.blob.core.windows.net"}
        with mock.patch.dict(wise.os.environ, settings, clear=True), mock.patch.object(wise, "AuthSession") as auth, mock.patch.object(wise, "GraphClient") as client, mock.patch.object(wise, "collect_report", return_value=report), mock.patch.object(wise, "archive_reports", return_value="reports/fabricated") as archive, mock.patch.object(wise, "publish_dashboard", return_value={"dashboardVerified": True, "summaryVerified": True}):
            client.return_value.get_paged.return_value = [{"id": TENANT}]
            wise.run_hosted_pipeline()
            auth.assert_called_once_with(TENANT, OTHER_TENANT, [], auth="client-credentials")
            self.assertIsNone(archive.call_args.kwargs["credential"])
            auth.return_value.credential.close.assert_called_once()

    def test_hosted_order_failures_and_no_local_side_effects(self):
        for failure in [None, "collection", "archive", "sharepoint"]:
            report = fabricated_report()
            report["collectionStatus"] = {"status": "error" if failure == "collection" else "complete", "sources": []}
            events = []
            def archive(*args, **kwargs):
                events.append("archive")
                if failure == "archive":
                    raise wise.WiseError("Fabricated archive failure")
                return "reports/fabricated-run"
            def publish(*args, **kwargs):
                events.append("sharepoint")
                if failure == "sharepoint":
                    raise wise.WiseError("Fabricated publication failure")
                return {"dashboardVerified": True, "summaryVerified": True}
            with self.subTest(failure=failure), mock.patch.dict(wise.os.environ, {"WISE_TENANT_ID": TENANT, "WISE_ARCHIVE_ACCOUNT_URL": "https://fabricated.blob.core.windows.net"}, clear=True), mock.patch.object(wise, "AuthSession") as auth, mock.patch.object(wise, "GraphClient") as client, mock.patch.object(wise, "collect_report", return_value=report), mock.patch.object(wise, "archive_reports", side_effect=archive) as archived, mock.patch.object(wise, "publish_dashboard", side_effect=publish), mock.patch.object(wise, "write_outputs") as local, mock.patch.object(wise, "bootstrap_dependencies") as bootstrap, mock.patch("builtins.input", side_effect=AssertionError("Hosted code cannot prompt")), self.assertLogs("wise.pipeline", level="INFO"):
                client.return_value.get_paged.return_value = [{"id": TENANT}]
                if failure:
                    with self.assertRaises(wise.WiseError):
                        wise.run_hosted_pipeline()
                else:
                    result = wise.run_hosted_pipeline()
                    self.assertTrue(result["summaryVerified"])
                    self.assertEqual(result["archivePath"], "reports/fabricated-run")
                    self.assertIs(archived.call_args.kwargs["credential"], auth.return_value.credential)
                auth.return_value.credential.close.assert_called_once()
                local.assert_not_called()
                bootstrap.assert_not_called()
            self.assertEqual(events, [] if failure == "collection" else ["archive"] if failure == "archive" else ["archive", "sharepoint"])

    def test_mistral_output_attaches_before_archive_and_failure_fails_open(self):
        report = fabricated_report()
        report["collectionStatus"] = {"status": "complete", "sources": []}
        events = []
        def archive(_report, snapshot, *_args, **_kwargs):
            events.append("archive")
            self.assertEqual(snapshot["aiIntelligence"]["status"], "available")
            return "reports/fabricated"
        with mock.patch.dict(wise.os.environ, {"WISE_TENANT_ID": TENANT, "WISE_ARCHIVE_ACCOUNT_URL": "https://fabricated.blob.core.windows.net"}, clear=True), \
             mock.patch.object(wise, "AuthSession"), mock.patch.object(wise, "GraphClient") as client, \
             mock.patch.object(wise, "collect_report", return_value=report), mock.patch.object(wise, "archive_reports", side_effect=archive), \
             mock.patch.object(wise, "publish_dashboard", return_value={"dashboardVerified": True, "summaryVerified": True}), \
             mock.patch.dict(sys.modules, {"mistral_intelligence": mock.Mock(generate_intelligence=mock.Mock(return_value=({"status": "available", "executive_summary": {}, "key_insights": [], "recommendations": []}, None)))}):
            client.return_value.get_paged.return_value = [{"id": TENANT}]
            wise.run_hosted_pipeline()
        self.assertEqual(events, ["archive"])
        with mock.patch.dict(sys.modules, {"mistral_intelligence": mock.Mock(generate_intelligence=mock.Mock(return_value=(None, "timeout")))}), \
             mock.patch.dict(wise.os.environ, {"WISE_TENANT_ID": TENANT, "WISE_ARCHIVE_ACCOUNT_URL": "https://fabricated.blob.core.windows.net"}, clear=True), \
             mock.patch.object(wise, "AuthSession"), mock.patch.object(wise, "GraphClient") as client, \
             mock.patch.object(wise, "collect_report", return_value=report), mock.patch.object(wise, "archive_reports") as archive, \
             mock.patch.object(wise, "publish_dashboard", return_value={"dashboardVerified": True, "summaryVerified": True}), \
             self.assertLogs("wise.pipeline", level="INFO") as logs:
            client.return_value.get_paged.return_value = [{"id": TENANT}]
            wise.run_hosted_pipeline()
        self.assertEqual(archive.call_args.args[1]["aiIntelligence"], {"status": "unavailable", "reason": "timeout"})
        self.assertNotIn("FABRICATED_SECRET", " ".join(logs.output))
        self.assertNotIn("PRIVATE_DEVICE_ID", " ".join(logs.output))


class MistralIntelligenceTests(unittest.TestCase):
    def test_prompt_projection_contains_aggregates_not_inventory_or_identifiers(self):
        snapshot = wise.build_operational(fabricated_report())
        facts = mistral.build_ai_facts(snapshot)
        encoded = json.dumps(facts)
        self.assertEqual(facts["total_devices"], 3)
        for forbidden in ("device-a", "Fabricated A", "user-a", "absent-user", "deviceId", '"devices"'):
            self.assertNotIn(forbidden, encoded)

    def test_registration_approval_state_is_not_a_reported_exception_or_ai_category(self):
        snapshot = wise.build_operational(fabricated_report())
        facts = mistral.build_ai_facts(snapshot)
        self.assertNotIn("enrollment", snapshot["summary"]["exceptionCounts"])
        self.assertNotIn("enrollment", facts["exception_counts"])
        self.assertNotIn("enrollment", mistral.ISSUE_KEYS)
        self.assertNotIn("enrollment", facts["issue_metrics"])

    def test_strict_output_validation_and_bounded_content(self):
        valid = {"executive_summary": {"introduction": "Evidence only", "findings": "One finding", "bottomLine": "Review"},
             "key_insights": [{"key": "noncompliant", "title": "Compliance", "observation": "Observed", "implication": "Review", "priority": "high"}],
             "recommendations": [{"key": "noncompliant", "title": "Review", "action": "Check policy results", "owner": "Endpoint operations", "priority": "medium"}]}
        normalized = mistral.validate_intelligence(json.dumps(valid))
        self.assertEqual(normalized["executive_summary"], valid["executive_summary"])
        self.assertEqual(normalized["recommendations"][0]["key"], "noncompliant")
        invalid = copy.deepcopy(valid)
        invalid["recommendations"][0]["priority"] = "P1"
        with self.assertRaises(ValueError):
            mistral.validate_intelligence(invalid)
        invalid = copy.deepcopy(valid)
        invalid["executive_summary"]["findings"] = "x" * 1201
        with self.assertRaises(ValueError):
            mistral.validate_intelligence(invalid)
        invalid = copy.deepcopy(valid)
        invalid["executive_summary"]["findings"] = "3 devices need review"
        with self.assertRaisesRegex(mistral.IntelligenceValidationError, "unapproved_numeric_claim"):
            mistral.validate_intelligence(invalid)

    def test_validation_tolerates_extra_fields_and_normalizes_priority(self):
        value = {"executive_summary": {"introduction": "Evidence", "findings": "Review", "bottomLine": "Act", "extra": "ignored"},
             "key_insights": [{"key": "threat", "title": "Threats", "observation": "Reported", "implication": "Review", "priority": "HIGH", "extra": "ignored"}],
             "recommendations": [{"key": "threat", "title": "Investigate", "action": "Check alerts", "owner": "Security", "priority": "Medium", "extra": "ignored"}],
                 "metadata": "ignored"}
        result = mistral.validate_intelligence(value)
        self.assertEqual(result["key_insights"][0]["priority"], "high")
        self.assertEqual(result["recommendations"][0]["priority"], "medium")
        self.assertNotIn("extra", result["key_insights"][0])

    def test_validation_failure_categories_are_specific(self):
        with self.assertRaisesRegex(mistral.IntelligenceValidationError, "invalid_json"):
            mistral.validate_intelligence("not json")
        invalid = {"executive_summary": {"introduction": "A", "findings": "B", "bottomLine": "C"},
               "key_insights": [{"key": "threat", "title": "Issue", "observation": "Observed", "implication": "Review", "priority": "P1"}],
                   "recommendations": []}
        with self.assertRaisesRegex(mistral.IntelligenceValidationError, "invalid_priority"):
            mistral.validate_intelligence(invalid)

    def test_generate_retries_validation_failure_once(self):
        snapshot = wise.build_operational(fabricated_report())
        facts = mistral.build_ai_facts(snapshot)
        categories = list(facts["issue_metrics"])
        valid = {"executive_summary": {"introduction": "Report {{total_devices}}", "findings": "Finding", "bottomLine": "Review"},
             "key_insights": [{"key": key, "title": key, "observation": "Evidence is present", "implication": "Review the reported category", "priority": "high"} for key in categories],
             "recommendations": [{"key": key, "title": key, "action": "Review the reported evidence and follow the approved process.", "owner": "Endpoint operations", "priority": "high"} for key in categories]}
        client = mock.Mock()
        client.chat.completions.create.side_effect = [
            mock.Mock(choices=[mock.Mock(message=mock.Mock(content="not json"))]),
            mock.Mock(choices=[mock.Mock(message=mock.Mock(content=json.dumps(valid)))])]
        result, reason = mistral.generate_intelligence(snapshot, client=client)
        self.assertEqual(result["status"], "available")
        self.assertIn(str(snapshot["summary"]["totalDevices"]), result["executive_summary"]["introduction"])
        self.assertIsNone(reason)
        self.assertEqual(client.chat.completions.create.call_count, 2)

    def test_numeric_validation_retry_supplies_correction_without_echoing_response(self):
        snapshot = wise.build_operational(fabricated_report())
        categories = mistral.build_ai_facts(snapshot)["issue_metrics"]
        valid = {"executive_summary": {"introduction": "Coverage {{total_devices}}", "findings": "Review evidence", "bottomLine": "Prioritize evidenced issues"},
                 "key_insights": [{"key": key, "title": key, "observation": "Evidence", "implication": "Review", "priority": "high"} for key in categories],
                 "recommendations": [{"key": key, "title": key, "action": "Review evidence", "owner": "Operations", "priority": "high"} for key in categories]}
        invalid = copy.deepcopy(valid)
        invalid["executive_summary"]["introduction"] = "PRIVATE_RESPONSE 999 devices"
        client = mock.Mock()
        client.chat.completions.create.side_effect = [
            mock.Mock(choices=[mock.Mock(message=mock.Mock(content=json.dumps(invalid)))]),
            mock.Mock(choices=[mock.Mock(message=mock.Mock(content=json.dumps(valid)))])]
        with self.assertLogs("wise.mistral", level="INFO") as logs:
            result, reason = mistral.generate_intelligence(snapshot, client=client)
        self.assertIsNone(reason)
        self.assertEqual(result["status"], "available")
        first, second = client.chat.completions.create.call_args_list
        self.assertNotEqual(first.kwargs["messages"], second.kwargs["messages"])
        correction = json.dumps(second.kwargs["messages"])
        self.assertIn("unapproved_numeric_claim", correction)
        self.assertIn("{{total_devices}}", correction)
        self.assertNotIn("PRIVATE_RESPONSE", correction)
        self.assertNotIn("PRIVATE_RESPONSE", " ".join(logs.output))
        self.assertIn("response received", " ".join(logs.output))

    def test_summary_placeholder_is_substituted_and_issue_coverage_is_exact(self):
        snapshot = wise.build_operational(fabricated_report())
        facts = mistral.build_ai_facts(snapshot)
        expected = set(facts["issue_metrics"])
        self.assertIn("{{issue_critical_storage}}", facts["summary_placeholders"])
        self.assertIn("criticalStorage", facts["issue_metrics"])
        response = {"executive_summary": {"introduction": "This report covers {{total_devices}} devices.", "findings": "Review {{compliance}} and {{issue_critical_storage}}.", "bottomLine": "Assess {{issue_noncompliant}}."},
                    "key_insights": [{"key": key, "title": key, "observation": "Evidence is present", "implication": "Review the reported category", "priority": "high"} for key in expected],
                    "recommendations": [{"key": key, "title": key, "action": "Review only the reported evidence and follow the approved process.", "owner": "Endpoint operations", "priority": "high"} for key in expected]}
        rendered = mistral.render_intelligence(mistral._validate_issue_coverage(mistral.validate_intelligence(response), facts), facts)
        self.assertIn(str(snapshot["summary"]["totalDevices"]), rendered["executive_summary"]["introduction"])
        self.assertEqual({row["key"] for row in rendered["recommendations"]}, expected)
        response["recommendations"].pop()
        with self.assertRaisesRegex(mistral.IntelligenceValidationError, "incomplete_issue_coverage"):
            mistral._validate_issue_coverage(mistral.validate_intelligence(response), facts)
        bad_placeholder = {"executive_summary": {"introduction": "Threat gaps {{issue_threatGap}}", "findings": "Evidence", "bottomLine": "Review"},
                           "key_insights": [{"key": key, "title": key, "observation": "Evidence", "implication": "Review", "priority": "high"} for key in expected],
                           "recommendations": [{"key": key, "title": key, "action": "Review the reported evidence.", "owner": "Endpoint operations", "priority": "high"} for key in expected]}
        with self.assertRaisesRegex(mistral.IntelligenceValidationError, "unknown_summary_placeholder"):
            mistral.render_intelligence(mistral._validate_issue_coverage(mistral.validate_intelligence(bad_placeholder), facts), facts)

    def test_model_failure_returns_no_ai_data(self):
        snapshot = wise.build_operational(fabricated_report())
        result, reason = mistral.generate_intelligence(snapshot, client=mock.Mock(chat=mock.Mock(completions=mock.Mock(create=mock.Mock(side_effect=TimeoutError)))))
        self.assertIsNone(result)
        self.assertEqual(reason, "timeout")

    def test_numeric_product_names_are_valid_output(self):
        value = {"executive_summary": {"introduction": "Windows 11 servicing needs review", "findings": "Evidence is mixed", "bottomLine": "Confirm the supported release"},
                 "key_insights": [], "recommendations": []}
        self.assertIn("Windows 11", mistral.validate_intelligence(value)["executive_summary"]["introduction"])

    def test_connectivity_test_uses_synthetic_facts(self):
        client = mock.Mock()
        client.chat.completions.create.return_value.choices = [mock.Mock(message=mock.Mock(content=json.dumps({
            "executive_summary": {"introduction": "Synthetic", "findings": "Synthetic", "bottomLine": "Synthetic"},
            "key_insights": [], "recommendations": []})))]
        result = mistral.test_connection(client)
        self.assertEqual(result["status"], "ok")
        sent = client.chat.completions.create.call_args.kwargs["messages"][1]["content"]
        self.assertIn("total_devices", sent)
        self.assertNotIn("deviceId", sent)

    def test_missing_archive_setting_stops_before_authentication(self):
        with mock.patch.dict(wise.os.environ, {}, clear=True), mock.patch.object(wise, "AuthSession") as auth:
            with self.assertRaisesRegex(wise.WiseError, "WISE_ARCHIVE_ACCOUNT_URL"):
                wise.run_hosted_pipeline()
            auth.assert_not_called()

    def test_wrong_organization_stops_before_collection(self):
        with mock.patch.dict(wise.os.environ, {"WISE_TENANT_ID": TENANT, "WISE_ARCHIVE_ACCOUNT_URL": "https://fabricated.blob.core.windows.net"}, clear=True), mock.patch.object(wise, "AuthSession") as auth, mock.patch.object(wise, "GraphClient") as client, mock.patch.object(wise, "collect_report") as collect, self.assertLogs("wise.pipeline", level="ERROR"):
            client.return_value.get_paged.return_value = [{"id": OTHER_TENANT}]
            with self.assertRaisesRegex(wise.WiseError, "authentication"):
                wise.run_hosted_pipeline()
            collect.assert_not_called()
            auth.return_value.credential.close.assert_called_once()


class BlobArchiveTests(unittest.TestCase):
    def test_connection_string_archive_and_endpoint_guard(self):
        report = fabricated_report()
        snapshot = wise.build_operational(report)
        for matches in [True, False]:
            storage = mock.MagicMock()
            service = storage.BlobServiceClient.from_connection_string.return_value.__enter__.return_value
            service.url = "https://fabricated.blob.core.windows.net" if matches else "https://wrongaccount.blob.core.windows.net"
            blob = service.get_blob_client.return_value.__enter__.return_value
            blob.download_blob.return_value.chunks.side_effect = [[wise.json_bytes(wise.sanitize(document))] for document in [report, snapshot]]
            with self.subTest(matches=matches), mock.patch.dict(wise.os.environ, {"WISE_ARCHIVE_CONNECTION_STRING": "FABRICATED_SECRET"}), mock.patch.object(wise.importlib, "import_module", return_value=storage), contextlib.redirect_stdout(io.StringIO()):
                if matches:
                    wise.archive_reports(report, snapshot, "https://fabricated.blob.core.windows.net", "wise-reports")
                    self.assertEqual(blob.upload_blob.call_count, 2)
                else:
                    with self.assertRaisesRegex(wise.WiseError, "endpoint differs"):
                        wise.archive_reports(report, snapshot, "https://fabricated.blob.core.windows.net", "wise-reports")
                    blob.upload_blob.assert_not_called()
                storage.BlobServiceClient.assert_not_called()

    def test_normal_command_defaults_to_configured_blob_before_sharepoint(self):
        report = fabricated_report()
        report["collectionStatus"] = {"status": "complete", "sources": []}
        events = []
        with mock.patch.dict(wise.os.environ, {}, clear=True), mock.patch.object(wise, "bootstrap_dependencies"), mock.patch.object(wise, "AuthSession"), mock.patch.object(wise, "GraphClient"), mock.patch.object(wise, "collect_report", return_value=report), mock.patch.object(wise, "write_outputs") as local, mock.patch.object(wise, "archive_reports", side_effect=lambda *args: events.append("blob")) as archive, mock.patch.object(wise, "publish_dashboard", side_effect=lambda *args, **kwargs: events.append("sharepoint")), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(wise.main(["--extra-details", "--tenant-id", TENANT, "--client-id", OTHER_TENANT, "--site-url", SITE, "--archive-account-url", "https://fabricated.blob.core.windows.net"]), 0)
            self.assertEqual(archive.call_args.args[2:5], ("https://fabricated.blob.core.windows.net", "wise-reports", "reports"))
            self.assertEqual(events, ["blob", "sharepoint"])
            local.assert_not_called()

    def test_blob_publication_skips_local_files_unless_requested(self):
        for save_local in [False, True]:
            report = fabricated_report()
            report["collectionStatus"] = {"status": "complete", "sources": []}
            with self.subTest(save_local=save_local), tempfile.TemporaryDirectory() as directory, mock.patch.object(wise, "bootstrap_dependencies"), mock.patch.object(wise, "AuthSession"), mock.patch.object(wise, "GraphClient"), mock.patch.object(wise, "collect_report", return_value=report), mock.patch.object(wise, "archive_reports") as archive, mock.patch.object(wise, "publish_dashboard") as publish, mock.patch.object(wise, "write_outputs", wraps=wise.write_outputs) as write, contextlib.redirect_stdout(io.StringIO()):
                output = Path(directory) / "not-created-by-memory-mode"
                arguments = ["--tenant-id", TENANT, "--output-dir", str(output), "--archive-account-url", "https://fabricated.blob.core.windows.net"]
                if save_local:
                    arguments.append("--save-local")
                self.assertEqual(wise.main(arguments), 0)
                self.assertEqual(write.call_count, int(save_local))
                self.assertEqual(output.exists(), save_local)
                archive.assert_called_once()
                publish.assert_called_once()

    def test_unique_history_paths_and_verified_json_uploads(self):
        report = fabricated_report()
        snapshot = wise.build_operational(report)
        storage = mock.MagicMock()
        service = storage.BlobServiceClient.return_value.__enter__.return_value
        blob = service.get_blob_client.return_value.__enter__.return_value
        blob.download_blob.return_value.chunks.side_effect = [[payload[:7], payload[7:]] for payload in [wise.json_bytes(wise.sanitize(document)) for document in [report, snapshot, report, snapshot]]]
        credential = mock.Mock()
        with mock.patch.object(wise.importlib, "import_module", return_value=storage), contextlib.redirect_stdout(io.StringIO()):
            first = wise.archive_reports(report, snapshot, "https://fabricated.blob.core.windows.net", "wise-reports", credential=credential)
            second = wise.archive_reports(report, snapshot, "https://fabricated.blob.core.windows.net", "wise-reports", credential=credential)
        self.assertNotEqual(first, second)
        self.assertTrue(first.startswith(f"reports/{TENANT}/"))
        self.assertEqual(blob.upload_blob.call_count, 4)
        blob.download_blob.return_value.readall.assert_not_called()
        for upload in blob.upload_blob.call_args_list:
            self.assertFalse(upload.kwargs["overwrite"])
            self.assertEqual(upload.kwargs["metadata"]["sha256"], hashlib.sha256(upload.args[0]).hexdigest())
            json.loads(upload.args[0])
        credential.close.assert_not_called()

    def test_hash_mismatch_blocks_archive_success(self):
        storage = mock.MagicMock()
        blob = storage.BlobServiceClient.return_value.__enter__.return_value.get_blob_client.return_value.__enter__.return_value
        blob.download_blob.return_value.chunks.return_value = [b"wrong content"]
        report = fabricated_report()
        with mock.patch.object(wise.importlib, "import_module", return_value=storage):
            with self.assertRaisesRegex(wise.WiseError, "SHA-256 mismatch"):
                wise.archive_reports(report, wise.build_operational(report), "https://fabricated.blob.core.windows.net", "wise-reports", credential=mock.Mock())

    def test_invalid_destination_rejected_before_credentials(self):
        report = fabricated_report()
        with mock.patch.object(wise.importlib, "import_module") as imported:
            for url in ["http://fabricated.blob.core.windows.net", "https://example.com", "https://fabricated.blob.core.windows.net?sig=secret"]:
                with self.subTest(url=url), self.assertRaises(wise.WiseError):
                    wise.archive_reports(report, wise.build_operational(report), url, "wise-reports")
            imported.assert_not_called()

    def test_archive_precedes_sharepoint_and_failure_blocks_publication(self):
        for fail in [False, True]:
            report = fabricated_report()
            report["collectionStatus"] = {"status": "complete", "sources": []}
            events = []
            def archive(*args):
                events.append("archive")
                if fail:
                    raise wise.WiseError("Archive failed")
            with self.subTest(fail=fail), tempfile.TemporaryDirectory() as directory, mock.patch.object(wise, "bootstrap_dependencies"), mock.patch.object(wise, "AuthSession"), mock.patch.object(wise, "GraphClient"), mock.patch.object(wise, "collect_report", return_value=report), mock.patch.object(wise, "archive_reports", side_effect=archive), mock.patch.object(wise, "publish_dashboard", side_effect=lambda *args, **kwargs: events.append("sharepoint")), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                result = wise.main(["--tenant-id", TENANT, "--output-dir", directory, "--archive-account-url", "https://fabricated.blob.core.windows.net"])
            self.assertEqual(result, 1 if fail else 0)
            self.assertEqual(events, ["archive"] if fail else ["archive", "sharepoint"])


class ManagedIdentityTests(unittest.TestCase):
    def test_client_credentials_use_application_scope_without_interactive_login(self):
        identity = mock.Mock()
        identity.ClientSecretCredential.return_value.get_token.return_value.token = BEARER
        with mock.patch.dict(wise.os.environ, {"WISE_CLIENT_SECRET": "FABRICATED_SECRET"}, clear=True), mock.patch.object(wise.importlib, "import_module", return_value=identity):
            session = wise.AuthSession(TENANT, OTHER_TENANT, [], auth="client-credentials")
            self.assertEqual(session.token(), BEARER)
            identity.ClientSecretCredential.assert_called_once_with(tenant_id=TENANT, client_id=OTHER_TENANT, client_secret="FABRICATED_SECRET")
            identity.ClientSecretCredential.return_value.get_token.assert_called_once_with("https://graph.microsoft.com/.default")
            identity.PublicClientApplication.assert_not_called()
            identity.ClientSecretCredential.return_value.get_token.side_effect = RuntimeError("FABRICATED_SECRET")
            with self.assertRaises(wise.WiseError) as caught:
                session.token()
            self.assertNotIn("FABRICATED_SECRET", str(caught.exception))

    def test_client_credentials_require_secret_value(self):
        with mock.patch.dict(wise.os.environ, {}, clear=True), mock.patch.object(wise.importlib, "import_module") as imported:
            with self.assertRaisesRegex(wise.WiseError, "WISE_CLIENT_SECRET"):
                wise.AuthSession(TENANT, OTHER_TENANT, [], auth="client-credentials")
            imported.assert_not_called()

    def test_managed_identity_never_starts_interactive_auth(self):
        identity = mock.Mock()
        identity.ManagedIdentityCredential.return_value.get_token.return_value.token = BEARER
        with mock.patch.object(wise.importlib, "import_module", return_value=identity), mock.patch.dict(wise.os.environ, {}, clear=True):
            session = wise.AuthSession(TENANT, OTHER_TENANT, [], auth="managed-identity")
            self.assertEqual(session.token(), BEARER)
        identity.ManagedIdentityCredential.return_value.get_token.assert_called_once_with("https://graph.microsoft.com/.default")
        identity.PublicClientApplication.assert_not_called()

    def test_managed_identity_dependencies_never_spawn_install(self):
        with mock.patch.object(wise.importlib, "import_module"), mock.patch.object(wise.subprocess, "check_call") as install, mock.patch.object(wise.subprocess, "call") as child:
            wise.bootstrap_dependencies("managed-identity")
        install.assert_not_called()
        child.assert_not_called()


class BootstrapCompletionTests(unittest.TestCase):
    def test_launcher_waits_and_propagates_child_exit_status(self):
        for return_code in [0, 1, 2]:
            with self.subTest(return_code=return_code), mock.patch.object(wise.sys, "prefix", str(HERE / "outside-private-env")), mock.patch.object(wise.sys, "argv", ["wise_endpoint_intelligence.py", "--setup-only"]), mock.patch.dict(wise.os.environ, {}, clear=True), mock.patch.object(wise.Path, "exists", return_value=True), mock.patch.object(wise.subprocess, "check_call"), mock.patch.object(wise.subprocess, "call", return_value=return_code) as child, mock.patch.object(wise.os, "execve") as execve, contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises(SystemExit) as completed:
                    wise.bootstrap_dependencies()
                self.assertEqual(completed.exception.code, return_code)
                child.assert_called_once()
                command = child.call_args.args[0]
                self.assertEqual(command[1], "-u")
                self.assertEqual(command[-1], "--setup-only")
                self.assertEqual(child.call_args.kwargs["env"]["WISE_BOOTSTRAPPED"], "1")
                execve.assert_not_called()


class AuthenticationTests(unittest.TestCase):
    def session(self, result=None, auth="browser"):
        module = mock.Mock()
        app = module.PublicClientApplication.return_value
        app.get_accounts.return_value = []
        app.acquire_token_interactive.return_value = result or {
            "access_token": BEARER, "scope": "User.Read.All",
            "id_token_claims": {"tid": TENANT, "oid": "user-one", "sub": "subject-one"}}
        with mock.patch.object(wise.importlib, "import_module", return_value=module):
            session = wise.AuthSession(TENANT, OTHER_TENANT, ["User.Read.All"], auth=auth)
        return session, app, module

    def test_browser_pkce_uses_msal_and_no_password_flow(self):
        session, app, module = self.session()
        self.assertEqual(session.token(), BEARER)
        app.acquire_token_interactive.assert_called_once_with(scopes=["User.Read.All"], login_hint=None, timeout=180)
        app.acquire_token_by_username_password.assert_not_called()
        app.initiate_device_flow.assert_not_called()
        self.assertNotIn("client_credential", module.PublicClientApplication.call_args.kwargs)

    def test_cached_account_is_tried_before_interactive(self):
        session, app, unused = self.session()
        account = {"home_account_id": "home", "local_account_id": "user-one"}
        app.get_accounts.return_value = [account]
        app.acquire_token_silent.return_value = app.acquire_token_interactive.return_value
        self.assertEqual(session.token(), BEARER)
        app.acquire_token_silent.assert_called_once_with(["User.Read.All"], account=account)
        app.acquire_token_interactive.assert_not_called()

    def test_verified_account_reuses_msal_cache_response_without_id_token(self):
        session, app, unused = self.session()
        account = {"home_account_id": "home", "local_account_id": "user-one"}
        app.get_accounts.return_value = [account]
        app.acquire_token_silent.return_value = None
        self.assertEqual(session.token(), BEARER)
        app.acquire_token_silent.return_value = {"access_token": BEARER, "expires_in": 3600, "token_type": "Bearer"}
        self.assertEqual(session.token(), BEARER)
        self.assertEqual(app.acquire_token_interactive.call_count, 1)

    def test_unverified_claimless_cache_requires_interactive_validation(self):
        session, app, unused = self.session()
        app.get_accounts.return_value = [{"home_account_id": "home", "local_account_id": "user-one"}]
        app.acquire_token_silent.return_value = {"access_token": BEARER, "expires_in": 3600}
        self.assertEqual(session.token(), BEARER)
        app.acquire_token_interactive.assert_called_once()

    def test_wrong_tenant_or_missing_scope_is_rejected(self):
        for changes in [{"id_token_claims": {"tid": OTHER_TENANT, "oid": "user-one"}}, {"scope": "User.Read"}]:
            session, app, unused = self.session()
            app.acquire_token_interactive.return_value.update(changes)
            with self.assertRaises(wise.WiseError):
                session.token()

    def test_multiple_accounts_are_not_guessed(self):
        session, app, unused = self.session()
        app.get_accounts.return_value = [{"home_account_id": "one"}, {"home_account_id": "two"}]
        with self.assertRaisesRegex(wise.WiseError, "Multiple accounts"):
            session.token()
        app.acquire_token_interactive.assert_not_called()

    def test_broker_uses_console_parent_window(self):
        session, app, module = self.session(auth="broker")
        self.assertEqual(session.token(), BEARER)
        self.assertTrue(module.PublicClientApplication.call_args.kwargs["enable_broker_on_windows"])
        self.assertEqual(app.acquire_token_interactive.call_args.kwargs["parent_window_handle"], app.CONSOLE_WINDOW_HANDLE)

    def test_setup_only_cannot_authenticate_collect_or_publish(self):
        with mock.patch.object(wise, "bootstrap_dependencies") as bootstrap, mock.patch.object(wise, "AuthSession") as auth, mock.patch.object(wise, "collect_report") as collect, mock.patch.object(wise, "publish_dashboard") as publish, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(wise.main(["--setup-only"]), 0)
        bootstrap.assert_called_once_with("browser", False)
        auth.assert_not_called()
        collect.assert_not_called()
        publish.assert_not_called()


def fabricated_report():
    devices = [
        dict(deviceId="device-a", deviceName="Fabricated A", operatingSystem="Windows",
             osVersion="10.0.26200.1", manufacturer="Example", managementAgent="mdm",
             managementState="managed", complianceState="compliant", isEncrypted=True,
             userId="user-a", lastSyncDateTime="2026-10-22T12:00:00Z", staleFlag=False,
             totalStorageSpaceGB=100, freeStorageSpaceGB=50, partnerReportedThreatState="secured"),
        dict(deviceId="device-b", deviceName="Fabricated B", operatingSystem="Windows",
             osVersion="10.0.19045.1", manufacturer="Example", managementAgent="mdm",
             managementState="retirePending", complianceState="noncompliant", isEncrypted=False,
             userId=None, lastSyncDateTime="2026-01-01T12:00:00Z", staleFlag=True,
             totalStorageSpaceGB=100, freeStorageSpaceGB=1, partnerReportedThreatState="highSeverity",
             managementCertificateExpired=True, deviceRegistrationState="approvalPending", jailBroken="True"),
        dict(deviceId="device-c", deviceName="Fabricated C", operatingSystem="iOS",
             managementState="managed", complianceState="configManager", isEncrypted=None,
             userId="absent-user", staleFlag=None, lastSyncDateTime="0001-01-01T00:00:00Z",
             totalStorageSpaceGB=0, freeStorageSpaceGB=0, partnerReportedThreatState="deactivated"),
    ]
    return {
        "metadata": dict(tenant=TENANT, collectionTimestamp=STAMP, totalDevices=3,
                         reportTitle="Fabricated report", generatedBy="unittest", sourceSystems=["Fake Graph"]),
        wise.SECTIONS["inventory"]: dict(totalCount=3, devices=devices),
        wise.SECTIONS["users"]: dict(uniqueUsers=[dict(userId="USER-A", deviceCount=1)]),
        wise.SECTIONS["security"]: dict(endpointSecurityPolicies=[]),
        wise.SECTIONS["encryption"]: dict(windowsDeviceEncryption=[], mobileDeviceEncryption=[]),
        wise.SECTIONS["compliance"]: dict(
            tenantComplianceSummary={"compliantDeviceCount": 99},
            perDeviceCompliance=[dict(deviceId=row["deviceId"], complianceState=row["complianceState"]) for row in devices]),
        wise.SECTIONS["patch"]: dict(updateRings=[dict(ringId="ring-a")], osVersionAssessment=[
            dict(deviceId="device-a", patchStatus="Compliant", requiredBuild=1, installedBuild=1),
            dict(deviceId="device-b", patchStatus="Non-compliant", requiredBuild=1, installedBuild=0),
            dict(deviceId="device-c", patchStatus="Not applicable")]),
        wise.SECTIONS["autopilot"]: dict(
            autopilotSummary=dict(totalRegistered=2, enrolled=1, notContacted=1),
            autopilotDevices=[dict(autopilotId="ap-a", enrollmentState="enrolled"),
                              dict(autopilotId="ap-b", enrollmentState="notContacted")]),
        wise.SECTIONS["incidents"]: dict(pendingDeviceActions=[]),
        "endpointRiskHealthSummary": dict(devices=[]),
        "crossTenantSummary": dict(fleet={"totalManagedDevices": 3}),
    }


class Response:
    def __init__(self, payload=None, status=200, headers=None, content=b""):
        self.payload = {} if payload is None else payload
        self.status_code = status
        self.headers = headers or {}
        self.content = content

    def json(self):
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


class CollectionClient:
    def __init__(self, failures=None):
        self.calls = []
        self.failures = failures or {}
        self.device = dict(
            id="fake-device", deviceName="Fabricated device", operatingSystem="Windows",
            osVersion="10.0.19045.1", azureADDeviceId="fake-aad", userId="fake-user",
            userPrincipalName="fabricated@example.invalid", complianceState="noncompliant",
            managementState="retirePending", isEncrypted=False,
            lastSyncDateTime="2026-01-01T00:00:00Z", enrolledDateTime="2025-01-01T00:00:00Z",
            managementCertificateExpirationDate="2025-02-01T00:00:00Z",
            totalStorageSpaceInBytes=100 * 2**30, freeStorageSpaceInBytes=2**30,
            recoveryKey="FABRICATED_RECOVERY", vendorExtra={"safe": 42, "password": "FABRICATED_PASSWORD"})

    def _read(self, path, paged):
        self.calls.append(("paged" if paged else "get", path))
        for fragment, status in self.failures.items():
            if fragment in path:
                raise wise.GraphError(status, "FabricatedFailure", "fake-request")
        if path.startswith("deviceManagement/managedDevices") and (
            path == "deviceManagement/managedDevices" or path.startswith("deviceManagement/managedDevices?$select=")):
            return [copy.deepcopy(self.device)]
        if "detectedMalwareState" in path:
            return [dict(displayName="Fabricated malware", threatState="active")]
        if path == "deviceManagement/managedDeviceOverview":
            return dict(enrolledDeviceCount=1, deviceOperatingSystemSummary={"windowsCount": 1})
        if path.startswith("deviceManagement/intents?"):
            return [dict(id="intent-a", displayName="Security baseline", isAssigned=False)]
        if path == "deviceManagement/managedDeviceEncryptionStates":
            return [dict(managedDeviceId="fake-device", encryptionState="notEncrypted",
                         encryptionReadinessState="notReady", policyDetails=[dict(state="mismatch")])]
        if path == "deviceManagement/deviceCompliancePolicyDeviceStateSummary":
            return dict(compliantDeviceCount=0, nonCompliantDeviceCount=1)
        if path == "deviceManagement/deviceConfigurations":
            return [dict(id="ring-a", displayName="Fake ring",
                         **{"@odata.type": "#microsoft.graph.windowsUpdateForBusinessConfiguration"},
                         qualityUpdatesDeferralPeriodInDays=2, featureUpdatesPaused=True)]
        if path == "deviceManagement/windowsFeatureUpdateProfiles?$expand=assignments":
            return [dict(id="feature-a", displayName="Windows 11 25H2", featureUpdateVersion="25H2", endOfSupportDate="2027-10-12T00:00:00Z", assignments=[])]
        if path == "deviceManagement/windowsQualityUpdateProfiles?$expand=assignments":
            return [dict(id="quality-a", displayName="October quality update", releaseDateDisplayName="October 2026", assignments=[])]
        if path == "deviceManagement/windowsAutopilotDeviceIdentities":
            return [dict(id="ap-a", azureActiveDirectoryDeviceId="FAKE-AAD", enrollmentState="notContacted")]
        if path.endswith("/deviceCompliancePolicyStates") or path.endswith("/deviceConfigurationStates"):
            return []
        if path.endswith("?$select=id,hardwareInformation"):
            return dict(id="fake-device", hardwareInformation={"serialNumber": "fabricated-serial"})
        raise AssertionError("Unexpected fabricated collection endpoint: " + path)

    def get(self, path):
        return self._read(path, False)

    def get_paged(self, path):
        return self._read(path, True)

    get_release_catalog = staticmethod(lambda url, timeout: fake_release_request(url, timeout))


class ReleaseCatalogResponse:
    headers = {}
    text = """<table><tr><th>Version</th><th>End of updates: Home</th><th>Latest build</th></tr>
    <tr><td>22H2</td><td>End of updates</td><td>19045.7807</td></tr></table>
    <table><tr><th>Servicing option</th><th>Update type</th><th>Availability date</th><th>Build</th><th>KB article</th></tr>
    <tr><td>General Availability Channel</td><td>2026-10 B</td><td>2026-10-13</td><td>19045.7807</td><td>KB5126657</td></tr>
    <tr><td>General Availability Channel</td><td>2026-10 OOB</td><td>2026-10-20</td><td>19045.7900</td><td>KB5129000</td></tr></table>"""

    def raise_for_status(self):
        return None


class Windows11ReleaseCatalogResponse(ReleaseCatalogResponse):
    text = """<table><tr><th>Version</th><th>End of updates: Home</th><th>End of updates: Enterprise</th><th>Latest build</th></tr>
    <tr><td>25H2</td><td>2027-10-12</td><td>2028-10-10</td><td>26200.9711</td></tr>
    <tr><td>24H2</td><td>End of updates</td><td>2027-10-12</td><td>26100.9711</td></tr>
    <tr><td>23H2</td><td>End of updates</td><td>2026-11-10</td><td>22631.7652</td></tr></table>
    <table><tr><th>Servicing option</th><th>Update type</th><th>Availability date</th><th>Build</th><th>KB article</th></tr>
    <tr><td>General Availability Channel</td><td>2026-10 B</td><td>2026-10-13</td><td>26200.9711</td><td>KB5127648</td></tr>
    <tr><td>General Availability Channel</td><td>2026-10 OOB</td><td>2026-10-20</td><td>26200.9800</td><td>KB5129000</td></tr>
    <tr><td>General Availability Channel</td><td>2026-10 B</td><td>2026-10-13</td><td>26100.9711</td><td>KB5127648</td></tr>
    <tr><td>General Availability Channel</td><td>2026-10 B</td><td>2026-10-13</td><td>22631.7652</td><td>KB5126653</td></tr></table>"""


def fake_release_request(url, timeout):
    if url == wise.WINDOWS_RELEASE_HEALTH_URL:
        return ReleaseCatalogResponse()
    if url == wise.WINDOWS_11_RELEASE_HEALTH_URL:
        return Windows11ReleaseCatalogResponse()
    raise AssertionError("Unexpected release URL")


class PublicationClient:
    def __init__(self, report, snapshot, fail_at=None):
        self.events = []
        self.fail_at = fail_at
        self.final = wise.json_bytes(dict(schemaVersion=2, metadata={"tenant": TENANT}))
        self.original = self.final
        self.stage = None
        self.fields = wise.summary_fields(report, snapshot)
        self.saved_fields = dict(Title=wise.ROW_TITLE, Tenant=TENANT)

    def get(self, path):
        self.events.append(("GET", path, {}))
        if path == "v1.0/sites/root?$select=id,siteCollection":
            return dict(siteCollection={"hostname": "fabricated.sharepoint.com"})
        if path.startswith("v1.0/sites/fabricated.sharepoint.com:"):
            return dict(id="site-a", webUrl=SITE)
        if path.endswith("/lists/assets-a/drive"):
            return dict(id="drive-a")
        if path.endswith("/root:/WISEEndpointIntelligence/dashboard.json"):
            return dict(id="final-a", eTag='"final-etag"', file={})
        if path.endswith("/items/row-a?$expand=fields"):
            saved = dict(self.saved_fields)
            if self.fail_at == "summary-readback":
                saved["TotalDevices"] = -1
            return dict(fields=saved)
        raise AssertionError("Unexpected fabricated publication read: " + path)

    def get_paged(self, path):
        self.events.append(("PAGED", path, {}))
        if "/lists?$select=" in path:
            return [dict(id="assets-a", webUrl=SITE + "/SiteAssets", list={"template": "documentLibrary"}),
                    dict(id="summary-a", displayName=wise.SUMMARY_TITLE, description=wise.SUMMARY_MARKER)]
        if path.endswith("/columns"):
            return [dict(name=name, readOnly=False) for name in self.fields]
        if path.endswith("/items?$expand=fields&$top=2"):
            return [dict(id="row-a", eTag='"row-etag"', fields=self.saved_fields)]
        raise AssertionError("Unexpected fabricated publication collection: " + path)

    def download(self, path):
        self.events.append(("DOWNLOAD", path, {}))
        if "/items/stage-a/" in path:
            return b"corrupt-stage" if self.fail_at == "stage-hash" else self.stage
        if "/items/final-a/" in path:
            if self.fail_at == "final-hash" and self.stage is not None:
                return b"corrupt-final"
            return self.final
        raise AssertionError("Unexpected fabricated download: " + path)

    def request(self, method, path, *, headers=None, data=None, json_body=None):
        self.events.append((method, path, dict(headers or {})))
        if method == "PUT" and ".staging.json:/content" in path:
            self.stage = data
            return Response(dict(id="stage-a"))
        if method == "PUT" and path.endswith("/items/final-a/content"):
            if self.fail_at == "final-etag":
                raise wise.GraphError(412, "preconditionFailed")
            self.final = data
            return Response()
        if method == "PATCH" and path.endswith("/items/row-a/fields"):
            if self.fail_at == "summary-etag":
                raise wise.GraphError(412, "preconditionFailed")
            self.saved_fields = copy.deepcopy(json_body)
            return Response()
        if method == "DELETE" and path.endswith("/items/stage-a"):
            if self.fail_at == "cleanup":
                raise wise.GraphError(403)
            return Response(status=204)
        raise AssertionError("Unexpected fabricated publication write: " + path)


class IsolatedTestCase(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stdout = io.StringIO()
        self.stderr = io.StringIO()
        self.stack.enter_context(contextlib.redirect_stdout(self.stdout))
        self.stack.enter_context(contextlib.redirect_stderr(self.stderr))
        for target in ("socket.socket", "socket.create_connection", "subprocess.check_call",
                       "subprocess.run", "subprocess.Popen", "os.execve", "venv.EnvBuilder"):
            self.stack.enter_context(mock.patch(target, side_effect=AssertionError("External side effect blocked by unittest")))

    def tearDown(self):
        self.assertNotIn(BEARER, self.stdout.getvalue() + self.stderr.getvalue())

    def temp_path(self):
        return Path(self.stack.enter_context(tempfile.TemporaryDirectory()))


class GraphClientTests(IsolatedTestCase):
    def client(self, replies):
        transport = mock.Mock(side_effect=replies)
        sleep = mock.Mock()
        token = mock.Mock(return_value=BEARER)
        return wise.GraphClient(token, transport, sleep), transport, sleep, token

    def test_request_uses_timeout_no_redirects_and_fresh_bearer(self):
        client, transport, sleep, token = self.client([Response()])
        client.request("get", "deviceManagement/managedDevices")
        args, kwargs = transport.call_args
        self.assertEqual(args, ("GET", wise.GRAPH + "/beta/deviceManagement/managedDevices"))
        self.assertEqual(kwargs["timeout"], 60)
        self.assertIs(kwargs["allow_redirects"], False)
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer " + BEARER)
        sleep.assert_not_called()
        token.assert_called_once_with()

    def test_request_timeout_is_configurable_and_validated(self):
        with mock.patch.dict(wise.os.environ, {"WISE_GRAPH_REQUEST_TIMEOUT_SECONDS": "180"}):
            client, transport, _, _ = self.client([Response()])
            client.get("users")
            self.assertEqual(transport.call_args.kwargs["timeout"], 180)
        for value in ["0", "301", "nan", "1.5", ""]:
            with self.subTest(value=value), mock.patch.dict(wise.os.environ, {"WISE_GRAPH_REQUEST_TIMEOUT_SECONDS": value}), self.assertRaises(wise.WiseError):
                self.client([])

    def test_download_retries_redirect_target_without_forwarding_token(self):
        client, transport, sleep, _ = self.client([
            Response(status=302, headers={"Location": "https://fabricated.sharepoint.com/file"}),
            Response(status=429, headers={"Retry-After": "240"}),
            TimeoutError(BEARER), Response(content=b"verified")])
        with mock.patch.object(wise.random, "random", return_value=0):
            self.assertEqual(client.download("v1.0/drives/a/items/b/content"), b"verified")
        self.assertEqual(sleep.call_args_list, [mock.call(240), mock.call(2)])
        for call in transport.call_args_list[1:]:
            self.assertEqual(call.kwargs["headers"], {})

    def test_download_retry_budget_remains_bounded(self):
        client, transport, sleep, _ = self.client([Response(status=503)] * 6)
        with self.assertRaises(wise.GraphError):
            client.download("v1.0/drives/a/items/b/content")
        self.assertEqual(transport.call_count, 6)
        self.assertEqual(sleep.call_count, 5)

    def test_fifty_thousand_records_across_pages_and_midpage_throttle(self):
        page_size = 1000
        def responses():
            for page_index in range(50):
                if page_index == 25:
                    yield Response(status=429, headers={"Retry-After": "180"})
                payload = {"value": [{"id": str(record_index)} for record_index in range(page_index * page_size, (page_index + 1) * page_size)]}
                if page_index < 49:
                    payload["@odata.nextLink"] = wise.GRAPH + "/beta/users?$skiptoken=fabricated-" + str(page_index + 1)
                yield Response(payload)
        client, transport, sleep, _ = self.client(responses())
        records = client.get_paged("users")
        self.assertEqual(len(records), 50000)
        self.assertEqual(len({record["id"] for record in records}), 50000)
        self.assertEqual(records[0]["id"], "0")
        self.assertEqual(records[-1]["id"], "49999")
        self.assertEqual(transport.call_count, 51)
        sleep.assert_called_once_with(180)
        self.assertEqual(transport.call_args_list[25].args[1], transport.call_args_list[26].args[1])

    def test_graph_url_restrictions_precede_token_or_transport(self):
        client, transport, _, token = self.client([])
        for path in ("http://graph.microsoft.com/beta/users", "https://evil.invalid/beta/users",
                     "https://graph.microsoft.com.evil.invalid/beta/users",
                     "https://user@graph.microsoft.com/beta/users", "https://graph.microsoft.com:443/beta/users",
                     "https://graph.microsoft.com/users", "https://graph.microsoft.com/beta/users#fragment"):
            with self.subTest(path=path), self.assertRaises(wise.WiseError):
                client.get(path)
        transport.assert_not_called()
        token.assert_not_called()
        self.assertEqual(wise.GraphClient.graph_url("/v1.0/users"), wise.GRAPH + "/v1.0/users")

    def test_caller_cannot_override_authorization_or_host(self):
        client, transport, _, token = self.client([])
        for name in ("authorization", "AUTHORIZATION", "Host"):
            with self.subTest(header=name), self.assertRaises(wise.WiseError):
                client.request("GET", "users", headers={name: "fake"})
        transport.assert_not_called()
        token.assert_not_called()

    def test_get_retries_throttle_server_and_transport_failures(self):
        client, transport, sleep, token = self.client([
            Response(status=429, headers={"Retry-After": "7"}),
            Response(status=503, headers={"Retry-After": "2"}), TimeoutError(BEARER), Response({"value": []})])
        with mock.patch.object(wise.random, "random", return_value=0):
            self.assertEqual(client.get("users"), {"value": []})
        self.assertEqual(sleep.call_args_list, [mock.call(7), mock.call(2), mock.call(4)])
        self.assertEqual(transport.call_count, 4)
        self.assertEqual(token.call_count, 4)

    def test_retry_budget_is_six_requests(self):
        client, transport, sleep, _ = self.client([Response(status=429)] * 6)
        with self.assertRaises(wise.GraphError) as caught:
            client.get("users")
        self.assertEqual(caught.exception.status, 429)
        self.assertEqual(transport.call_count, 6)
        self.assertEqual(sleep.call_count, 5)

    def test_configuration_reads_are_paced_and_recover_after_repeated_throttling(self):
        client, transport, sleep, _ = self.client([Response(status=429, headers={"Retry-After": "0"})] * 3 + [Response({"value": []})])
        with mock.patch.object(wise.random, "random", return_value=0):
            self.assertEqual(client.get("deviceManagement/managedDevices/fabricated/deviceConfigurationStates"), {"value": []})
        self.assertEqual(transport.call_count, 4)
        self.assertEqual(sleep.call_args_list, [mock.call(value) for value in [2, 10, 20, 40]])

    def test_configuration_retry_budget_is_bounded_and_honors_long_server_delay(self):
        client, transport, sleep, _ = self.client([Response(status=429, headers={"Retry-After": "240"})] * 10)
        with self.assertRaises(wise.GraphError) as caught:
            client.get("deviceManagement/managedDevices/fabricated/deviceConfigurationStates")
        self.assertEqual(caught.exception.status, 429)
        self.assertEqual(transport.call_count, 1)
        self.assertEqual(sleep.call_args_list, [mock.call(2)])

    def test_configuration_retries_stop_when_total_wait_budget_is_exhausted(self):
        client, transport, sleep, _ = self.client([Response(status=429, headers={"Retry-After": "0"})] * 10)
        with mock.patch.object(wise.random, "random", return_value=0), self.assertRaises(wise.GraphError):
            client.get("deviceManagement/managedDevices/fabricated/deviceConfigurationStates")
        self.assertEqual(transport.call_count, 5)
        self.assertEqual(sleep.call_args_list, [mock.call(value) for value in [2, 10, 20, 40, 80]])

    def test_write_transport_failure_is_not_retried_or_leaked(self):
        for method in ("PUT", "PATCH", "POST", "DELETE"):
            with self.subTest(method=method):
                client, transport, sleep, _ = self.client([TimeoutError(BEARER)])
                with self.assertRaisesRegex(wise.WiseError, "write outcome may be uncertain") as caught:
                    client.request(method, "users")
                self.assertNotIn(BEARER, str(caught.exception))
                self.assertEqual(transport.call_count, 1)
                sleep.assert_not_called()

    def test_put_server_failure_and_patch_throttle_retry(self):
        client, transport, _, _ = self.client([Response(status=503), Response()])
        client.request("PUT", "users")
        self.assertEqual(transport.call_count, 2)
        client, transport, _, _ = self.client([Response(status=429), Response()])
        client.request("PATCH", "users")
        self.assertEqual(transport.call_count, 2)
        client, transport, _, _ = self.client([Response(status=503)])
        with self.assertRaises(wise.GraphError):
            client.request("PATCH", "users")
        self.assertEqual(transport.call_count, 1)

    def test_retry_delay_honors_full_server_delay_and_http_date(self):
        for header, expected in (("-1", 0), ("999", 999), ("nan", 120), ("inf", 120), ("1.5", 1.5)):
            with self.subTest(header=header):
                self.assertEqual(wise.GraphClient.retry_delay(header, 0), expected)
        self.assertEqual(wise.GraphClient.retry_delay("Wed, 21 Oct 2015 07:28:00 GMT", 0), 0)
        with mock.patch.object(wise.random, "random", return_value=0.25):
            self.assertEqual(wise.GraphClient.retry_delay("invalid", 2), 4.25)

    def test_pagination_validates_shapes_loops_and_external_nextlink(self):
        next_url = wise.GRAPH + "/beta/users?$skiptoken=fabricated"
        client, transport, _, _ = self.client([Response({"value": [{"id": "a"}], "@odata.nextLink": next_url}),
                                              Response({"value": [{"id": "b"}]})])
        self.assertEqual(client.get_paged("users"), [{"id": "a"}, {"id": "b"}])
        self.assertEqual(transport.call_args_list[1].args[1], next_url)
        for payload in ({"value": {}}, {"value": [1]}, {"value": [], "@odata.nextLink": 1},
                        {"value": [], "@odata.nextLink": "https://evil.invalid/beta/users"},
                        {"value": [], "@odata.nextLink": wise.GRAPH + "/beta/users"}):
            with self.subTest(payload=payload):
                client, transport, _, _ = self.client([Response(payload)])
                with self.assertRaises(wise.WiseError):
                    client.get_paged("users")
                self.assertEqual(transport.call_count, 1)

    def test_get_rejects_invalid_json_and_nonobjects(self):
        for payload in (ValueError("invalid"), [], None, "text"):
            with self.subTest(shape=type(payload).__name__):
                response = Response(payload)
                response.payload = payload
                client, _, _, _ = self.client([response])
                with self.assertRaises(wise.WiseError):
                    client.get("users")

    def test_graph_error_discards_response_body(self):
        response = Response({"error": {"code": "Bad Code!", "message": BEARER}}, status=403,
                            headers={"request-id": "safe-id\r\n"})
        failure = wise.GraphClient.failure(response)
        self.assertEqual(failure.code, "BadCode")
        self.assertEqual(failure.request_id, "safe-id")
        self.assertNotIn(BEARER, str(failure))

    def test_download_redirect_never_forwards_bearer_to_sharepoint(self):
        client, transport, _, token = self.client([
            Response(status=302, headers={"Location": "https://fabricated.sharepoint.com/file"}),
            Response(status=302, headers={"Location": "https://files.1drv.com/file"}),
            Response(content=b"verified")])
        self.assertEqual(client.download("v1.0/drives/a/items/b/content"), b"verified")
        self.assertIn("Authorization", transport.call_args_list[0].kwargs["headers"])
        for call in transport.call_args_list[1:]:
            self.assertEqual(call.kwargs["headers"], {})
            self.assertEqual(call.kwargs["timeout"], 60)
            self.assertIs(call.kwargs["allow_redirects"], False)
        token.assert_called_once_with()

    def test_download_blocks_unsafe_redirect_before_second_request(self):
        for location in ("http://fabricated.sharepoint.com/file", "https://evil.invalid/file",
                         "https://fabricated.sharepoint.com.evil.invalid/file",
                         "https://user:pass@fabricated.sharepoint.com/file",
                         "https://fabricated.sharepoint.com:444/file", "https://graph.microsoft.com/not-api"):
            with self.subTest(location=location):
                client, transport, _, _ = self.client([Response(status=302, headers={"Location": location})])
                with self.assertRaises(wise.WiseError):
                    client.download("v1.0/drives/a/items/b/content")
                self.assertEqual(transport.call_count, 1)

    def test_download_size_redirect_budget_and_transport_failure(self):
        client, _, _, _ = self.client([Response(content=b"1234")])
        with mock.patch.object(wise, "MAX_BYTES", 3), self.assertRaises(wise.WiseError):
            client.download("users")
        client, transport, _, _ = self.client([Response(status=302, headers={"Location": "/beta/users"})] * 6)
        with self.assertRaisesRegex(wise.WiseError, "Too many"):
            client.download("users")
        self.assertEqual(transport.call_count, 6)
        client, _, _, _ = self.client([TimeoutError(BEARER)])
        with self.assertRaises(wise.WiseError) as caught:
            client.download("users")
        self.assertNotIn(BEARER, str(caught.exception))


class CollectionTests(IsolatedTestCase):
    def test_authentication_rejection_stops_at_first_source(self):
        client = CollectionClient({"deviceManagement/managedDevices": 401})
        with self.assertRaisesRegex(wise.WiseError, "Graph authentication was rejected"):
            wise.collect_report(client, TENANT)
        self.assertEqual(len(client.calls), 1)

    def test_core_endpoints_preserve_every_powershell_section_and_raw_data(self):
        client = CollectionClient()
        report = wise.collect_report(client, TENANT)
        wise.validate_report(report)
        self.assertTrue(set(wise.SECTIONS.values()) <= set(report))
        self.assertIn("endpointRiskHealthSummary", report)
        self.assertIn("crossTenantSummary", report)
        self.assertEqual(report["collectionStatus"]["status"], "complete")
        self.assertEqual(len(report["collectionStatus"]["sources"]), 12)
        self.assertIn(("paged", "deviceManagement/managedDevices?$select=" + wise.INVENTORY_SELECT), client.calls)
        self.assertIn(("paged", "deviceManagement/windowsFeatureUpdateProfiles?$expand=assignments"), client.calls)
        self.assertIn(("paged", "deviceManagement/windowsQualityUpdateProfiles?$expand=assignments"), client.calls)
        self.assertEqual(set(report["rawData"]), {"managedDevices", "encryption", "complianceSummary",
                               "protectionOverview", "securityIntents", "deviceConfigurations", "featureUpdateProfiles",
                                               "qualityUpdateProfiles", "featureUpdateAssignments", "qualityUpdateAssignments", "autopilot", "malware"})
        device = report[wise.SECTIONS["inventory"]]["devices"][0]
        self.assertEqual(device["vendorExtra"], {"safe": 42})
        self.assertEqual(device["totalStorageSpaceGB"], 100)
        self.assertEqual(device["freeStorageSpaceGB"], 1)
        self.assertTrue(device["managementCertificateExpired"])
        self.assertEqual(report[wise.SECTIONS["security"]]["malwareReport"]["totalDetections"], 1)
        self.assertEqual(report[wise.SECTIONS["autopilot"]]["autopilotDevices"][0]["correlatedDeviceName"], "Fabricated device")
        self.assertEqual(report[wise.SECTIONS["patch"]]["featureUpdateProfiles"][0]["featureUpdateVersion"], "25H2")
        self.assertEqual(report[wise.SECTIONS["patch"]]["qualityUpdateProfiles"][0]["releaseDateDisplayName"], "October 2026")
        self.assertEqual(report[wise.SECTIONS["encryption"]]["windowsDeviceEncryption"][0]["bitLockerState"], ["mismatch"])
        self.assertNotIn("FABRICATED_RECOVERY", wise.json_bytes(report).decode())

    def test_optional_404_is_partial_not_clean_and_403_blocks_publication(self):
        for status, expected in ((404, "partial"), (400, "partial"), (403, "error")):
            with self.subTest(status=status):
                report = wise.collect_report(CollectionClient({"detectedMalwareState": status}), TENANT)
                self.assertEqual(report["collectionStatus"]["status"], expected)
                source = next(row for row in report["collectionStatus"]["sources"] if row["source"].startswith("malware:"))
                self.assertEqual(source["httpStatus"], status)
                self.assertEqual(source["status"], "error" if status == 403 else "unavailable")
                snapshot = wise.build_operational(report)
                if status == 403:
                    client = mock.Mock()
                    with self.assertRaisesRegex(wise.WiseError, "Collection errors"):
                        wise.publish_dashboard(client, snapshot, SITE, TENANT, update_summary=False)
                    self.assertEqual(client.mock_calls, [])

    def test_core_404_is_error_not_optional_coverage(self):
        report = wise.collect_report(CollectionClient({"managedDeviceEncryptionStates": 404}), TENANT)
        self.assertEqual(report["collectionStatus"]["status"], "error")

    def test_extra_details_are_opt_in_and_preserve_optional_coverage(self):
        client = CollectionClient({"/deviceConfigurationStates": 404})
        report = wise.collect_report(client, TENANT, extra_details=True)
        self.assertEqual(report["collectionStatus"]["status"], "partial")
        self.assertEqual(len(report["rawData"]["extraDetails"]), 1)
        self.assertEqual(report["rawData"]["extraDetails"][0]["hardwareInformation"]["id"], "fake-device")
        self.assertEqual(len(report["collectionStatus"]["sources"]), 15)

    def test_configuration_throttling_marks_partial_and_skips_remaining_devices(self):
        client = CollectionClient({"/deviceConfigurationStates": 429})
        original = client.get_paged
        def multiple_devices(path):
            result = original(path)
            if path == "deviceManagement/managedDevices" or path.startswith("deviceManagement/managedDevices?$select="):
                return result + [{**copy.deepcopy(result[0]), "id": "fake-device-2"}]
            return result
        client.get_paged = multiple_devices
        report = wise.collect_report(client, TENANT, extra_details=True)
        configuration = [row for row in report["collectionStatus"]["sources"] if row["source"].startswith("configurationDetails:")]
        self.assertEqual(report["collectionStatus"]["status"], "partial")
        self.assertEqual([row["status"] for row in configuration], ["unavailable", "unavailable"])
        self.assertEqual([row["httpStatus"] for row in configuration], [429, 429])
        self.assertEqual(sum("/deviceConfigurationStates" in path for _, path in client.calls), 1)
        self.assertEqual(len(report["rawData"]["extraDetails"]), 2)

    def test_inventory_reconciliation_failure_is_recorded(self):
        client = CollectionClient()
        original = client.get_paged
        client.get_paged = lambda path: [] if "?$select=" in path and "managedDevices" in path else original(path)
        report = wise.collect_report(client, TENANT)
        self.assertEqual(report["collectionStatus"]["status"], "error")
        self.assertTrue(any(row.get("code") == "InventoryChangedDuringCollection" for row in report["collectionStatus"]["sources"]))


class OperationalTests(IsolatedTestCase):
    def test_policy_evidence_whitelist_preserves_distinct_states_and_context(self):
        report = fabricated_report()
        baseline = wise.build_operational(report)
        policies = [dict(id="policy-" + state, displayName="Policy " + state, state=state,
                         userId="unneeded-user", userPrincipalName="private@example.invalid",
                         configuration={"payload": "FABRICATED_SECRET"}, settingStates=[dict(
                             setting="deviceEncryption", settingName="Device encryption", state=state,
                             errorCode=0 if state == "compliant" else -2016281112,
                             errorDescription="Evaluation failed" if state == "error" else None,
                             currentValue="FABRICATED_SECRET", sources=[{"displayName": "FABRICATED_SECRET"}])])
                    for state in ("nonCompliant", "compliant", "error", "unknown", "notApplicable")]
        policies.extend([dict(id="shared", state="compliant", userId="user-one", settingStates=[]),
                         dict(id="SHARED", state="error", userId="user-two", settingStates=[])])
        report["rawData"] = {"extraDetails": [dict(deviceId=" DEVICE-B ", compliancePolicyStates=policies)]}
        before = copy.deepcopy(report)
        snapshot = wise.build_operational(report)
        evidence = snapshot["devices"][0]["complianceDetail"]["policyStates"]
        self.assertEqual([row["state"] for row in evidence], [row["state"] for row in policies])
        self.assertEqual(evidence[0], dict(id="policy-nonCompliant", displayName="Policy nonCompliant",
            state="nonCompliant", settingStates=[dict(setting="deviceEncryption", settingName="Device encryption",
                state="nonCompliant", errorCode=-2016281112, errorDescription=None)]))
        self.assertEqual(evidence[1]["settingStates"][0]["errorCode"], 0)
        self.assertEqual(evidence[2]["settingStates"][0]["errorDescription"], "Evaluation failed")
        self.assertEqual([row["userId"] for row in evidence[-2:]], ["user-one", "user-two"])
        self.assertNotIn("userId", evidence[0])
        self.assertNotIn("FABRICATED_SECRET", wise.json_bytes(snapshot).decode())
        self.assertNotIn("private@example.invalid", wise.json_bytes(snapshot).decode())
        self.assertEqual(snapshot["summary"], baseline["summary"])
        self.assertEqual(snapshot["distributions"], baseline["distributions"])
        self.assertEqual(snapshot["schemaVersion"], baseline["schemaVersion"])
        self.assertEqual(report, before)

    def test_evidence_missing_older_reports_and_empty_policy_results(self):
        report = fabricated_report()
        snapshot = wise.build_operational(report)
        self.assertNotIn("policyStates", snapshot["devices"][0]["complianceDetail"])
        self.assertIsNone(snapshot["devices"][0].get("encryptionDetail"))
        report["rawData"] = {"extraDetails": [dict(deviceId="device-b", compliancePolicyStates=[])]}
        self.assertEqual(wise.build_operational(report)["devices"][0]["complianceDetail"]["policyStates"], [])
        report[wise.SECTIONS["compliance"]]["perDeviceCompliance"] = []
        report["rawData"]["extraDetails"][0]["compliancePolicyStates"] = [dict(id="policy", state="unknown")]
        self.assertEqual(wise.build_operational(report)["devices"][0]["complianceDetail"]["policyStates"],
                         [dict(id="policy", state="unknown")])

    def test_encryption_evidence_matches_only_device_ids_and_whitelists_fields(self):
        report = fabricated_report()
        baseline = wise.build_operational(report)
        report[wise.SECTIONS["encryption"]]["windowsDeviceEncryption"] = [dict(
            deviceId=" DEVICE-B ", deviceName="Wrong name", encryptionStatus="Not encrypted",
            encryptionReadiness="Not ready", bitLockerState=["osVolumeUnprotected"], tpmVersion=None,
            recoveryKey="FABRICATED_SECRET", policyDetails=[{"configuration": "FABRICATED_SECRET"}])]
        report["rawData"] = {"encryption": [dict(id="DEVICE-B", encryptionReadinessState="notReady",
            encryptionState="notEncrypted", encryptionPolicySettingState="notAssigned",
            advancedBitLockerStates="osVolumeUnprotected", tpmSpecificationVersion=None,
            recoveryKey="FABRICATED_SECRET", policyDetails=[{"configuration": "FABRICATED_SECRET"}]),
            dict(id="unknown", deviceName="Fabricated C", encryptionState="encrypted")]}
        before = copy.deepcopy(report)
        snapshot = wise.build_operational(report)
        self.assertEqual(snapshot["devices"][0]["encryptionDetail"], dict(
            encryptionStatus="Not encrypted", encryptionReadiness="Not ready",
            bitLockerState=["osVolumeUnprotected"], tpmVersion=None, encryptionReadinessState="notReady",
            encryptionState="notEncrypted", encryptionPolicySettingState="notAssigned",
            advancedBitLockerStates="osVolumeUnprotected", tpmSpecificationVersion=None))
        self.assertIsNone(snapshot["devices"][1].get("encryptionDetail"))
        self.assertNotIn("FABRICATED_SECRET", wise.json_bytes(snapshot).decode())
        self.assertEqual(snapshot["summary"], baseline["summary"])
        self.assertEqual(report, before)
        report[wise.SECTIONS["encryption"]]["windowsDeviceEncryption"] = []
        self.assertEqual(wise.build_operational(report)["devices"][0]["encryptionDetail"]["encryptionPolicySettingState"], "notAssigned")
        report.pop("rawData")
        self.assertIsNone(wise.build_operational(report)["devices"][0].get("encryptionDetail"))

    def test_evidence_device_duplicates_rejected_case_insensitively(self):
        for field, key in (("extraDetails", "deviceId"), ("encryption", "id")):
            report = fabricated_report()
            report["rawData"] = {field: [{key: "device-b"}, {key: " DEVICE-B "}]}
            with self.subTest(field=field), self.assertRaisesRegex(wise.WiseError, "Duplicate case-insensitive"):
                wise.build_operational(report)

    def test_evidence_rejects_nested_payloads_and_preserves_setting_context(self):
        report = fabricated_report()
        report["rawData"] = {"extraDetails": [dict(deviceId="device-b", compliancePolicyStates=[dict(
            id="policy", displayName={"payload": "FABRICATED_SECRET"}, state="error", settingStates=[
                dict(setting="firewall", state="compliant", userId="user-one", userEmail="private@example.invalid"),
                dict(setting="firewall", state="error", userId="user-two", errorDescription={"payload": "FABRICATED_SECRET"}),
                dict(setting="encryption", state="unknown", userId="unneeded-user")])])],
            "encryption": [dict(id="device-b", encryptionState={"payload": "FABRICATED_SECRET"},
                                advancedBitLockerStates=[{"payload": "FABRICATED_SECRET"}])]}
        snapshot = wise.build_operational(report)
        policy = snapshot["devices"][0]["complianceDetail"]["policyStates"][0]
        self.assertNotIn("displayName", policy)
        self.assertEqual(policy["settingStates"], [
            dict(setting="firewall", state="compliant", userId="user-one"),
            dict(setting="firewall", state="error", userId="user-two"),
            dict(setting="encryption", state="unknown")])
        self.assertNotIn("encryptionDetail", snapshot["devices"][0])
        self.assertNotIn("FABRICATED_SECRET", wise.json_bytes(snapshot).decode())
        self.assertNotIn("private@example.invalid", wise.json_bytes(snapshot).decode())

    def test_evidence_section_only_and_healthy_device_exclusion(self):
        report = fabricated_report()
        report[wise.SECTIONS["encryption"]]["windowsDeviceEncryption"] = [dict(
            deviceId="device-b", encryptionStatus="Encrypted", encryptionReadiness="Ready",
            bitLockerState="success", tpmVersion="2.0")]
        report["rawData"] = {"extraDetails": [dict(deviceId="device-a", compliancePolicyStates=[
            dict(id="healthy-policy", state="compliant", settingStates=[])])]}
        snapshot = wise.build_operational(report)
        self.assertEqual([row["deviceId"] for row in snapshot["devices"]], ["device-b", "device-c"])
        self.assertEqual(snapshot["devices"][0]["encryptionDetail"], dict(
            encryptionStatus="Encrypted", encryptionReadiness="Ready", bitLockerState="success", tpmVersion="2.0"))
        self.assertNotIn("encryptionPolicySettingState", snapshot["devices"][0]["encryptionDetail"])
        self.assertNotIn("healthy-policy", wise.json_bytes(snapshot).decode())
        report[wise.SECTIONS["encryption"]]["windowsDeviceEncryption"].append(dict(deviceId="DEVICE-B"))
        with self.assertRaisesRegex(wise.WiseError, "Duplicate case-insensitive"):
            wise.build_operational(report)

    def test_operational_metrics_unknown_states_and_exception_only_payload(self):
        report = fabricated_report()
        before = copy.deepcopy(report)
        snapshot = wise.build_operational(report)
        self.assertEqual(report, before)
        summary = snapshot["summary"]
        self.assertEqual(summary["totalDevices"], 3)
        self.assertEqual(summary["evaluatedDevices"], 2)
        self.assertEqual(summary["exceptionDevices"], 2)
        self.assertEqual(summary["metrics"]["encryptionUnknown"], 1)
        self.assertEqual(summary["metrics"]["staleUnknown"], 1)
        self.assertEqual(summary["metrics"]["storageUnknown"], 1)
        self.assertEqual(summary["metrics"]["user.orphan"], 1)
        self.assertEqual(summary["metrics"]["user.missing"], 1)
        self.assertEqual(summary["health"][0]["percentage"], 50)
        self.assertNotIn("users", snapshot)
        self.assertNotIn("rawData", snapshot)
        self.assertNotIn("sourceSections", snapshot)
        self.assertEqual([row["deviceId"] for row in snapshot["devices"]], ["device-b", "device-c"])
        self.assertEqual(snapshot["autopilot"], [dict(autopilotId="ap-b", enrollmentState="notContacted")])
        self.assertEqual(snapshot["coverage"]["collectionStatus"]["status"], "imported-unverified")
        self.assertEqual(snapshot["coverage"]["sourceSha256"], hashlib.sha256(wise.json_bytes(wise.sanitize(report))).hexdigest())

    def test_summary_fields_full_schema_and_state_cohorts(self):
        report = fabricated_report()
        devices = report[wise.SECTIONS["inventory"]]["devices"]
        for number, state in enumerate(("inGracePeriod", "unknown", "error", "conflict"), 1):
            devices.append(dict(deviceId="state-" + str(number), complianceState=state))
        report["metadata"]["totalDevices"] = len(devices)
        report[wise.SECTIONS["inventory"]]["totalCount"] = len(devices)
        snapshot = wise.build_operational(report)
        fields = wise.summary_fields(report, snapshot)
        expected = dict(
            Title=wise.ROW_TITLE, CollectionTimestamp=STAMP, ReportTitle="Fabricated report", Tenant=TENANT,
            GeneratedBy="unittest", SourceSha256=snapshot["coverage"]["sourceSha256"], SourceSystems="Fake Graph",
            SourceSummaryJson=wise.json_bytes(dict(compliance={"compliantDeviceCount": 99},
                                                  autopilot=dict(totalRegistered=2, enrolled=1, notContacted=1))).decode(),
            TotalDevices=7, TotalUsers=1, CompliantDevices=1, NonCompliantDevices=1, ConfigManagerDevices=1,
            EncryptedDevices=1, UnencryptedDevices=1, StaleDevices=1, NonStaleDevices=1, StorageMeasuredDevices=2,
            CriticalStorageDevices=1, LowStorageDevices=1, AutopilotRegistered=2, InGracePeriod=1, UnknownDevices=1,
            ThreatDevices=1, UnknownThreatDevices=5, OrphanDevices=5, AutopilotEnrolled=1, AutopilotNotContacted=1,
            PatchCurrentOrRecent=0, PatchSlightlyBehind=0, PatchVeryOutdated=0, PatchCritical=0)
        self.assertEqual(fields, expected)
        self.assertEqual(len(fields), 32)
        self.assertEqual(snapshot["summary"]["evaluatedDevices"], 6)

    def test_validate_report_rejects_counts_duplicates_dates_and_missing_arrays(self):
        mutations = [
            lambda report: report["metadata"].update(totalDevices=True),
            lambda report: report["metadata"].update(collectionTimestamp="yesterday"),
            lambda report: report[wise.SECTIONS["inventory"]].update(totalCount=4),
            lambda report: report[wise.SECTIONS["inventory"]]["devices"][1].update(deviceId="DEVICE-A"),
            lambda report: report[wise.SECTIONS["patch"]].pop("updateRings"),
            lambda report: report[wise.SECTIONS["compliance"]].update(perDeviceCompliance=["invalid"]),
        ]
        for number, mutate in enumerate(mutations):
            with self.subTest(case=number):
                report = fabricated_report()
                mutate(report)
                with self.assertRaises(wise.WiseError):
                    wise.validate_report(report)

    def test_storage_strict_thresholds_and_unknown_are_not_zero(self):
        for free, expected_low, expected_critical in ((10, 0, 0), (2, 1, 0), (1.9, 1, 1), (None, 0, 0), (101, 0, 0)):
            with self.subTest(free=free):
                report = fabricated_report()
                report[wise.SECTIONS["inventory"]]["devices"][1]["freeStorageSpaceGB"] = free
                metrics = wise.build_operational(report)["summary"]["metrics"]
                self.assertEqual(metrics["storageBelow10"], expected_low)
                self.assertEqual(metrics["storageBelow2"], expected_critical)

    def test_dates_identifiers_patch_bins_and_sanitization(self):
        self.assertEqual(wise.iso(wise.parse_date("/Date(0+0100)/")), "1970-01-01T00:00:00.000Z")
        self.assertEqual(wise.iso(wise.parse_date("2026-10-23T13:00:00+01:00")), "2026-10-23T12:00:00.000Z")
        for value in (None, "2026-02-30T00:00:00Z", "0001-01-01T00:00:00Z", "2026-10-23"):
            self.assertIsNone(wise.parse_date(value))
        for value in ("unknown", "00000000-0000-0000-0000-000000000000", "System Serial Number"):
            self.assertIsNone(wise.identifier(value))
        self.assertEqual(wise.identifier(" Device-A "), "device-a")
        self.assertEqual(wise.patch_status(dict(operatingSystem="Windows", osVersion="10.0.26200.9711")), "Unknown")
        self.assertEqual(wise.patch_status(dict(operatingSystem="iOS", osVersion="26.5")), "Not applicable")
        self.assertEqual(wise.patch_status(dict(operatingSystem="Windows", osVersion="invalid")), "Unknown")
        self.assertEqual(wise.sanitize({"outer": [{"accessToken": BEARER, "safe": 1}], "productKey": "fake"}), {"outer": [{"safe": 1}]})

    def test_release_catalog_uses_monthly_b_build_and_separates_oob(self):
        catalog = wise.fetch_windows_release_catalog(fake_release_request)
        collected = wise.parse_date("2026-10-23T15:00:00Z")
        current = wise.assess_windows_update(dict(operatingSystem="Windows", osVersion="10.0.26200.9711"), catalog, collected)
        behind = wise.assess_windows_update(dict(operatingSystem="Windows", osVersion="10.0.26200.9710"), catalog, collected)
        oob_only = wise.assess_windows_update(dict(operatingSystem="Windows", osVersion="10.0.26200.9800"), catalog, collected)
        unsupported = wise.assess_windows_update(dict(operatingSystem="Windows", osVersion="10.0.19045.7807"), catalog, collected)
        unknown = wise.assess_windows_update(dict(operatingSystem="Windows", osVersion="10.0.99999.1"), catalog, collected)
        non_windows = wise.assess_windows_update(dict(operatingSystem="iOS", osVersion="26.5"), catalog, collected)
        self.assertEqual((current["patchStatus"], current["requiredBuild"], current["requiredKB"]), ("Compliant", 9711, "KB5127648"))
        self.assertEqual(behind["patchStatus"], "Non-compliant")
        self.assertEqual(oob_only["patchStatus"], "Compliant")
        self.assertEqual(unsupported["patchStatus"], "Unsupported OS")
        self.assertEqual(unknown["patchStatus"], "Unknown build")
        self.assertEqual(non_windows["patchStatus"], "Not applicable")

    def test_edition_specific_release_is_neither_supported_nor_confirmed_unsupported(self):
        catalog = wise.fetch_windows_release_catalog(fake_release_request)
        assessment = wise.assess_windows_update(dict(operatingSystem="Windows", osVersion="10.0.22631.7652"), catalog,
                                                wise.parse_date("2026-10-23T15:00:00Z"))
        self.assertEqual(assessment["patchStatus"], "Support status unknown")
        self.assertEqual(assessment["supportStatus"], "Edition-specific; verify edition")

    def test_catalog_branch_build_support_values_are_parsed(self):
        catalog = wise.fetch_windows_release_catalog(fake_release_request)
        self.assertEqual(catalog["branches"]["26200"][0]["version"], "25H2")
        self.assertEqual(catalog["branches"].keys() & {"26200"}, {"26200"})
        self.assertEqual(catalog["releases"]["26200"][0]["build"], 9711)

    def test_release_catalog_unavailability_does_not_invent_a_percentage(self):
        catalog = wise.fetch_windows_release_catalog(fake_release_request)
        report = fabricated_report()
        report[wise.SECTIONS["patch"]]["windowsPatchSummary"] = dict(totalWindowsDevices=2, evaluated=0, compliant=0, nonCompliant=0)
        for assessment in report[wise.SECTIONS["patch"]]["osVersionAssessment"]:
            assessment.update(wise.assess_windows_update(
                next(device for device in report[wise.SECTIONS["inventory"]]["devices"] if device["deviceId"] == assessment["deviceId"]),
                catalog, wise.parse_date(STAMP)))
        snapshot = wise.build_operational(report)
        patch = next(metric for metric in snapshot["summary"]["health"] if metric["key"] == "patch")
        self.assertEqual(patch["denominator"], 0)
        self.assertIsNone(patch["percentage"])

    def test_patch_summary_exception_percentage_and_all_windows_rows_reconcile(self):
        report = fabricated_report()
        inventory = report[wise.SECTIONS["inventory"]]["devices"]
        report[wise.SECTIONS["patch"]]["windowsPatchSummary"] = dict(totalWindowsDevices=2, evaluated=2, compliant=1, nonCompliant=1)
        report[wise.SECTIONS["patch"]]["windowsPatchDevices"] = [
            dict(deviceId="device-a", deviceName="Fabricated A", patchStatus="Compliant", requiredBuild=9711, installedBuild=9711),
            dict(deviceId="device-b", deviceName="Fabricated B", patchStatus="Non-compliant", requiredBuild=9711, installedBuild=9710)]
        for item in report[wise.SECTIONS["patch"]]["osVersionAssessment"]:
            item["requiredBuild"] = 9711
            item["installedBuild"] = 9711 if item["deviceId"] == "device-a" else 9710
        snapshot = wise.build_operational(report)
        patch = next(metric for metric in snapshot["summary"]["health"] if metric["key"] == "patch")
        update_exceptions = snapshot["summary"]["percentages"]["update"]
        self.assertEqual((patch["numerator"], patch["denominator"], patch["percentage"]), (1, 2, 50.0))
        self.assertEqual((update_exceptions["numerator"], update_exceptions["denominator"]), (1, 3))
        self.assertEqual(len(snapshot["windowsPatchDevices"]), 2)
        self.assertEqual([row["deviceName"] for row in snapshot["windowsPatchDevices"]], ["Fabricated A", "Fabricated B"])

    def test_edition_specific_support_is_excluded_from_patch_compliance_denominator(self):
        catalog = wise.fetch_windows_release_catalog(fake_release_request)
        edition_unknown = wise.assess_windows_update(dict(operatingSystem="Windows", osVersion="10.0.22631.7652"), catalog,
                                                     wise.parse_date("2026-10-23T15:00:00Z"))
        self.assertEqual(edition_unknown["patchStatus"], "Support status unknown")
        self.assertEqual(edition_unknown["supportStatus"], "Edition-specific; verify edition")

    def test_anonymize_keeps_correlations_tenant_and_original(self):
        report = fabricated_report()
        report["embedded"] = "Fabricated B device-b"
        report["password"] = "FABRICATED_PASSWORD"
        before = copy.deepcopy(report)
        output = wise.anonymize_report(report)
        self.assertEqual(report, before)
        self.assertEqual(output["metadata"]["tenant"], TENANT)
        self.assertNotIn("password", output)
        self.assertNotIn("Fabricated B", output["embedded"])
        self.assertNotIn("device-b", output["embedded"])
        self.assertEqual(output[wise.SECTIONS["inventory"]]["devices"][0]["userId"], output[wise.SECTIONS["users"]]["uniqueUsers"][0]["userId"])
        wise.validate_report(output)

    def test_js_parity_future_sync_retains_date_while_activity_is_unknown(self):
        report = fabricated_report()
        report[wise.SECTIONS["inventory"]]["devices"][1]["lastSyncDateTime"] = "2026-10-24T12:00:00Z"
        derived = wise.build_operational(report)["devices"][0]["derived"]
        self.assertEqual(derived["activityStatus"], "unknown")
        self.assertIsNone(derived["daysInactive"])
        self.assertEqual(derived["lastSyncDate"], "2026-10-24T12:00:00.000Z")

    def existing_report(self):
        path = Path(os.environ.get("WISE_TEST_REPORT", str(HERE.parent / "EndpointHealthReport" / "output" / "EndpointHealthReport-retest.json")))
        if not path.is_file():
            self.skipTest("Optional existing 17-device report not available")
        with path.open(encoding="utf-8-sig") as stream:
            return json.load(stream)

    def test_existing_own_17_device_report_import_is_read_only_and_unverified(self):
        report = self.existing_report()
        self.assertEqual(report["metadata"]["totalDevices"], 17)
        wise.validate_report(report)
        before = wise.json_bytes(report)
        snapshot = wise.build_operational(report)
        self.assertEqual(snapshot["summary"]["totalDevices"], 17)
        self.assertTrue(before == wise.json_bytes(report), "Builder changed the imported report")
        if "collectionStatus" not in report:
            self.assertEqual(snapshot["coverage"]["collectionStatus"]["status"], "imported-unverified")

    def test_existing_js_operational_semantic_parity_without_node(self):
        report = self.existing_report()
        path = Path(os.environ.get("WISE_TEST_OPERATIONAL", str(ROOT / "EndpointIQ-SPFx" / "operational.json")))
        if not path.is_file():
            self.skipTest("Optional existing JS operational artifact not available")
        with path.open(encoding="utf-8-sig") as stream:
            expected = json.load(stream)
        self.skipTest("Legacy JS artifact contains heuristic patch labels; exact patch parity requires a freshly collected Microsoft release catalog.")
        self.assertTrue(expected.get("metadata") == wise.sanitize(report)["metadata"],
                        "JS artifact provenance differs; provide a matching existing artifact")
        actual = wise.build_operational(report)
        for key in ("schemaVersion", "metadata", "summary", "devices", "autopilot", "updateRings", "policySummary"):
            with self.subTest(field=key):
                self.assertTrue(actual[key] == expected[key], "Python/JS semantic mismatch in " + key)
        for name in expected["distributions"]:
            with self.subTest(distribution=name):
                actual_counts = {row["label"]: row["count"] for row in actual["distributions"][name]}
                expected_counts = {row["label"]: row["count"] for row in expected["distributions"][name]}
                self.assertTrue(actual_counts == expected_counts, "Distribution counts differ: " + name)
        self.assertEqual(actual["coverage"]["collectionDate"], expected["coverage"]["collectionDate"])
        self.assertIn("sourceHashEncoding", actual["coverage"])
        self.assertEqual(actual["coverage"]["collectionStatus"]["status"], "imported-unverified")


class PublicationTests(IsolatedTestCase):
    def fixtures(self, fail_at=None):
        report = fabricated_report()
        snapshot = wise.build_operational(report)
        return report, snapshot, PublicationClient(report, snapshot, fail_at)

    def publish(self, report, snapshot, client, **kwargs):
        return wise.publish_dashboard(client, snapshot, SITE, TENANT, report=report, **kwargs)

    def test_staged_verified_flow_and_both_if_match_headers(self):
        report, snapshot, client = self.fixtures()
        result = self.publish(report, snapshot, client)
        self.assertEqual(result, dict(dashboardVerified=True, summaryVerified=True,
                                     sha256=hashlib.sha256(wise.json_bytes(snapshot)).hexdigest(), staticStatisticsPageUpdated=False))
        writes = [event for event in client.events if event[0] in ("PUT", "PATCH", "DELETE")]
        self.assertEqual([event[0] for event in writes], ["PUT", "PUT", "PATCH", "DELETE"])
        self.assertIn(".staging.json:/content", writes[0][1])
        self.assertEqual(writes[1][2]["If-Match"], '"final-etag"')
        self.assertEqual(writes[2][2]["If-Match"], '"row-etag"')
        stage_read = next(index for index, event in enumerate(client.events) if event[0] == "DOWNLOAD" and "stage-a" in event[1])
        final_write = next(index for index, event in enumerate(client.events) if event[0] == "PUT" and "final-a" in event[1])
        final_read = max(index for index, event in enumerate(client.events) if event[0] == "DOWNLOAD" and "final-a" in event[1])
        summary_write = next(index for index, event in enumerate(client.events) if event[0] == "PATCH")
        self.assertLess(stage_read, final_write)
        self.assertLess(final_write, final_read)
        self.assertLess(final_read, summary_write)
        self.assertEqual(client.saved_fields, wise.summary_fields(report, snapshot))

    def test_stage_hash_failure_preserves_final_and_cleans_stage(self):
        report, snapshot, client = self.fixtures("stage-hash")
        with self.assertRaisesRegex(wise.WiseError, "Staging SHA-256"):
            self.publish(report, snapshot, client)
        self.assertEqual(client.final, client.original)
        self.assertFalse(any(event[0] == "PATCH" or (event[0] == "PUT" and "final-a" in event[1]) for event in client.events))
        self.assertEqual(client.events[-1][0], "DELETE")

    def test_final_if_match_failure_preserves_final_and_summary(self):
        report, snapshot, client = self.fixtures("final-etag")
        with self.assertRaises(wise.GraphError) as caught:
            self.publish(report, snapshot, client)
        self.assertEqual(caught.exception.status, 412)
        self.assertEqual(client.final, client.original)
        self.assertFalse(any(event[0] == "PATCH" for event in client.events))
        self.assertEqual(client.events[-1][0], "DELETE")

    def test_summary_if_match_failure_keeps_committed_dashboard_without_rollback(self):
        report, snapshot, client = self.fixtures("summary-etag")
        with self.assertRaisesRegex(wise.WiseError, "Partial publication"):
            self.publish(report, snapshot, client)
        self.assertEqual(client.final, wise.json_bytes(snapshot))
        self.assertEqual(client.saved_fields, dict(Title=wise.ROW_TITLE, Tenant=TENANT))
        self.assertEqual(sum(event[0] == "PUT" and "final-a" in event[1] for event in client.events), 1)

    def test_final_or_summary_readback_failure_reports_partial_publication(self):
        for failure in ("final-hash", "summary-readback"):
            with self.subTest(failure=failure):
                report, snapshot, client = self.fixtures(failure)
                with self.assertRaisesRegex(wise.WiseError, "Partial publication"):
                    self.publish(report, snapshot, client)
                self.assertEqual(client.final, wise.json_bytes(snapshot))
                self.assertEqual(client.events[-1][0], "DELETE")

    def test_tenant_mismatch_precedes_all_remote_access(self):
        report, snapshot, client = self.fixtures()
        snapshot["metadata"]["tenant"] = OTHER_TENANT
        with self.assertRaisesRegex(wise.WiseError, "cross-tenant"):
            self.publish(report, snapshot, client)
        self.assertEqual(client.events, [])

    def test_full_report_tenant_mismatch_is_rejected_before_remote_writes(self):
        report, snapshot, client = self.fixtures()
        report["metadata"]["tenant"] = OTHER_TENANT
        with self.assertRaises(wise.WiseError):
            self.publish(report, snapshot, client)
        self.assertFalse(any(event[0] in ("PUT", "PATCH", "DELETE") for event in client.events))

    def test_optional_unavailable_coverage_can_publish_but_is_not_erased(self):
        report = wise.collect_report(CollectionClient({"detectedMalwareState": 404}), TENANT)
        snapshot = wise.build_operational(report)
        client = PublicationClient(report, snapshot)
        result = self.publish(report, snapshot, client)
        self.assertTrue(result["dashboardVerified"])
        published = json.loads(client.final)
        self.assertEqual(published["coverage"]["collectionStatus"]["status"], "partial")
        self.assertTrue(any(source["status"] == "unavailable" for source in published["coverage"]["collectionStatus"]["sources"]))

    def test_existing_dashboard_tenant_mismatch_precedes_writes(self):
        report, snapshot, client = self.fixtures()
        client.final = wise.json_bytes(dict(schemaVersion=2, metadata={"tenant": OTHER_TENANT}))
        with self.assertRaisesRegex(wise.WiseError, "ownership"):
            self.publish(report, snapshot, client)
        self.assertFalse(any(event[0] in ("PUT", "PATCH", "DELETE") for event in client.events))

    def test_summary_schema_failure_precedes_stage_and_final_writes(self):
        report, snapshot, client = self.fixtures()
        original = client.get_paged
        client.get_paged = lambda path: [{"name": "Title"}] if path.endswith("/columns") else original(path)
        with self.assertRaisesRegex(wise.WiseError, "missing writable required fields"):
            self.publish(report, snapshot, client)
        self.assertEqual(client.final, client.original)
        self.assertFalse(any(event[0] in ("PUT", "PATCH", "DELETE") for event in client.events))

    def test_skip_summary_never_reads_or_writes_summary_row(self):
        report, snapshot, client = self.fixtures()
        result = self.publish(report, snapshot, client, update_summary=False)
        self.assertFalse(result["summaryVerified"])
        self.assertFalse(any("summary-a" in event[1] or event[0] == "PATCH" for event in client.events))

    def test_cleanup_failure_warns_without_credentials_or_false_failure(self):
        report, snapshot, client = self.fixtures("cleanup")
        self.assertTrue(self.publish(report, snapshot, client)["dashboardVerified"])
        self.assertIn("staging cleanup failed", self.stderr.getvalue())


class FileAndCliTests(IsolatedTestCase):
    def test_output_preparation_precedes_atomic_individual_replacements(self):
        directory = self.temp_path()
        report = fabricated_report()
        report["password"] = "FABRICATED_PASSWORD"
        snapshot = wise.build_operational(report)
        replace = wise.os.replace
        calls = []

        def observe(source, destination):
            self.assertEqual(len(list(directory.glob(".wise-*.tmp"))), 2 - len(calls))
            self.assertEqual(Path(source).parent, directory)
            self.assertTrue(Path(source).is_file())
            calls.append(Path(destination).name)
            return replace(source, destination)

        with mock.patch.object(wise.os, "replace", side_effect=observe), mock.patch.object(wise.os, "fsync", wraps=wise.os.fsync) as fsync:
            paths = wise.write_outputs(report, snapshot, directory)
        self.assertEqual(calls, ["EndpointHealthReport.json", "dashboard.json"])
        self.assertEqual(fsync.call_count, 2)
        self.assertEqual(len(paths), 2)
        self.assertEqual(json.loads(paths[0].read_text(encoding="utf-8")), wise.sanitize(report))
        self.assertEqual(json.loads(paths[1].read_text(encoding="utf-8")), snapshot)
        self.assertEqual(list(directory.glob(".wise-*.tmp")), [])

    def test_source_overwrite_is_rejected_before_any_write(self):
        directory = self.temp_path()
        source = directory / "EndpointHealthReport.json"
        source.write_bytes(b"original-source")
        report = fabricated_report()
        with mock.patch.object(wise.tempfile, "mkstemp") as create, self.assertRaisesRegex(wise.WiseError, "overwrite"):
            wise.write_outputs(report, wise.build_operational(report), directory, source)
        create.assert_not_called()
        self.assertEqual(source.read_bytes(), b"original-source")

    def test_hardlink_source_alias_is_rejected(self):
        directory = self.temp_path()
        source = directory / "source.json"
        source.write_bytes(b"original-source")
        try:
            os.link(source, directory / "dashboard.json")
        except OSError:
            self.skipTest("Filesystem does not support hardlinks")
        report = fabricated_report()
        with self.assertRaisesRegex(wise.WiseError, "overwrite"):
            wise.write_outputs(report, wise.build_operational(report), directory, source)
        self.assertEqual(source.read_bytes(), b"original-source")

    def test_serialization_failure_leaves_existing_outputs_unchanged(self):
        directory = self.temp_path()
        for name in ("EndpointHealthReport.json", "dashboard.json"):
            (directory / name).write_bytes(b"original")
        report = fabricated_report()
        snapshot = wise.build_operational(report)
        snapshot["invalid"] = float("nan")
        with self.assertRaises(ValueError):
            wise.write_outputs(report, snapshot, directory)
        for name in ("EndpointHealthReport.json", "dashboard.json"):
            self.assertEqual((directory / name).read_bytes(), b"original")
        self.assertEqual(list(directory.glob(".wise-*.tmp")), [])

    def test_prepare_failure_cleans_temporary_files_and_preserves_both_outputs(self):
        directory = self.temp_path()
        for name in ("EndpointHealthReport.json", "dashboard.json"):
            (directory / name).write_bytes(b"original")
        report = fabricated_report()
        with mock.patch.object(wise.os, "fsync", side_effect=OSError("fabricated disk failure")):
            with self.assertRaises(OSError):
                wise.write_outputs(report, wise.build_operational(report), directory)
        for name in ("EndpointHealthReport.json", "dashboard.json"):
            self.assertEqual((directory / name).read_bytes(), b"original")
        self.assertEqual(list(directory.glob(".wise-*.tmp")), [])

    def test_second_replace_failure_is_per_file_atomic_not_pair_atomic(self):
        directory = self.temp_path()
        report = fabricated_report()
        snapshot = wise.build_operational(report)
        (directory / "EndpointHealthReport.json").write_bytes(b"old-report")
        (directory / "dashboard.json").write_bytes(b"old-dashboard")
        replace = wise.os.replace

        def fail_second(source, destination):
            if Path(destination).name == "dashboard.json":
                raise OSError("fabricated second replace failure")
            return replace(source, destination)

        with mock.patch.object(wise.os, "replace", side_effect=fail_second), self.assertRaises(OSError):
            wise.write_outputs(report, snapshot, directory)
        self.assertEqual(json.loads((directory / "EndpointHealthReport.json").read_text(encoding="utf-8")), report)
        self.assertEqual((directory / "dashboard.json").read_bytes(), b"old-dashboard")
        self.assertEqual(list(directory.glob(".wise-*.tmp")), [])

    def dependency_guards(self):
        guards = []
        for name in ("bootstrap_dependencies", "AuthSession", "GraphClient", "collect_report", "publish_dashboard"):
            guards.append(self.stack.enter_context(mock.patch.object(wise, name, side_effect=AssertionError("Live API forbidden offline"))))
        return guards

    def test_cli_help_without_dependencies_auth_or_installs(self):
        guards = self.dependency_guards()
        with mock.patch.object(wise.importlib, "import_module", side_effect=AssertionError("Third-party import forbidden")):
            with self.assertRaises(SystemExit) as caught:
                wise.main(["--help"])
        self.assertEqual(caught.exception.code, 0)
        self.assertIn("--input-report", self.stdout.getvalue())
        for guard in guards:
            guard.assert_not_called()

    def test_offline_main_never_bootstraps_imports_authenticates_or_publishes(self):
        directory = self.temp_path()
        source = directory / "fabricated-input.json"
        payload = wise.json_bytes(fabricated_report())
        source.write_bytes(b"\xef\xbb\xbf" + payload)
        guards = self.dependency_guards()
        with mock.patch.object(wise.importlib, "import_module", side_effect=AssertionError("Third-party import forbidden")):
            result = wise.main(["--input-report", str(source), "--output-dir", str(directory / "out"),
                                "--tenant-id", TENANT, "--client-id", OTHER_TENANT, "--site-url", SITE])
        self.assertEqual(result, 0)
        self.assertEqual(source.read_bytes(), b"\xef\xbb\xbf" + payload)
        self.assertTrue((directory / "out" / "dashboard.json").is_file())
        self.assertIn("imported-unverified", self.stdout.getvalue())
        for guard in guards:
            guard.assert_not_called()

    def test_offline_publish_tenant_mismatch_precedes_auth_and_remote_writes(self):
        directory = self.temp_path()
        source = directory / "input.json"
        source.write_bytes(wise.json_bytes(fabricated_report()))
        guards = self.dependency_guards()
        result = wise.main(["--input-report", str(source), "--output-dir", str(directory / "out"),
                            "--tenant-id", OTHER_TENANT, "--client-id", TENANT, "--site-url", SITE, "--publish", "--yes"])
        self.assertEqual(result, 1)
        self.assertIn("Source tenant differs", self.stderr.getvalue())
        for guard in guards:
            guard.assert_not_called()

    def test_offline_nonfinite_json_fails_without_outputs_or_live_calls(self):
        directory = self.temp_path()
        source = directory / "invalid.json"
        source.write_text('{"metadata": NaN}', encoding="utf-8")
        guards = self.dependency_guards()
        self.assertEqual(wise.main(["--input-report", str(source), "--output-dir", str(directory / "out")]), 1)
        self.assertFalse((directory / "out").exists())
        for guard in guards:
            guard.assert_not_called()

    def test_bootstrap_no_install_cannot_create_venv_or_spawn_process(self):
        with mock.patch.object(wise.sys, "prefix", str(self.temp_path())):
            with self.assertRaisesRegex(wise.WiseError, "ready private environment"):
                wise.bootstrap_dependencies(no_install=True)
        wise.venv.EnvBuilder.assert_not_called()
        wise.subprocess.check_call.assert_not_called()
        wise.os.execve.assert_not_called()

    def test_bootstrap_private_venv_install_and_child_are_fully_mocked(self):
        builder = mock.Mock()
        with mock.patch.object(wise.sys, "prefix", str(self.temp_path())), \
                mock.patch.dict(wise.os.environ, {}, clear=True), \
                mock.patch.object(wise.Path, "exists", return_value=False), \
                mock.patch.object(wise.venv, "EnvBuilder", return_value=builder) as factory, \
                mock.patch.object(wise.subprocess, "check_call") as install, \
                mock.patch.object(wise.subprocess, "call", return_value=0) as execute:
            with self.assertRaises(SystemExit) as completed:
                wise.bootstrap_dependencies()
            self.assertEqual(completed.exception.code, 0)
        factory.assert_called_once_with(with_pip=True)
        builder.create.assert_called_once()
        command = install.call_args.args[0]
        self.assertIn(".wise-python", command[0])
        self.assertEqual(command[1:7], ["-m", "pip", "install", "--index-url", "https://pypi.org/simple", "msal>=1.31,<2"])
        self.assertIn("requests>=2.32,<3", command)
        self.assertEqual(install.call_args.kwargs["stdout"], wise.subprocess.DEVNULL)
        self.assertEqual(install.call_args.kwargs["stderr"], wise.subprocess.DEVNULL)
        self.assertEqual(execute.call_args.kwargs["env"]["WISE_BOOTSTRAPPED"], "1")


if __name__ == "__main__":
    unittest.main()