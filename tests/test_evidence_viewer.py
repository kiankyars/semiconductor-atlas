"""Real loopback HTTP checks, not browser or visual QA."""

import hashlib
import http.client
import json
import queue
import threading
import unittest
from importlib.resources import files
from unittest.mock import patch
from urllib.parse import urlencode

from semiconductor_atlas import models as m, repository as r
from semiconductor_atlas.cli import build_parser
from semiconductor_atlas.evidence_dossier import json_bytes
from semiconductor_atlas.evidence_search import search_evidence
from semiconductor_atlas.evidence_viewer import CSP, open_viewer
from tests import test_evidence_dossier as fixtures


class EvidenceViewerTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.EvidenceDossierTests("runTest")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.claim("source", valid_from="2028-01-01")
        self.fixture.claim("observed", kind=m.ClaimKind.DIRECT_OBSERVATION, valid_from="2026-01-01")
        self.fixture.c.commit()
        self.fixture.c.execute("PRAGMA journal_mode=WAL")
        self.servers = []
        self.addCleanup(self.stop)
        self.start()

    def start(self):
        ready = queue.Queue()
        def run():
            try:
                with open_viewer(self.fixture.path, as_of=fixtures.AS_OF,
                                 recorded_at=fixtures.ADMITTED) as server:
                    ready.put(server)
                    server.serve_forever(poll_interval=.01)
            except BaseException as error:
                ready.put(error)
        worker = threading.Thread(target=run, daemon=True)
        worker.start()
        server = ready.get(timeout=5)
        if isinstance(server, BaseException):
            raise server
        self.server = server
        self.servers.append((server, worker))

    def stop(self):
        for server, worker in self.servers:
            server.shutdown()
            worker.join(timeout=5)
            self.assertFalse(worker.is_alive())
        self.servers.clear()

    def request(self, route="", params=None, *, method="GET", headers=None, raw_path=None):
        client = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        try:
            target = raw_path if raw_path is not None else self.server.prefix + route
            if params is not None:
                target += "?" + urlencode(params)
            client.request(method, target, headers=headers or {})
            response = client.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            client.close()

    def api(self, route="api/search", **params):
        return self.request(route, {"as_of": fixtures.AS_OF, "recorded_at": fixtures.ADMITTED, **params})

    def test_assets_are_packaged_self_contained_and_content_typed(self):
        for route, name, mime in (("", "index.html", "text/html"), ("viewer.js", "viewer.js", "text/javascript"),
                                  ("viewer.css", "viewer.css", "text/css")):
            with self.subTest(route=route):
                status, headers, raw = self.request(route)
                self.assertEqual(status, 200)
                self.assertTrue(headers["Content-Type"].startswith(mime))
                self.assertEqual(raw, files("semiconductor_atlas").joinpath("viewer", name).read_bytes())
                self.assertEqual(headers["Content-Length"], str(len(raw)))

    def test_config_preserves_microseconds_and_discloses_fixed_snapshot(self):
        status, _, raw = self.request("api/config")
        self.assertEqual(status, 200)
        config = json.loads(raw)
        self.assertEqual(config["recorded_at"], fixtures.ADMITTED)
        self.assertIn("restart", config["snapshot"])
        self.assertEqual(set(config["entity_kinds"]), {k.value for k in m.EntityKind})
        self.assertNotIn(str(self.fixture.path), raw.decode())

    def test_every_response_has_no_store_no_referrer_and_csp_without_cors(self):
        for path in ("", "api/config", "missing"):
            _, headers, _ = self.request(path)
            self.assertEqual(headers["Cache-Control"], "no-store")
            self.assertEqual(headers["Referrer-Policy"], "no-referrer")
            self.assertEqual(headers["Content-Security-Policy"], CSP)
            self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
            self.assertEqual(headers["X-Frame-Options"], "DENY")
            self.assertNotIn("Access-Control-Allow-Origin", headers)

    def test_search_response_matches_the_direct_api_exactly(self):
        status, _, raw = self.api(query="Synthetic publisher")
        expected = search_evidence(self.fixture.c, as_of=fixtures.AS_OF,
                                   recorded_at=fixtures.ADMITTED, query="Synthetic publisher")
        self.assertEqual(status, 200)
        self.assertEqual(raw, json_bytes(expected))
        self.assertEqual(expected["results"][0]["known_source_statement_count"], 1)
        self.assertEqual(expected["results"][0]["world_current_claim_count"], 1)

    def test_dossier_is_exact_api_bytes_not_source_jsonl_or_rehydrated_claims(self):
        status, _, raw = self.api("api/dossier", entity_id="project")
        self.assertEqual(status, 200)
        self.assertEqual(raw, json_bytes(self.fixture.report()))
        self.assertNotIn(b"SECRET", raw)
        self.assertEqual(json.loads(raw)["source_statements"][0]["confidence"], None)

    def test_each_request_applies_its_own_explicit_cutoffs(self):
        _, _, raw = self.api(recorded_at=fixtures.JUST_BEFORE)
        self.assertTrue(all(e["known_source_statement_count"] == 0 for e in json.loads(raw)["results"]))
        _, _, raw = self.api("api/dossier", entity_id="project", recorded_at=fixtures.JUST_BEFORE)
        report = json.loads(raw)
        self.assertEqual(report["source_statements"], [])
        self.assertEqual(report["world_current_claims"], [])
        _, _, raw = self.api("api/dossier", entity_id="project", as_of="2020-01-01")
        report = json.loads(raw)
        self.assertEqual(len(report["source_statements"]), 1)
        self.assertEqual(report["world_current_claims"], [])

    def test_search_dossier_selection_retains_server_normalized_clocks(self):
        _, _, raw = self.api(recorded_at="2026-09-08T22:52:00.817388-07:00", query="fixture:project")
        selection = json.loads(raw)["results"][0]["dossier_selection"]
        status, _, raw = self.request("api/dossier", selection)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(raw)["recorded_at"], fixtures.ADMITTED)

    def test_empty_registry_records_are_discoverable(self):
        _, _, raw = self.api(query="fixture:company")
        entity = json.loads(raw)["results"][0]
        self.assertEqual(entity["known_source_statement_count"], 0)
        status, _, raw = self.request("api/dossier", entity["dossier_selection"])
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(raw)["recorded_history"], [])

    def test_pagination_denominator_is_not_replaced_by_page_size(self):
        _, _, raw = self.api(limit="2", offset="2")
        report = json.loads(raw)
        self.assertEqual(report["visible_registry_entities"], 5)
        self.assertEqual(report["returned_entities"], 2)
        self.assertEqual(report["next_offset"], 4)

    def test_dossier_rejection_is_error_not_empty_evidence(self):
        status, _, raw = self.api("api/dossier", entity_id="not-an-entity")
        self.assertEqual(status, 400)
        self.assertIn("error", json.loads(raw))
        self.assertNotIn(b"source_statements", raw)

    def test_invalid_lineage_returns_no_partial_report(self):
        with patch("semiconductor_atlas.evidence_viewer.build_evidence_dossier", side_effect=ValueError("SECRET_PATH")):
            status, _, raw = self.api("api/dossier", entity_id="project")
        self.assertEqual(status, 400)
        self.assertNotIn(b"SECRET_PATH", raw)
        self.assertNotIn(b"world_current_claims", raw)

    def test_missing_wrong_and_traversal_tokens_are_rejected(self):
        for path in ("/", "/api/search", "/wrong/api/config", self.server.prefix + "../api/config",
                     self.server.prefix + "%2e%2e/viewer.js", self.server.prefix + "index.html",
                     self.server.origin + self.server.prefix):
            with self.subTest(path=path):
                self.assertEqual(self.request(raw_path=path)[0], 404)

    def test_wrong_hosts_are_rejected_even_with_valid_token(self):
        for host in ("evil.test", "127.0.0.1", "localhost:" + str(self.server.server_port), ""):
            with self.subTest(host=host):
                self.assertEqual(self.request(headers={"Host": host})[0], 403)

    def test_wrong_origins_and_fetch_sites_are_rejected(self):
        for headers in ({"Origin": "https://evil.test"}, {"Origin": "null"},
                        {"Sec-Fetch-Site": "cross-site"}, {"Sec-Fetch-Site": "same-site"}):
            self.assertEqual(self.request(headers=headers)[0], 403)
        self.assertEqual(self.request(headers={"Origin": self.server.origin, "Sec-Fetch-Site": "same-origin"})[0], 200)
        self.assertEqual(self.request(headers={"Sec-Fetch-Site": "none"})[0], 200)

    def test_duplicate_host_headers_are_rejected(self):
        client = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        try:
            client.putrequest("GET", self.server.prefix)
            client.putheader("Host", self.server.authority)
            client.endheaders()
            response = client.getresponse()
            self.assertEqual(response.status, 403)
            response.read()
        finally:
            client.close()

    def test_non_get_methods_are_not_supported(self):
        for method in ("POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"):
            with self.subTest(method=method):
                self.assertEqual(self.request(method=method)[0], 501)

    def test_duplicate_unknown_missing_and_malformed_parameters_fail(self):
        for route, query in (("api/search", "as_of=2026-09-09"), ("api/search", "x=x"),
                             ("api/config", "x=x"), ("viewer.js", "x=x"),
                             ("api/search", "query=one&query=two"), ("api/search", "broken"),
                             ("api/search", "query=%FF")):
            with self.subTest(query=query):
                self.assertEqual(self.request(raw_path=self.server.prefix + route + "?" + query)[0], 400)
        self.assertEqual(self.api(unexpected="x")[0], 400)
        self.assertEqual(self.api("api/dossier", entity_id="project", query="x")[0], 400)

    def test_invalid_clocks_and_limits_fail_without_fallback(self):
        for params in ({"as_of": "20260909"}, {"recorded_at": "2026-09-09"}, {"limit": "201"},
                       {"offset": "-1"}, {"limit": "true"}, {"query": ""}, {"kind": "invented"}):
            self.assertEqual(self.api(**params)[0], 400)

    def test_timezone_overflow_returns_bounded_error_and_server_keeps_serving(self):
        for cutoff in ("0001-01-01T00:00:00+23:59", "9999-12-31T23:59:59-23:59"):
            self.assertEqual(self.api(recorded_at=cutoff)[0], 400)
        self.assertEqual(self.request("api/config")[0], 200)

    def test_oversized_path_is_bounded(self):
        self.assertEqual(self.request(raw_path=self.server.prefix + "a" * 8192)[0], 404)

    def test_source_and_query_bytes_do_not_appear_in_access_logs(self):
        with patch("sys.stderr") as stderr:
            self.api(query="SECRET_QUERY")
            self.request(raw_path="/bad-token")
        stderr.write.assert_not_called()

    def test_database_bytes_unchanged_by_requests(self):
        before = hashlib.sha256(self.fixture.path.read_bytes()).hexdigest()
        self.api()
        self.api("api/dossier", entity_id="project")
        self.assertEqual(hashlib.sha256(self.fixture.path.read_bytes()).hexdigest(), before)

    def test_committed_wal_is_visible_at_start_but_later_commit_requires_restart(self):
        self.assertEqual(self.fixture.c.execute("PRAGMA journal_mode").fetchone()[0], "wal")
        # A source report in the committed WAL was already selected above. This later entity
        # predates the cutoff but was not in the database when the session pinned its snapshot.
        r.add_entity(self.fixture.c, m.Entity("later-commit", m.EntityKind.PROJECT,
                                            "new:entity", fixtures.CREATED, "Later commit"))
        self.fixture.c.commit()
        _, _, raw = self.api(query="Later commit")
        self.assertEqual(json.loads(raw)["total_matched_entities"], 0)
        self.assertEqual(self.api("api/dossier", entity_id="later-commit")[0], 400)
        old_prefix = self.server.prefix
        self.stop()
        self.start()
        self.assertNotEqual(old_prefix, self.server.prefix)
        _, _, raw = self.api(query="Later commit")
        self.assertEqual(json.loads(raw)["total_matched_entities"], 1)

    def test_cli_keeps_host_fixed_and_requires_both_clocks(self):
        args = build_parser().parse_args(["evidence-viewer", "--database", str(self.fixture.path),
            "--as-of", fixtures.AS_OF, "--recorded-at", fixtures.ADMITTED])
        self.assertEqual(args.port, 0)
        self.assertFalse(hasattr(args, "host"))

    def test_startup_validates_port_and_cutoffs_without_migration(self):
        for options in ({"port": -1}, {"port": True}, {"as_of": "20260909"}, {"recorded_at": fixtures.AS_OF}):
            with self.assertRaises(ValueError):
                with open_viewer(self.fixture.path, **dict(as_of=fixtures.AS_OF,
                        recorded_at=fixtures.ADMITTED) | options):
                    self.fail("invalid viewer opened")


if __name__ == "__main__":
    unittest.main()
