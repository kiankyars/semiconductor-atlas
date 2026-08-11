from __future__ import annotations

import copy
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from semiconductor_atlas.gleif_snapshot import (
    GLEIFHTTPResponse,
    GLEIF_API_URL_TEMPLATE,
    GLEIF_CANONICAL_FILENAME,
    GLEIF_MAX_ALLOWLIST_RECORDS,
    GLEIF_MINIMUM_REQUEST_INTERVAL_SECONDS,
    canonical_lei_allowlist_bytes,
    create_gleif_snapshot,
    parse_lei_allowlist_bytes,
    read_lei_allowlist_file,
    verify_gleif_snapshot,
)
from semiconductor_atlas.gleif_snapshot import _rename_directory_no_replace


FIRST_LEI = "2549005GOBWLCSY63Q97"
SECOND_LEI = "KNX4USFCNGPY45LOCE31"
THIRD_LEI = "529900Z9HRA5529A5V36"
PUBLISH_A = "2026-07-19T16:00:00Z"
PUBLISH_B = "2026-07-19T20:00:00Z"


def _address(city: str) -> dict[str, object]:
    return {
        "language": "en",
        "addressLines": ["1 Semiconductor Way"],
        "addressNumber": "1",
        "addressNumberWithinBuilding": None,
        "mailRouting": None,
        "city": city,
        "region": "US-AZ",
        "country": "US",
        "postalCode": "85284",
    }


def _record(lei: str, name: str) -> dict[str, object]:
    return {
        "type": "lei-records",
        "id": lei,
        "attributes": {
            "lei": lei,
            "entity": {
                "legalName": {"name": name, "language": "en"},
                "otherNames": [],
                "transliteratedOtherNames": [],
                "legalAddress": _address("Tempe"),
                "headquartersAddress": _address("Phoenix"),
                "registeredAt": {"id": "RA000602", "other": None},
                "registeredAs": "fixture-1",
                "jurisdiction": "US-AZ",
                "category": "GENERAL",
                "subCategory": None,
                "legalForm": {"id": "XTIQ", "other": None},
                "status": "ACTIVE",
                "creationDate": "2020-01-01T00:00:00Z",
                "expiration": {"date": None, "reason": None},
                "successorEntity": {"lei": None, "name": None},
                "successorEntities": [],
                "eventGroups": [],
            },
            "registration": {
                "initialRegistrationDate": "2021-01-01T00:00:00Z",
                "lastUpdateDate": "2026-07-01T00:00:00Z",
                "status": "ISSUED",
                "nextRenewalDate": "2027-07-01T00:00:00Z",
                "managingLou": lei,
                "corroborationLevel": "FULLY_CORROBORATED",
                "validatedAt": {"id": "RA000602", "other": None},
                "validatedAs": "fixture-1",
                "otherValidationAuthorities": [],
            },
        },
        "relationships": {
            "direct-parent": {
                "links": {"related": "https://api.gleif.org/not-followed"}
            }
        },
        "links": {"self": GLEIF_API_URL_TEMPLATE.format(lei=lei)},
    }


def _response_raw(
    lei: str,
    publish_date: str = PUBLISH_A,
    *,
    name: str | None = None,
    data: object | None = None,
    sort_keys: bool = False,
) -> bytes:
    payload = {
        "meta": {"goldenCopy": {"publishDate": publish_date}},
        "data": _record(lei, name or f"Entity {lei}") if data is None else data,
    }
    return json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=sort_keys,
    ).encode("utf-8")


class _FakeTime:
    def __init__(self) -> None:
        self.value = 0.0
        self.sleeps: list[float] = []
        self.origin = datetime(2026, 7, 20, 12, 0, 0, tzinfo=UTC)

    def monotonic(self) -> float:
        return self.value

    def sleep(self, duration: float) -> None:
        self.sleeps.append(duration)
        self.value += duration

    def wall_clock(self) -> datetime:
        return self.origin + timedelta(seconds=self.value)


class _SequenceTransport:
    def __init__(
        self,
        raws: list[bytes],
        fake_time: _FakeTime,
        *,
        durations: list[float] | None = None,
    ) -> None:
        self.raws = list(raws)
        self.fake_time = fake_time
        self.durations = list(durations or [0.0] * len(raws))
        self.calls: list[tuple[str, float, float]] = []

    def __call__(self, url: str, timeout: float) -> GLEIFHTTPResponse:
        self.calls.append((url, timeout, self.fake_time.value))
        if not self.raws:
            raise AssertionError("unexpected GLEIF transport call")
        raw = self.raws.pop(0)
        duration = self.durations.pop(0)
        self.fake_time.value += duration
        return GLEIFHTTPResponse(
            raw,
            final_url=url,
            content_type="application/vnd.api+json; charset=utf-8",
            etag='"fixture"',
            last_modified="Sun, 19 Jul 2026 16:00:00 GMT",
        )


def _manifest(root: Path) -> dict[str, object]:
    return json.loads((root / "manifest.json").read_text(encoding="utf-8"))


def _write_manifest(root: Path, manifest: dict[str, object]) -> None:
    (root / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _tree_bytes(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


class GLEIFSnapshotTests(unittest.TestCase):
    def _create(
        self,
        parent: Path,
        *,
        raws: list[bytes] | None = None,
        fake_time: _FakeTime | None = None,
        durations: list[float] | None = None,
        max_attempts: int = 3,
        name: str = "snapshot",
    ):
        clock = fake_time or _FakeTime()
        response_raws = raws or [
            _response_raw(FIRST_LEI),
            _response_raw(SECOND_LEI),
        ]
        transport = _SequenceTransport(
            response_raws,
            clock,
            durations=durations,
        )
        output = parent / name
        snapshot = create_gleif_snapshot(
            canonical_lei_allowlist_bytes((FIRST_LEI, SECOND_LEI)),
            output,
            transport=transport,
            wall_clock=clock.wall_clock,
            monotonic_clock=clock.monotonic,
            sleeper=clock.sleep,
            timeout_seconds=12.5,
            max_attempts=max_attempts,
        )
        return snapshot, transport, clock

    def test_allowlist_requires_exact_sorted_checksum_valid_ascii(self) -> None:
        valid = canonical_lei_allowlist_bytes((FIRST_LEI, SECOND_LEI))
        self.assertEqual((FIRST_LEI, SECOND_LEI), parse_lei_allowlist_bytes(valid))
        invalid = {
            "empty": b"",
            "unterminated": valid[:-1],
            "CRLF": valid.replace(b"\n", b"\r\n"),
            "blank": valid.replace(b"\n", b"\n\n", 1),
            "unsorted": canonical_lei_allowlist_bytes((FIRST_LEI, SECOND_LEI))[
                21:
            ]
            + canonical_lei_allowlist_bytes((FIRST_LEI, SECOND_LEI))[:21],
            "duplicate": f"{FIRST_LEI}\n{FIRST_LEI}\n".encode("ascii"),
            "lowercase": f"{FIRST_LEI.lower()}\n".encode("ascii"),
            "bad checksum": b"2549005GOBWLCSY63Q96\n",
            "BOM": b"\xef\xbb\xbf" + valid,
        }
        for label, raw in invalid.items():
            with self.subTest(label=label), self.assertRaises(ValueError):
                parse_lei_allowlist_bytes(raw)

        too_many = [FIRST_LEI] * (GLEIF_MAX_ALLOWLIST_RECORDS + 1)
        with self.assertRaises(ValueError):
            canonical_lei_allowlist_bytes(too_many)

    def test_acquires_and_replays_exact_raw_and_canonical_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            snapshot, transport, clock = self._create(root)

            self.assertEqual((FIRST_LEI, SECOND_LEI), snapshot.leis)
            self.assertEqual(PUBLISH_A, snapshot.golden_copy_publish_date)
            self.assertEqual(2, len(snapshot.responses))
            self.assertEqual(1, snapshot.attempts_used)
            self.assertTrue(snapshot.canonical_bytes.endswith(b"\n"))
            self.assertNotIn(b"relationships", snapshot.canonical_bytes)
            self.assertEqual(
                [
                    GLEIF_API_URL_TEMPLATE.format(lei=FIRST_LEI),
                    GLEIF_API_URL_TEMPLATE.format(lei=SECOND_LEI),
                ],
                [call[0] for call in transport.calls],
            )
            self.assertEqual(
                [GLEIF_MINIMUM_REQUEST_INTERVAL_SECONDS], clock.sleeps
            )

            replay = verify_gleif_snapshot(snapshot.root)
            self.assertEqual(snapshot.manifest_bytes, replay.manifest_bytes)
            self.assertEqual(snapshot.canonical_bytes, replay.canonical_bytes)
            self.assertEqual(
                [item.raw_bytes for item in snapshot.responses],
                [item.raw_bytes for item in replay.responses],
            )

    def test_fixed_inputs_and_clocks_produce_byte_identical_trees(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first, _, _ = self._create(root, name="first")
            second, _, _ = self._create(root, name="second")
            self.assertEqual(_tree_bytes(first.root), _tree_bytes(second.root))

    def test_raw_json_order_does_not_change_canonical_derivative(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first, _, _ = self._create(root, name="natural")
            reordered, _, _ = self._create(
                root,
                name="reordered",
                raws=[
                    _response_raw(FIRST_LEI, sort_keys=True),
                    _response_raw(SECOND_LEI, sort_keys=True),
                ],
            )
            self.assertNotEqual(
                first.responses[0].raw_bytes,
                reordered.responses[0].raw_bytes,
            )
            self.assertEqual(first.canonical_bytes, reordered.canonical_bytes)

    def test_rotation_retries_whole_attempt_and_paces_across_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            raws = [
                _response_raw(FIRST_LEI, PUBLISH_A, name="discarded first"),
                _response_raw(SECOND_LEI, PUBLISH_B, name="rotation detector"),
                _response_raw(FIRST_LEI, PUBLISH_B, name="retained first"),
                _response_raw(SECOND_LEI, PUBLISH_B, name="retained second"),
            ]
            snapshot, transport, clock = self._create(
                root,
                raws=raws,
                fake_time=_FakeTime(),
            )

            self.assertEqual(2, snapshot.attempts_used)
            self.assertEqual(PUBLISH_B, snapshot.golden_copy_publish_date)
            self.assertEqual(4, len(transport.calls))
            starts = [call[2] for call in transport.calls]
            for expected, actual in zip((0.0, 1.1, 2.2, 3.3), starts):
                self.assertAlmostEqual(expected, actual)
            for actual in clock.sleeps:
                self.assertAlmostEqual(1.1, actual)
            self.assertNotIn(b"discarded first", snapshot.responses[0].raw_bytes)
            self.assertIn(b"retained first", snapshot.responses[0].raw_bytes)

    def test_persistent_rotation_installs_nothing_and_cleans_stages(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            clock = _FakeTime()
            transport = _SequenceTransport(
                [
                    _response_raw(FIRST_LEI, PUBLISH_A),
                    _response_raw(SECOND_LEI, PUBLISH_B),
                    _response_raw(FIRST_LEI, PUBLISH_A),
                    _response_raw(SECOND_LEI, PUBLISH_B),
                ],
                clock,
            )
            output = root / "never-installed"
            with self.assertRaisesRegex(ValueError, "changed during every"):
                create_gleif_snapshot(
                    canonical_lei_allowlist_bytes((FIRST_LEI, SECOND_LEI)),
                    output,
                    transport=transport,
                    wall_clock=clock.wall_clock,
                    monotonic_clock=clock.monotonic,
                    sleeper=clock.sleep,
                    max_attempts=2,
                )
            self.assertFalse(output.exists())
            self.assertEqual([], list(root.glob(".never-installed.stage-*")))

    def test_rate_limiter_counts_transport_duration_and_does_not_sleep_last(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            clock = _FakeTime()
            _, transport, clock = self._create(
                root,
                fake_time=clock,
                durations=[0.4, 0.2],
            )
            self.assertEqual(1, len(clock.sleeps))
            self.assertAlmostEqual(0.7, clock.sleeps[0])
            starts = [call[2] for call in transport.calls]
            self.assertAlmostEqual(0.0, starts[0])
            self.assertAlmostEqual(1.1, starts[1])

    def test_invalid_configuration_and_existing_output_fail_before_transport(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "existing"
            output.mkdir()
            clock = _FakeTime()
            transport = _SequenceTransport([], clock)
            allowlist = canonical_lei_allowlist_bytes((FIRST_LEI,))
            for label, kwargs in (
                ("zero timeout", {"timeout_seconds": 0}),
                ("too many attempts", {"max_attempts": 6}),
            ):
                with self.subTest(label=label), self.assertRaises(ValueError):
                    create_gleif_snapshot(
                        allowlist,
                        root / label.replace(" ", "-"),
                        transport=transport,
                        **kwargs,
                    )
            with self.assertRaises(FileExistsError):
                create_gleif_snapshot(allowlist, output, transport=transport)
            self.assertEqual([], transport.calls)

    def test_atomic_install_primitive_never_replaces_an_existing_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            stage = root / "stage"
            stage.mkdir()
            (stage / "new.txt").write_text("new", encoding="utf-8")
            destination = root / "destination"
            destination.mkdir()
            destination_inode = destination.stat().st_ino

            with self.assertRaises(FileExistsError):
                _rename_directory_no_replace(stage, destination)
            self.assertTrue(stage.is_dir())
            self.assertEqual(destination_inode, destination.stat().st_ino)
            self.assertEqual([], list(destination.iterdir()))

    def test_rejects_wrong_response_cardinality_identity_and_url(self) -> None:
        cases = (
            ("zero", _response_raw(FIRST_LEI, data=[]), None),
            (
                "multiple",
                _response_raw(
                    FIRST_LEI,
                    data=[
                        _record(FIRST_LEI, "First"),
                        _record(SECOND_LEI, "Second"),
                    ],
                ),
                None,
            ),
            ("wrong LEI", _response_raw(SECOND_LEI), None),
            ("invalid UTF-8", b"\xff", None),
            ("redirect", _response_raw(FIRST_LEI), "https://example.com/"),
        )
        for label, raw, final_url in cases:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                calls = 0

                def transport(url: str, _timeout: float) -> GLEIFHTTPResponse:
                    nonlocal calls
                    calls += 1
                    return GLEIFHTTPResponse(
                        raw,
                        final_url=final_url or url,
                        content_type="application/vnd.api+json",
                    )

                with self.assertRaises((ValueError, TypeError)):
                    create_gleif_snapshot(
                        canonical_lei_allowlist_bytes((FIRST_LEI,)),
                        root / "output",
                        transport=transport,
                        wall_clock=lambda: datetime(2026, 7, 20, tzinfo=UTC),
                    )
                self.assertEqual(1, calls)
                self.assertFalse((root / "output").exists())

    def test_rejects_json_extensions_bad_content_type_and_wrong_transport_type(self) -> None:
        cases: tuple[tuple[str, object, str], ...] = (
            (
                "duplicate key",
                (
                    b'{"meta":{"goldenCopy":{"publishDate":"'
                    + PUBLISH_A.encode("ascii")
                    + b'"}},"data":[],"data":[]}'
                ),
                "application/vnd.api+json",
            ),
            (
                "NaN",
                b'{"meta":{"goldenCopy":{"publishDate":NaN}},"data":[]}',
                "application/vnd.api+json",
            ),
            ("bad content type", _response_raw(FIRST_LEI), "text/html"),
            ("wrong transport type", object(), "application/vnd.api+json"),
        )
        for label, response_value, content_type in cases:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)

                def transport(url: str, _timeout: float):
                    if isinstance(response_value, bytes):
                        return GLEIFHTTPResponse(
                            response_value,
                            final_url=url,
                            content_type=content_type,
                        )
                    return response_value

                with self.assertRaises((TypeError, ValueError)):
                    create_gleif_snapshot(
                        canonical_lei_allowlist_bytes((FIRST_LEI,)),
                        root / "output",
                        transport=transport,
                        wall_clock=lambda: datetime(2026, 7, 20, tzinfo=UTC),
                    )
                self.assertFalse((root / "output").exists())

    def test_verifier_rejects_manifest_scope_and_request_policy_drift(self) -> None:
        mutations = (
            (
                "rights",
                lambda scope: scope["rights"].__setitem__("license", "unknown"),
            ),
            (
                "coverage",
                lambda scope: scope.__setitem__("coverage", "global"),
            ),
            (
                "rate",
                lambda scope: scope["request_policy"].__setitem__(
                    "minimum_request_start_interval_seconds", 1.0
                ),
            ),
            (
                "timeout ceiling",
                lambda scope: scope["request_policy"].__setitem__(
                    "timeout_seconds", 301
                ),
            ),
            (
                "relationships",
                lambda scope: scope["relationships"].__setitem__("included", True),
            ),
        )
        for label, mutate in mutations:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                snapshot, _, _ = self._create(root)
                manifest = _manifest(snapshot.root)
                mutate(manifest["source_scopes"]["gleif_lei_level_1"])
                _write_manifest(snapshot.root, manifest)
                with self.assertRaises(ValueError):
                    verify_gleif_snapshot(snapshot.root)

    def test_verifier_rejects_hash_consistent_canonical_and_raw_mutations(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            snapshot, _, _ = self._create(root, name="canonical-mutation")
            manifest = _manifest(snapshot.root)
            canonical = snapshot.root / GLEIF_CANONICAL_FILENAME
            changed = b'{"changed":true}\n'
            canonical.write_bytes(changed)
            manifest["inputs"][0]["bytes"] = len(changed)
            manifest["inputs"][0]["sha256"] = hashlib.sha256(changed).hexdigest()
            _write_manifest(snapshot.root, manifest)
            with self.assertRaisesRegex(ValueError, "does not replay"):
                verify_gleif_snapshot(snapshot.root)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            snapshot, _, _ = self._create(root, name="raw-mutation")
            manifest = _manifest(snapshot.root)
            entry = manifest["inputs"][1]
            old_path = snapshot.root / entry["path"]
            changed = _response_raw(FIRST_LEI, PUBLISH_B)
            digest = hashlib.sha256(changed).hexdigest()
            new_path = snapshot.root / "raw" / "sha256" / f"{digest}.json"
            new_path.write_bytes(changed)
            old_path.unlink()
            entry["path"] = f"raw/sha256/{digest}.json"
            entry["bytes"] = len(changed)
            entry["sha256"] = digest
            _write_manifest(snapshot.root, manifest)
            with self.assertRaisesRegex(ValueError, "one Golden Copy"):
                verify_gleif_snapshot(snapshot.root)

    def test_verifier_rejects_traversal_duplicate_and_non_content_addressed_paths(self) -> None:
        mutations = (
            ("traversal", "../escaped.json"),
            ("dot", "raw/./response.json"),
            ("not addressed", "raw/response.json"),
            ("duplicate", GLEIF_CANONICAL_FILENAME),
        )
        for label, path in mutations:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                snapshot, _, _ = self._create(root)
                manifest = _manifest(snapshot.root)
                manifest["inputs"][1]["path"] = path
                _write_manifest(snapshot.root, manifest)
                with self.assertRaises(ValueError):
                    verify_gleif_snapshot(snapshot.root)

    def test_verifier_rejects_missing_extra_and_reordered_raw_inputs(self) -> None:
        mutations = (
            ("missing", lambda inputs: inputs.pop()),
            ("extra", lambda inputs: inputs.append(copy.deepcopy(inputs[-1]))),
            (
                "reordered",
                lambda inputs: inputs.__setitem__(
                    slice(1, 3), [inputs[2], inputs[1]]
                ),
            ),
        )
        for label, mutate in mutations:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                snapshot, _, _ = self._create(root)
                manifest = _manifest(snapshot.root)
                mutate(manifest["inputs"])
                _write_manifest(snapshot.root, manifest)
                with self.assertRaises(ValueError):
                    verify_gleif_snapshot(snapshot.root)

    def test_verifier_rejects_missing_truncated_and_allowlist_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            snapshot, _, _ = self._create(root, name="missing")
            manifest = _manifest(snapshot.root)
            raw_path = snapshot.root / manifest["inputs"][1]["path"]
            raw_path.unlink()
            with self.assertRaisesRegex(ValueError, "missing or unsafe"):
                verify_gleif_snapshot(snapshot.root)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            snapshot, _, _ = self._create(root, name="truncated")
            manifest = _manifest(snapshot.root)
            raw_path = snapshot.root / manifest["inputs"][1]["path"]
            raw_path.write_bytes(raw_path.read_bytes()[:-1])
            with self.assertRaisesRegex(ValueError, "size mismatch"):
                verify_gleif_snapshot(snapshot.root)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            snapshot, _, _ = self._create(root, name="allowlist")
            manifest = _manifest(snapshot.root)
            allowlist_path = snapshot.root / "gleif-lei-allowlist.txt"
            changed = canonical_lei_allowlist_bytes((FIRST_LEI, THIRD_LEI))
            allowlist_path.write_bytes(changed)
            metadata = manifest["source_scopes"]["gleif_lei_level_1"]["allowlist"]
            metadata["bytes"] = len(changed)
            metadata["sha256"] = hashlib.sha256(changed).hexdigest()
            _write_manifest(snapshot.root, manifest)
            with self.assertRaisesRegex(ValueError, "disagree"):
                verify_gleif_snapshot(snapshot.root)

    def test_verifier_rejects_symlink_fifo_and_symlinked_directory_without_blocking(self) -> None:
        cases = ("raw symlink", "raw fifo", "raw directory symlink")
        for case in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                snapshot, _, _ = self._create(root)
                manifest = _manifest(snapshot.root)
                raw_path = snapshot.root / manifest["inputs"][1]["path"]
                if case == "raw symlink":
                    actual = snapshot.root / "actual.json"
                    raw_path.replace(actual)
                    raw_path.symlink_to(actual)
                elif case == "raw fifo":
                    raw_path.unlink()
                    os.mkfifo(raw_path)
                else:
                    raw_directory = snapshot.root / "raw"
                    actual_directory = snapshot.root / "actual-raw"
                    raw_directory.replace(actual_directory)
                    raw_directory.symlink_to(actual_directory, target_is_directory=True)
                with self.assertRaisesRegex(ValueError, "symlink|regular|unsafe"):
                    verify_gleif_snapshot(snapshot.root)

    def test_verifier_rejects_symlinked_or_fifo_manifest_and_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            snapshot, _, _ = self._create(root, name="manifest-symlink")
            manifest_path = snapshot.root / "manifest.json"
            actual = snapshot.root / "actual-manifest.json"
            manifest_path.replace(actual)
            manifest_path.symlink_to(actual)
            with self.assertRaisesRegex(ValueError, "unsafe"):
                verify_gleif_snapshot(snapshot.root)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            snapshot, _, _ = self._create(root, name="manifest-fifo")
            manifest_path = snapshot.root / "manifest.json"
            manifest_path.unlink()
            os.mkfifo(manifest_path)
            with self.assertRaisesRegex(ValueError, "regular"):
                verify_gleif_snapshot(snapshot.root)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            snapshot, _, _ = self._create(root, name="real-root")
            linked = root / "linked-root"
            linked.symlink_to(snapshot.root, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "non-symlink directory"):
                verify_gleif_snapshot(linked)

    def test_manifest_duplicate_keys_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            snapshot, _, _ = self._create(root)
            (snapshot.root / "manifest.json").write_bytes(
                b'{"format":"first","format":"second"}\n'
            )
            with self.assertRaisesRegex(ValueError, "duplicate JSON object key"):
                verify_gleif_snapshot(snapshot.root)

    def test_verified_result_retains_bytes_after_path_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            snapshot, _, _ = self._create(root)
            canonical_before = snapshot.canonical_bytes
            raw_before = snapshot.responses[0].raw_bytes
            (snapshot.root / GLEIF_CANONICAL_FILENAME).write_bytes(b"changed")
            manifest = _manifest(snapshot.root)
            raw_path = snapshot.root / manifest["inputs"][1]["path"]
            raw_path.write_bytes(b"changed")
            self.assertEqual(canonical_before, snapshot.canonical_bytes)
            self.assertEqual(raw_before, snapshot.responses[0].raw_bytes)

    def test_allowlist_reader_rejects_symlink_and_fifo_without_blocking(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            actual = root / "actual.txt"
            actual.write_bytes(canonical_lei_allowlist_bytes((FIRST_LEI,)))
            linked = root / "linked.txt"
            linked.symlink_to(actual)
            with self.assertRaisesRegex(ValueError, "unsafe"):
                read_lei_allowlist_file(linked)

            fifo = root / "allowlist.fifo"
            os.mkfifo(fifo)
            with self.assertRaisesRegex(ValueError, "regular"):
                read_lei_allowlist_file(fifo)

    def test_manifest_retrieval_clocks_cannot_precede_publication_or_regress(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            snapshot, _, _ = self._create(root)
            manifest = _manifest(snapshot.root)
            manifest["inputs"][1]["retrieved_at"] = "2026-07-19T15:59:59Z"
            _write_manifest(snapshot.root, manifest)
            with self.assertRaisesRegex(ValueError, "predates"):
                verify_gleif_snapshot(snapshot.root)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            snapshot, _, _ = self._create(root)
            manifest = _manifest(snapshot.root)
            first_time = manifest["inputs"][1]["retrieved_at"]
            manifest["inputs"][2]["retrieved_at"] = first_time
            manifest["retrieved_at"] = first_time
            _write_manifest(snapshot.root, manifest)
            verified = verify_gleif_snapshot(snapshot.root)
            self.assertEqual(first_time, verified.retrieved_at)

            manifest = copy.deepcopy(manifest)
            manifest["inputs"][1]["retrieved_at"] = "2026-07-20T12:00:01Z"
            _write_manifest(snapshot.root, manifest)
            with self.assertRaisesRegex(ValueError, "later than snapshot|non-decreasing"):
                verify_gleif_snapshot(snapshot.root)

    def test_verify_only_cli_replays_without_network(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            snapshot, _, _ = self._create(root)
            script = Path(__file__).resolve().parents[1] / "scripts" / "fetch_gleif.py"
            result = subprocess.run(
                [sys.executable, str(script), "--verify-only", str(snapshot.root)],
                cwd=script.parents[1],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(0, result.returncode, result.stderr)
            summary = json.loads(result.stdout)
            self.assertEqual(2, summary["lei_count"])
            self.assertEqual(PUBLISH_A, summary["golden_copy_publish_date"])


if __name__ == "__main__":
    unittest.main()
