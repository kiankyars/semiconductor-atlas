"""Reviewed publisher grouping preserves original provenance and exact knowledge time."""

import copy
import hashlib
import json
import sqlite3
import unittest

from semiconductor_atlas import database, models as m, repository as r
from semiconductor_atlas import source_family_review as review


CREATED = "2026-01-01T00:00:00Z"
REVIEWED = "2026-09-09T12:00:00Z"
ADMITTED = "2026-09-09T12:00:00.000001Z"
LATER = "2026-09-09T12:00:00.000002Z"


def encoded(value):
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return raw, hashlib.sha256(raw).hexdigest()


class SourceFamilyReviewTests(unittest.TestCase):
    def setUp(self):
        self.c, _ = database.initialize(":memory:", target_version=11)
        self.addCleanup(self.c.close)
        for identifier in ("original", "publisher", "third"):
            r.add_source_family(self.c, m.SourceFamily(identifier, identifier, identifier.title(), CREATED))
        for identifier, family in (("source", "original"), ("other", "third")):
            r.add_source(self.c, m.Source(identifier, family, identifier, identifier.title(),
                "Synthetic publisher", "https://example.test/" + identifier, CREATED))
            r.add_source_document(self.c, m.SourceDocument("doc:" + identifier, identifier,
                "https://example.test/" + identifier + "/document", "Synthetic evidence", CREATED,
                ("a" if identifier == "source" else "b") * 64))
        r.add_entity(self.c, m.Entity("project", m.EntityKind.PROJECT, "project", CREATED))
        r.add_claim_series(self.c, m.ClaimSeries("series", "project", "series", "source_reported.name",
            m.ValueKind.SCALAR, CREATED))
        r.insert_claim(self.c, m.ClaimVersion("claim", "series", None, REVIEWED,
            m.ClaimKind.SOURCE_STATEMENT, "synthetic_review", None), m.ScalarValue(m.ScalarType.TEXT,
            "Synthetic project"), evidence=[m.EvidenceLink("doc:source", locator="Synthetic text")])
        self.c.commit()

    def migrate(self):
        self.c.commit()
        return database.apply_migrations(self.c, target_version=12, applied_at=REVIEWED)

    def document(self, identifier="doc:source"):
        return dict(self.c.execute("""SELECT id, source_id, document_url, content_sha256, retrieved_at
            FROM source_documents WHERE id=?""", (identifier,)).fetchone())

    def payload(self, *, source="source", family="publisher", predecessor=None, reviewed=REVIEWED):
        return {"format": review.FORMAT, "reviewer": "Synthetic reviewer", "reviewed_at": reviewed,
            "reason": "Group the exact publisher without changing importer provenance.",
            "prior_exposure": "Synthetic test fixture only", "source": dict(self.c.execute(
                "SELECT * FROM sources WHERE id=?", (source,)).fetchone()),
            "target_family": dict(self.c.execute("SELECT * FROM source_families WHERE id=?", (family,)).fetchone()),
            "expected_predecessor_id": predecessor, "evidence_documents": [self.document("doc:" + source)],
            "boundaries": dict(review.BOUNDARIES)}

    def insert(self, payload=None, *, recorded=ADMITTED):
        raw, digest = encoded(self.payload() if payload is None else payload)
        return review.insert_review(self.c, raw, expected_sha256=digest, recorded_at=recorded)

    def snapshot(self):
        return {row[0]: [tuple(r) for r in self.c.execute('SELECT * FROM "' + row[0] + '" ORDER BY rowid')]
            for row in self.c.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")}

    def raw_insert(self, payload=None, *, recorded=ADMITTED, **changes):
        payload = self.payload() if payload is None else payload
        raw, digest = encoded(payload)
        row = dict(id=r.stable_id(review.FORMAT, digest), source_id=payload["source"]["id"],
            family_id=payload["target_family"]["id"], supersedes_id=payload["expected_predecessor_id"],
            recorded_at=recorded, reviewed_at=payload["reviewed_at"], review_sha256=digest,
            review_json=raw.decode()) | changes
        self.c.execute("INSERT INTO source_family_assignments (" + ",".join(row) + ") VALUES ("
            + ",".join("?" for _ in row) + ")", tuple(row.values()))
        return row["id"]

    def assert_insert_rejected(self, payload, *, recorded=ADMITTED):
        before = self.snapshot()
        with self.assertRaises((ValueError, sqlite3.IntegrityError)):
            self.insert(payload, recorded=recorded)
        self.assertEqual(before, self.snapshot())

    def test_schema11_reader_is_empty_and_insertion_never_migrates(self):
        before = self.snapshot()
        self.assertFalse(review.available(self.c))
        self.assertEqual([], review.assignment_history(self.c, recorded_at=ADMITTED))
        self.assertEqual({}, review.resolutions(self.c, recorded_at=ADMITTED))
        with self.assertRaisesRegex(ValueError, "schema 12"):
            self.insert()
        self.assertEqual(before, self.snapshot())
        self.assertEqual(11, database.schema_version(self.c))

    def test_schema11_to12_preserves_all_original_rows_and_hashes(self):
        before = self.snapshot()
        self.assertEqual([12], self.migrate())
        after = self.snapshot()
        self.assertEqual([], after.pop("source_family_assignments"))
        ledger = after.pop("schema_migrations")
        self.assertEqual(before.pop("schema_migrations"), ledger[:-1])
        self.assertEqual((12, "source_family_assignments"), ledger[-1][:2])
        self.assertEqual(REVIEWED, ledger[-1][-1])
        self.assertEqual(before, after)
        self.assertEqual([], database.apply_migrations(self.c, target_version=12))
        self.assertEqual([], r.validate_database(self.c))
        self.assertEqual(1, self.c.execute("PRAGMA foreign_keys").fetchone()[0])

    def test_migration_preserves_disabled_foreign_keys(self):
        self.c.execute("PRAGMA foreign_keys=OFF")
        self.migrate()
        self.assertEqual(0, self.c.execute("PRAGMA foreign_keys").fetchone()[0])

    def test_failed_migration_preflight_preserves_schema11_and_exact_ledger(self):
        self.c.execute("DROP TRIGGER scalar_values_immutable_update")
        self.c.execute("UPDATE scalar_values SET text_value='different unhashed value' WHERE claim_version_id='claim'")
        self.c.commit()
        before = self.snapshot()
        with self.assertRaisesRegex(sqlite3.IntegrityError, "migration 0012 source family assignment validation failed"):
            self.migrate()
        self.assertEqual(before, self.snapshot())
        self.assertEqual(11, database.schema_version(self.c))
        self.assertFalse(review.available(self.c))
        self.assertFalse(self.c.in_transaction)
        self.assertEqual(1, self.c.execute("PRAGMA foreign_keys").fetchone()[0])

    def test_exact_review_bytes_and_stable_identifier_are_preserved(self):
        self.migrate()
        before = self.snapshot()
        payload = self.payload()
        raw = json.dumps(payload, indent=2, ensure_ascii=False).encode() + b"\n"
        digest = hashlib.sha256(raw).hexdigest()
        identifier = review.insert_review(self.c, raw, expected_sha256=digest, recorded_at=ADMITTED)
        self.assertEqual(r.stable_id(review.FORMAT, digest), identifier)
        row = review.assignment_history(self.c)[0]
        self.assertEqual(payload, row["review"])
        self.assertEqual(digest, row["review_sha256"])
        self.assertEqual(raw, row["review_json"].encode())
        self.assertEqual(raw, self.c.execute("SELECT review_json FROM source_family_assignments").fetchone()[0].encode())
        after = self.snapshot()
        after.pop("source_family_assignments"); before.pop("source_family_assignments")
        self.assertEqual(before, after)
        self.assertEqual([], r.validate_database(self.c))

    def test_hash_binds_exact_bytes_and_all_review_fields(self):
        payload = self.payload()
        raw, digest = encoded(payload)
        self.assertEqual(payload, review.parse_review(raw, digest))
        for mutated, expected in ((raw + b"\n", digest), (raw, "0" * 64), (raw.decode(), digest)):
            with self.subTest(mutated=repr(mutated)[:30]), self.assertRaisesRegex(ValueError, "hash differs"):
                review.parse_review(mutated, expected)
        for key in ("reason", "reviewer", "prior_exposure"):
            changed = copy.deepcopy(payload); changed[key] += " amended"
            changed_raw, _ = encoded(changed)
            with self.subTest(key=key), self.assertRaises(ValueError):
                review.parse_review(changed_raw, digest)

    def test_parser_rejects_duplicate_keys_nonfinite_and_wrong_contract(self):
        payload = self.payload()
        raw, _ = encoded(payload)
        malformed = [raw.replace(b'"format":', b'"reviewer":"duplicate","format":', 1),
            raw.replace(b'"source_rows_modified":false', b'"source_rows_modified":false,"source_rows_modified":false'),
            raw.replace(b'"reason":"Group the exact publisher without changing importer provenance."', b'"reason":NaN')]
        for change in ({"format": "wrong"}, {"extra": "not allowed"}, {"reviewer": "\t"},
                {"reviewed_at": "2026-09-09T12:00:00"}, {"source": []}, {"target_family": None},
                {"expected_predecessor_id": " "}, {"evidence_documents": []},
                {"evidence_documents": [self.document()] * 101}):
            malformed.append(encoded(payload | change)[0])
        missing = copy.deepcopy(payload); del missing["reason"]; malformed.append(encoded(missing)[0])
        for value in malformed:
            with self.subTest(value=value[:100]), self.assertRaises(ValueError):
                review.parse_review(value, hashlib.sha256(value).hexdigest())

    def test_boundaries_require_exact_literal_false_inventory(self):
        payload = self.payload()
        for boundaries in ({}, dict(review.BOUNDARIES, extra=False),
                dict(review.BOUNDARIES, claims_modified=True), dict(review.BOUNDARIES, claims_modified=0)):
            raw, digest = encoded(payload | {"boundaries": boundaries})
            with self.subTest(boundaries=boundaries), self.assertRaises(ValueError):
                review.parse_review(raw, digest)

    def test_evidence_document_inventory_has_exact_unique_fields(self):
        payload = self.payload(); doc = self.document()
        for docs in ([doc, doc], [doc | {"extra": "no"}], [{k:v for k,v in doc.items() if k != "id"}],
                [doc | {"retrieved_at": "2026-01-01"}], [doc | {"content_sha256": " "}]):
            raw, digest = encoded(payload | {"evidence_documents": docs})
            with self.subTest(docs=docs), self.assertRaises(ValueError):
                review.parse_review(raw, digest)

    def test_exact_endpoint_and_evidence_identity_cannot_drift(self):
        self.migrate()
        for field in ("source", "target_family"):
            for key, value in (("name", "Forged endpoint"), ("created_at", REVIEWED), ("id", "missing")):
                payload = self.payload(); payload[field][key] = value
                with self.subTest(field=field, key=key): self.assert_insert_rejected(payload)
        for key, value in (("document_url", "https://example.test/forged"), ("source_id", "other"),
                ("content_sha256", "c" * 64), ("retrieved_at", REVIEWED), ("id", "missing")):
            payload = self.payload(); payload["evidence_documents"][0][key] = value
            with self.subTest(key=key): self.assert_insert_rejected(payload)

    def test_review_requires_at_least_one_exact_subject_source_document(self):
        self.migrate()
        payload = self.payload(); payload["evidence_documents"] = [self.document("doc:other")]
        self.assert_insert_rejected(payload)
        payload["evidence_documents"].append(self.document())
        self.insert(payload)
        self.assertEqual([], review.validate_assignments(self.c))

    def test_review_and_admission_clocks_are_exact_and_aware(self):
        self.migrate()
        self.assert_insert_rejected(self.payload(reviewed=LATER), recorded=ADMITTED)
        for clock in ("2026-09-09", "2026-09-09T12:00:00", "bad", None):
            with self.subTest(clock=clock): self.assert_insert_rejected(self.payload(), recorded=clock)
        identifier = self.insert(self.payload(reviewed="2026-09-09T05:00:00.000001-07:00"))
        self.assertEqual([], review.assignment_history(self.c, recorded_at=REVIEWED))
        self.assertEqual([identifier], [row["id"] for row in review.assignment_history(self.c, recorded_at=ADMITTED)])

    def test_future_endpoint_and_document_by_one_microsecond_reject(self):
        self.migrate()
        r.add_source_family(self.c, m.SourceFamily("future-family", "future-family", "Future", LATER))
        self.assert_insert_rejected(self.payload(family="future-family", reviewed=ADMITTED), recorded=LATER)
        r.add_source(self.c, m.Source("future-source", "original", "future-source", "Future", "Publisher",
            "https://example.test/future", LATER))
        r.add_source_document(self.c, m.SourceDocument("doc:future-source", "future-source",
            "https://example.test/future/document", "Synthetic", CREATED, "c" * 64))
        self.assert_insert_rejected(self.payload(source="future-source", reviewed=ADMITTED), recorded=LATER)
        r.add_source_document(self.c, m.SourceDocument("future-doc", "source", "https://example.test/future-doc",
            "Synthetic", LATER, "d" * 64))
        payload = self.payload(reviewed=ADMITTED); payload["evidence_documents"] = [self.document("future-doc")]
        self.assert_insert_rejected(payload, recorded=LATER)

    def test_original_importer_family_must_exist_by_review(self):
        self.migrate()
        r.add_source_family(self.c, m.SourceFamily("future-original", "future-original", "Future original", LATER))
        r.add_source(self.c, m.Source("early-source", "future-original", "early-source", "Early", "Publisher",
            "https://example.test/early", CREATED))
        r.add_source_document(self.c, m.SourceDocument("doc:early-source", "early-source",
            "https://example.test/early/document", "Synthetic", CREATED, "e" * 64))
        self.assert_insert_rejected(self.payload(source="early-source", reviewed=ADMITTED), recorded=LATER)

    def test_raw_sql_guards_original_family_availability_at_review(self):
        self.migrate()
        r.add_source_family(self.c, m.SourceFamily("future-original", "future-original", "Future original", LATER))
        r.add_source(self.c, m.Source("early-source", "future-original", "early-source", "Early", "Publisher",
            "https://example.test/early", CREATED))
        r.add_source_document(self.c, m.SourceDocument("doc:early-source", "early-source",
            "https://example.test/early/document", "Synthetic", CREATED, "e" * 64))
        before = self.snapshot()
        with self.assertRaises(sqlite3.IntegrityError):
            self.raw_insert(self.payload(source="early-source", reviewed=ADMITTED), recorded=LATER)
        self.assertEqual(before, self.snapshot())

    def test_chain_can_return_to_original_family_without_mutating_original_source(self):
        self.migrate(); first = self.insert()
        second = self.insert(self.payload(family="original", predecessor=first, reviewed=ADMITTED), recorded=LATER)
        self.assertEqual([first, second], [row["id"] for row in review.assignment_history(self.c)])
        self.assertEqual("publisher", review.resolutions(self.c, recorded_at=ADMITTED)["source"]["source_family"])
        current = review.resolutions(self.c, recorded_at=LATER)["source"]
        self.assertEqual("original", current["source_family"])
        self.assertEqual(second, current["source_family_resolution"]["assignment_id"])
        self.assertEqual("original", current["source_family_resolution"]["original_source_family_id"])
        self.assertEqual("original", self.c.execute("SELECT family_id FROM sources WHERE id='source'").fetchone()[0])

    def test_interleaved_sources_and_offset_clocks_have_independent_exact_chains(self):
        self.migrate()
        first = self.insert(recorded="2026-09-09T05:00:00.000001-07:00")
        other = self.insert(self.payload(source="other"), recorded=REVIEWED)
        second = self.insert(self.payload(family="third", predecessor=first, reviewed=ADMITTED), recorded=LATER)
        self.assertEqual([other, first, second], [row["id"] for row in review.assignment_history(self.c)])
        mapping = review.resolutions(self.c, recorded_at=LATER)
        self.assertEqual({"source": "third", "other": "publisher"},
            {key:value["source_family"] for key,value in mapping.items()})

    def test_noop_stale_missing_and_cross_source_predecessors_reject_atomically(self):
        self.migrate()
        self.assert_insert_rejected(self.payload(family="original"))
        first = self.insert(); other = self.insert(self.payload(source="other"))
        for payload in (self.payload(family="third"), self.payload(family="third", predecessor="missing"),
                self.payload(family="third", predecessor=other), self.payload(predecessor=first, reviewed=ADMITTED)):
            self.assert_insert_rejected(payload, recorded=LATER)
        second = self.insert(self.payload(family="third", predecessor=first, reviewed=ADMITTED), recorded=LATER)
        self.assert_insert_rejected(self.payload(family="original", predecessor=first, reviewed=LATER),
            recorded="2026-09-09T12:00:00.000003Z")
        self.assertEqual(second, review.resolutions(self.c, recorded_at=LATER)["source"]["source_family_resolution"]["assignment_id"])

    def test_predecessor_requires_strict_admission_and_known_at_review(self):
        self.migrate(); first = self.insert()
        self.assert_insert_rejected(self.payload(family="third", predecessor=first, reviewed=REVIEWED), recorded=LATER)
        self.assert_insert_rejected(self.payload(family="third", predecessor=first, reviewed=ADMITTED), recorded=ADMITTED)
        self.insert(self.payload(family="third", predecessor=first, reviewed=ADMITTED), recorded=LATER)

    def test_duplicate_review_is_not_silently_readmitted_or_retimestamped(self):
        self.migrate(); self.insert()
        self.assert_insert_rejected(self.payload(), recorded=LATER)

    def test_assignment_immutability_covers_every_column_and_delete(self):
        self.migrate(); self.insert()
        for field, value in (("id", "changed"), ("source_id", "other"), ("family_id", "third"),
                ("recorded_at", LATER), ("reviewed_at", CREATED), ("review_sha256", "f" * 64),
                ("review_json", "{}"), ("supersedes_id", None)):
            with self.subTest(field=field), self.assertRaisesRegex(sqlite3.IntegrityError, "immutable"):
                self.c.execute("UPDATE source_family_assignments SET " + field + "=?", (value,))
        with self.assertRaisesRegex(sqlite3.IntegrityError, "immutable"):
            self.c.execute("DELETE FROM source_family_assignments")

    def test_raw_sql_guards_reject_invalid_clocks_hash_json_and_endpoints(self):
        self.migrate()
        for change in ({"recorded_at": REVIEWED, "reviewed_at": ADMITTED}, {"recorded_at": "2026-09-09T12:00:00"},
                {"reviewed_at": "not-a-clock"}, {"review_sha256": "A" * 64}, {"review_sha256": "a" * 63},
                {"review_json": "{"}, {"source_id": "missing"}, {"family_id": "missing"}, {"family_id": "original"}):
            before = self.snapshot()
            with self.subTest(change=change), self.assertRaises(sqlite3.IntegrityError):
                self.raw_insert(**change)
            self.assertEqual(before, self.snapshot())
        self.raw_insert(self.payload(reviewed="2026-09-09T05:00:00.000001-07:00"))
        self.assertEqual([], review.validate_assignments(self.c))

    def test_raw_sql_guards_endpoint_microsecond_availability(self):
        self.migrate()
        r.add_source_family(self.c, m.SourceFamily("future-family", "future-family", "Future", LATER))
        r.add_source(self.c, m.Source("future-source", "original", "future-source", "Future", "Publisher",
            "https://example.test/future", LATER))
        for changes in ({"family_id": "future-family"}, {"source_id": "future-source"}):
            before = self.snapshot()
            with self.subTest(changes=changes), self.assertRaises(sqlite3.IntegrityError):
                self.raw_insert(recorded=LATER, reviewed_at=ADMITTED, **changes)
            self.assertEqual(before, self.snapshot())

    def test_raw_sql_guards_reject_forks_cross_source_and_nonincreasing_chain(self):
        self.migrate(); first = self.insert(); other = self.insert(self.payload(source="other"))
        payload = self.payload(family="third", predecessor=first, reviewed=ADMITTED)
        for changes in ({"supersedes_id": None}, {"supersedes_id": other}, {"family_id": "publisher"},
                {"recorded_at": ADMITTED}, {"reviewed_at": REVIEWED}):
            before = self.snapshot()
            with self.subTest(changes=changes), self.assertRaises(sqlite3.IntegrityError):
                self.raw_insert(payload, recorded=LATER, **changes)
            self.assertEqual(before, self.snapshot())
        self.raw_insert(payload, recorded=LATER)
        with self.assertRaises(sqlite3.IntegrityError):
            self.raw_insert(self.payload(family="original", predecessor=first, reviewed=LATER),
                recorded="2026-09-09T12:00:00.000003Z")

    def test_validator_catches_raw_hash_identity_content_and_duplicate_key_tampering(self):
        self.migrate(); self.insert()
        self.c.execute("DROP TRIGGER source_family_assignments_immutable_update")
        baseline = self.c.execute("SELECT review_json FROM source_family_assignments").fetchone()[0]
        for field, value in (("id", "forged"), ("review_sha256", "0" * 64),
                ("review_json", baseline.replace('"reviewer":', '"reason":"duplicate","reviewer":', 1)),
                ("source_id", "other"), ("family_id", "third"), ("reviewed_at", CREATED)):
            self.c.execute("SAVEPOINT corrupt")
            try:
                self.c.execute("UPDATE source_family_assignments SET " + field + "=?", (value,))
                with self.subTest(field=field), self.assertRaises(ValueError):
                    review.assignment_history(self.c)
                self.assertTrue(any("source family assignment" in e for e in r.validate_database(self.c)))
            finally:
                self.c.execute("ROLLBACK TO corrupt"); self.c.execute("RELEASE corrupt")

    def test_validator_checks_duplicate_json_keys_even_with_matching_raw_hash(self):
        self.migrate()
        raw, _ = encoded(self.payload())
        raw = raw.replace(b'"reviewer":', b'"reason":"duplicate","reviewer":', 1)
        digest = hashlib.sha256(raw).hexdigest()
        self.raw_insert(id=r.stable_id(review.FORMAT, digest), review_sha256=digest, review_json=raw.decode())
        with self.assertRaisesRegex(ValueError, "duplicate source family review key"):
            review.assignment_history(self.c)
        self.assertTrue(review.validate_assignments(self.c))

    def test_invalid_future_clock_cannot_hide_behind_historical_cutoff(self):
        self.migrate(); self.insert()
        self.c.execute("DROP TRIGGER source_family_assignments_immutable_update")
        self.c.execute("PRAGMA ignore_check_constraints=ON")
        self.c.execute("UPDATE source_family_assignments SET recorded_at='not-a-clock'")
        with self.assertRaisesRegex(ValueError, "aware knowledge timestamp"):
            review.assignment_history(self.c, recorded_at=CREATED)
        self.assertTrue(review.validate_assignments(self.c))

    def test_nested_caller_transaction_survives_success_and_failure(self):
        self.migrate(); self.c.execute("SAVEPOINT caller")
        first = self.insert()
        self.assertTrue(self.c.in_transaction)
        self.assert_insert_rejected(self.payload(family="third"), recorded=LATER)
        self.assertEqual([first], [x["id"] for x in review.assignment_history(self.c)])
        self.c.execute("ROLLBACK TO caller"); self.c.execute("RELEASE caller")
        self.assertEqual([], review.assignment_history(self.c))

    def test_resolution_changes_only_existing_family_fields_and_carries_original_lineage(self):
        self.migrate(); identifier = self.insert()
        item = {"source_family": "original", "publisher": "Exact publisher", "unrelated": [1]}
        actual = review.apply_resolution(item, "source", review.resolutions(self.c, recorded_at=ADMITTED))
        self.assertIs(item, actual)
        self.assertEqual("publisher", actual["source_family"])
        self.assertEqual("Exact publisher", actual["publisher"])
        self.assertEqual([1], actual["unrelated"])
        self.assertNotIn("source_family_name", actual)
        self.assertEqual(identifier, actual["source_family_resolution"]["assignment_id"])
        untouched = {"source_family": "original"}
        self.assertEqual(untouched, review.apply_resolution(dict(untouched), "unassigned", {}))


if __name__ == "__main__":
    unittest.main()
