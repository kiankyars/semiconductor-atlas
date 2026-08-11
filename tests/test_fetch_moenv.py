from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import json
import sys
import tempfile
import unittest
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import HTTPError

from semiconductor_atlas.adapters.moenv_ems import MOENV_FIELDS
from semiconductor_atlas.moenv_snapshot import (
    MOENV_FULL_PACKAGE_URL,
    MOENV_PACKAGE_PID,
    MOENV_RESOURCE_RID,
    verify_moenv_snapshot,
)


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "fetch_moenv.py"
DATASET_UPDATED_AT = "2026-07-19T23:15:13Z"
COMPLETED_AT = datetime(2026, 7, 20, 6, 57, 50, tzinfo=UTC)
JSON_MEMBER = "環境保護許可管理系統(暨解除列管)對象基本資料.json"


def _load_fetch_module():
    specification = importlib.util.spec_from_file_location(
        "_semiconductor_atlas_fetch_moenv_test", SCRIPT
    )
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)
    return module


FETCH = _load_fetch_module()


def _row() -> dict[str, object]:
    row: dict[str, object] = {field: "" for field in MOENV_FIELDS}
    row.update(
        {
            "emsno": "p5806269",
            "facilityname": "測試半導體股份有限公司",
            "uniformno": "00123456",
            "county": "新竹市",
            "township": "東區",
            "facilityaddress": "新竹市東區測試路1號",
            "industryareaname": "新竹科學園區",
            "industryid": "2611",
            "industryname": "積體電路製造業",
            "twd97tm2x": "250000.125",
            "twd97tm2y": "2740000.500",
            "wgs84lon": "121.012300",
            "wgs84lat": "24.800400",
            "isair": "1",
            "iswater": "0",
            "iswaste": "0",
            "istoxic": "0",
            "issoil": "0",
            "industrygroup": "261",
            "admino": None,
            "facno": None,
        }
    )
    return row


def _archive_raw() -> bytes:
    member_raw = json.dumps(
        [_row()], ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    publisher_md5 = hashlib.md5(member_raw).hexdigest()  # noqa: S324 - upstream
    checksum_raw = (
        f"{JSON_MEMBER}：{publisher_md5}，演算法：MD5\n".encode("utf-8")
    )
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, raw in (("hash.txt", checksum_raw), (JSON_MEMBER, member_raw)):
            info = zipfile.ZipInfo(name, date_time=(2026, 7, 20, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, raw)
    return stream.getvalue()


class _FakeResponse:
    def __init__(
        self,
        raw: bytes,
        *,
        headers: dict[str, str] | None = None,
        final_url: str = MOENV_FULL_PACKAGE_URL,
        status: int = 200,
    ) -> None:
        self.raw = raw
        self.offset = 0
        self.headers = headers or {}
        self.final_url = final_url
        self.status = status
        self.closed = False
        self.read_sizes: list[int] = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.closed = True

    def geturl(self) -> str:
        return self.final_url

    def read(self, size: int = -1) -> bytes:
        self.read_sizes.append(size)
        if size < 0:
            size = len(self.raw) - self.offset
        chunk = self.raw[self.offset : self.offset + size]
        self.offset += len(chunk)
        return chunk


class _RecordingOpener:
    def __init__(self, response: _FakeResponse) -> None:
        self.response = response
        self.calls: list[tuple[object, float]] = []

    def __call__(self, request, *, timeout: float):
        self.calls.append((request, timeout))
        return self.response


class FetchMOENVTests(unittest.TestCase):
    def test_live_cli_posts_exact_body_and_installs_verified_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            temp_parent = root / "private-temporary"
            temp_parent.mkdir()
            output = root / "snapshot"
            raw = _archive_raw()
            response = _FakeResponse(
                raw,
                headers={
                    "Content-Length": str(len(raw)),
                    "ETag": '"fixture"',
                    "Last-Modified": "Sun, 19 Jul 2026 23:15:13 GMT",
                },
            )
            opener = _RecordingOpener(response)
            stdout = io.StringIO()
            stderr = io.StringIO()

            with (
                contextlib.redirect_stdout(stdout),
                contextlib.redirect_stderr(stderr),
            ):
                result = FETCH.main(
                    [
                        "--output-dir",
                        str(output),
                        "--dataset-updated-at",
                        DATASET_UPDATED_AT,
                        "--timeout",
                        "12.5",
                    ],
                    opener=opener,
                    wall_clock=lambda: COMPLETED_AT,
                    temporary_parent=temp_parent,
                )

            self.assertEqual(0, result, stderr.getvalue())
            self.assertEqual("", stderr.getvalue())
            self.assertTrue(response.closed)
            self.assertEqual([], list(temp_parent.iterdir()))
            self.assertEqual(1, len(opener.calls))
            request, timeout = opener.calls[0]
            self.assertEqual(12.5, timeout)
            self.assertEqual(MOENV_FULL_PACKAGE_URL, request.full_url)
            self.assertEqual("POST", request.get_method())
            self.assertEqual(FETCH.ACQUISITION_REQUEST_BYTES, request.data)
            self.assertEqual(
                {
                    "rid": [MOENV_RESOURCE_RID],
                    "download_type": "json",
                    "pid": MOENV_PACKAGE_PID,
                },
                json.loads(request.data),
            )
            self.assertEqual(
                FETCH.USER_AGENT,
                request.get_header("User-agent"),
            )
            self.assertEqual(
                "application/json; charset=utf-8",
                request.get_header("Content-type"),
            )
            self.assertIsNone(request.get_header("Authorization"))

            snapshot = verify_moenv_snapshot(output)
            self.assertEqual("2026-07-20T06:57:50Z", snapshot.retrieved_at)
            self.assertEqual(DATASET_UPDATED_AT, snapshot.dataset_updated_at)
            manifest = json.loads(snapshot.manifest_bytes)
            scope = manifest["source_scopes"][
                "moenv_ems_s_01_semiconductor_candidates"
            ]
            self.assertEqual('"fixture"', scope["upstream_archive"]["etag"])
            summary = json.loads(stdout.getvalue())
            self.assertEqual(snapshot.manifest_sha256, summary["manifest_sha256"])
            self.assertEqual(snapshot.raw_sha256, summary["raw_sha256"])
            self.assertEqual(1, summary["facility_count"])

    def test_verify_only_is_offline_and_rejects_acquisition_arguments(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "snapshot"
            opener = _RecordingOpener(_FakeResponse(_archive_raw()))
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(
                    0,
                    FETCH.main(
                        [
                            "--output-dir",
                            str(output),
                            "--dataset-updated-at",
                            DATASET_UPDATED_AT,
                        ],
                        opener=opener,
                        wall_clock=lambda: COMPLETED_AT,
                    ),
                )
            calls_after_acquisition = len(opener.calls)
            stdout = io.StringIO()
            stderr = io.StringIO()
            with (
                contextlib.redirect_stdout(stdout),
                contextlib.redirect_stderr(stderr),
            ):
                result = FETCH.main(
                    ["--verify-only", str(output)],
                    opener=lambda *_args, **_kwargs: self.fail("network used"),
                    wall_clock=lambda: self.fail("wall clock used"),
                )
            self.assertEqual(0, result, stderr.getvalue())
            self.assertEqual(calls_after_acquisition, len(opener.calls))
            self.assertEqual(
                verify_moenv_snapshot(output).manifest_sha256,
                json.loads(stdout.getvalue())["manifest_sha256"],
            )

            with contextlib.redirect_stderr(stderr):
                result = FETCH.main(
                    [
                        "--verify-only",
                        str(output),
                        "--dataset-updated-at",
                        DATASET_UPDATED_AT,
                    ]
                )
            self.assertEqual(1, result)

    def test_existing_output_is_rejected_before_network(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "existing"
            output.mkdir()
            opener = _RecordingOpener(_FakeResponse(_archive_raw()))
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                result = FETCH.main(
                    [
                        "--output-dir",
                        str(output),
                        "--dataset-updated-at",
                        DATASET_UPDATED_AT,
                    ],
                    opener=opener,
                    wall_clock=lambda: COMPLETED_AT,
                )
            self.assertEqual(1, result)
            self.assertIn("refusing to overwrite", stderr.getvalue())
            self.assertEqual([], opener.calls)

    def test_bounded_stream_removes_partial_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            private_file = root / "private.zip"
            self.assertEqual(
                3,
                FETCH._write_response_bounded(
                    _FakeResponse(b"zip"),
                    private_file,
                    max_bytes=10,
                ),
            )
            self.assertEqual(0o600, private_file.stat().st_mode & 0o777)

            destination = root / "download.zip"
            response = _FakeResponse(b"12345678901")
            with self.assertRaisesRegex(ValueError, "exceeds the byte limit"):
                FETCH._write_response_bounded(
                    response,
                    destination,
                    max_bytes=10,
                )
            self.assertFalse(destination.exists())
            self.assertLessEqual(max(response.read_sizes), 11)

    def test_bad_timestamp_unsafe_headers_and_http_errors_fail_cleanly(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            opener = _RecordingOpener(_FakeResponse(_archive_raw()))
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                result = FETCH.main(
                    [
                        "--output-dir",
                        str(root / "bad-time"),
                        "--dataset-updated-at",
                        "2026-07-19T23:15:13+00:00",
                    ],
                    opener=opener,
                )
            self.assertEqual(1, result)
            self.assertEqual([], opener.calls)
            self.assertFalse((root / "bad-time").exists())

            temp_parent = root / "temporary"
            temp_parent.mkdir()
            unsafe = _RecordingOpener(
                _FakeResponse(_archive_raw(), headers={"ETag": "bad\nvalue"})
            )
            with contextlib.redirect_stderr(stderr):
                result = FETCH.main(
                    [
                        "--output-dir",
                        str(root / "unsafe-header"),
                        "--dataset-updated-at",
                        DATASET_UPDATED_AT,
                    ],
                    opener=unsafe,
                    wall_clock=lambda: COMPLETED_AT,
                    temporary_parent=temp_parent,
                )
            self.assertEqual(1, result)
            self.assertEqual([], list(temp_parent.iterdir()))
            self.assertFalse((root / "unsafe-header").exists())

            def http_error(_request, *, timeout: float):
                self.assertEqual(FETCH.DEFAULT_TIMEOUT_SECONDS, timeout)
                raise HTTPError(
                    MOENV_FULL_PACKAGE_URL,
                    503,
                    "Service Unavailable",
                    hdrs=None,
                    fp=None,
                )

            with contextlib.redirect_stderr(stderr):
                result = FETCH.main(
                    [
                        "--output-dir",
                        str(root / "http-error"),
                        "--dataset-updated-at",
                        DATASET_UPDATED_AT,
                    ],
                    opener=http_error,
                    wall_clock=lambda: COMPLETED_AT,
                    temporary_parent=temp_parent,
                )
            self.assertEqual(1, result)
            self.assertEqual([], list(temp_parent.iterdir()))
            self.assertFalse((root / "http-error").exists())


if __name__ == "__main__":
    unittest.main()
