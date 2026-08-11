from __future__ import annotations

import contextlib
import csv
import importlib.util
import io
import json
import ssl
import sys
import tempfile
import unittest
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from unittest import mock
from urllib.error import HTTPError
from urllib.request import Request

from semiconductor_atlas.adapters.taiwan_factory_registry import (
    TAIWAN_FACTORY_ARCHIVE_URL,
    TAIWAN_FACTORY_FIELDS,
)
from semiconductor_atlas.taiwan_factory_snapshot import (
    TAIWAN_FACTORY_MAX_ARCHIVE_BYTES,
    TAIWAN_FACTORY_SCOPE,
    verify_taiwan_factory_snapshot,
)


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "fetch_taiwan_factory.py"
LAST_MODIFIED = "Mon, 20 Jul 2026 03:37:05 GMT"
COMPLETED_AT = datetime(2026, 7, 20, 6, 57, 50, tzinfo=UTC)


def _load_fetch_module():
    specification = importlib.util.spec_from_file_location(
        "_semiconductor_atlas_fetch_taiwan_factory_test", SCRIPT
    )
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)
    return module


FETCH = _load_fetch_module()


def _archive_raw() -> bytes:
    row = {
        "工廠名稱": "測試半導體股份有限公司一廠",
        "工廠登記編號": "12345678",
        "工廠設立許可案號": "TEST-CASE-1",
        "工廠地址": "新竹市東區測試路1號",
        "工廠市鎮鄉村里": "新竹市東區",
        "工廠負責人姓名": "不應出現在衍生資料的人名",
        "統一編號": "00123456",
        "工廠組織型態": "股份有限公司",
        "工廠設立核准日期": "2026072000000",
        "工廠登記核准日期": "2026072000000",
        "工廠登記狀態": "生產中",
        "產業類別": "26電子零組件製造業",
        "主要產品": "261半導體",
    }
    text = io.StringIO(newline="")
    writer = csv.DictWriter(
        text,
        fieldnames=TAIWAN_FACTORY_FIELDS,
        dialect="excel",
        lineterminator="\r\n",
    )
    writer.writeheader()
    writer.writerow(row)
    csv_raw = b"\xef\xbb\xbf" + text.getvalue().encode("utf-8")
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        info = zipfile.ZipInfo("11506.csv", date_time=(2026, 7, 20, 3, 37, 4))
        info.compress_type = zipfile.ZIP_DEFLATED
        info.external_attr = 0o100644 << 16
        archive.writestr(info, csv_raw)
    return stream.getvalue()


class _FakeResponse:
    def __init__(
        self,
        raw: bytes,
        *,
        headers: dict[str, str] | None = None,
        final_url: str = TAIWAN_FACTORY_ARCHIVE_URL,
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


def _response(raw: bytes | None = None, **overrides) -> _FakeResponse:
    body = _archive_raw() if raw is None else raw
    headers = {
        "Content-Length": str(len(body)),
        "Content-Type": "application/zip",
        "ETag": '"fixture"',
        "Last-Modified": LAST_MODIFIED,
    }
    headers.update(overrides.pop("headers", {}))
    return _FakeResponse(body, headers=headers, **overrides)


class FetchTaiwanFactoryTests(unittest.TestCase):
    def test_official_tls_context_clears_only_strict_mode_and_remains_verified(
        self,
    ) -> None:
        strict = getattr(ssl, "VERIFY_X509_STRICT", 0)
        context = ssl.create_default_context()
        context.verify_flags |= strict
        initial_flags = context.verify_flags

        with mock.patch.object(
            FETCH.ssl, "create_default_context", return_value=context
        ) as create_default:
            result = FETCH._official_ssl_context()

        self.assertIs(context, result)
        create_default.assert_called_once_with()
        self.assertEqual(initial_flags & ~strict, result.verify_flags)
        self.assertEqual(ssl.CERT_REQUIRED, result.verify_mode)
        self.assertTrue(result.check_hostname)

    def test_default_opener_uses_verified_context_only_for_fixed_get(self) -> None:
        response = object()
        request = Request(TAIWAN_FACTORY_ARCHIVE_URL, method="GET")
        with (
            mock.patch.object(
                FETCH.ssl, "create_default_context", wraps=ssl.create_default_context
            ),
            mock.patch.object(FETCH.ssl, "_create_unverified_context") as unverified,
            mock.patch.object(FETCH, "urlopen", return_value=response) as opened,
        ):
            self.assertIs(response, FETCH._official_urlopen(request, timeout=12.5))

        unverified.assert_not_called()
        context = opened.call_args.kwargs["context"]
        self.assertEqual(ssl.CERT_REQUIRED, context.verify_mode)
        self.assertTrue(context.check_hostname)
        opened.assert_called_once_with(request, timeout=12.5, context=context)

        with self.assertRaisesRegex(ValueError, "fixed factory URL"):
            FETCH._official_urlopen(
                Request("https://example.com/", method="GET"), timeout=1
            )
        with self.assertRaisesRegex(ValueError, "only GET"):
            FETCH._official_urlopen(
                Request(TAIWAN_FACTORY_ARCHIVE_URL, data=b"", method="POST"),
                timeout=1,
            )

    def test_live_cli_gets_exact_url_and_installs_verified_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            temp_parent = root / "private-temporary"
            temp_parent.mkdir()
            output = root / "snapshot"
            response = _response()
            opener = _RecordingOpener(response)
            stdout = io.StringIO()
            stderr = io.StringIO()

            with (
                contextlib.redirect_stdout(stdout),
                contextlib.redirect_stderr(stderr),
            ):
                result = FETCH.main(
                    ["--output-dir", str(output), "--timeout", "12.5"],
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
            self.assertEqual(TAIWAN_FACTORY_ARCHIVE_URL, request.full_url)
            self.assertEqual("GET", request.get_method())
            self.assertIsNone(request.data)
            self.assertEqual(FETCH.USER_AGENT, request.get_header("User-agent"))
            self.assertIsNone(request.get_header("Authorization"))
            self.assertIsNone(request.get_header("Cookie"))

            snapshot = verify_taiwan_factory_snapshot(output)
            self.assertEqual("2026-07-20T06:57:50Z", snapshot.retrieved_at)
            self.assertEqual("2026-07-20T03:37:05Z", snapshot.source_updated_at)
            manifest = json.loads(snapshot.manifest_bytes)
            metadata = manifest["source_scopes"][TAIWAN_FACTORY_SCOPE][
                "upstream_archive"
            ]["response_metadata"]
            self.assertEqual(
                {
                    "content_length": len(_archive_raw()),
                    "content_type": "application/zip",
                    "etag": '"fixture"',
                    "last_modified": LAST_MODIFIED,
                },
                metadata,
            )
            summary = json.loads(stdout.getvalue())
            self.assertEqual(snapshot.manifest_sha256, summary["manifest_sha256"])
            self.assertEqual(snapshot.raw_sha256, summary["raw_sha256"])
            self.assertEqual(1, summary["candidate_count"])

    def test_verify_only_is_offline_and_rejects_live_arguments(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "snapshot"
            opener = _RecordingOpener(_response())
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(
                    0,
                    FETCH.main(
                        ["--output-dir", str(output)],
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
                verify_taiwan_factory_snapshot(output).manifest_sha256,
                json.loads(stdout.getvalue())["manifest_sha256"],
            )

            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                result = FETCH.main(["--verify-only", str(output), "--timeout", "1"])
            self.assertEqual(1, result)
            self.assertIn("applies only", stderr.getvalue())

    def test_existing_output_is_rejected_before_network(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "existing"
            output.mkdir()
            opener = _RecordingOpener(_response())
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                result = FETCH.main(
                    ["--output-dir", str(output)],
                    opener=opener,
                    wall_clock=lambda: COMPLETED_AT,
                )
            self.assertEqual(1, result)
            self.assertIn("refusing to overwrite", stderr.getvalue())
            self.assertEqual([], opener.calls)

    def test_bounded_stream_enforces_fixed_cap_and_removes_partial_file(self) -> None:
        self.assertEqual(100_000_000, TAIWAN_FACTORY_MAX_ARCHIVE_BYTES)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            destination = root / "download.zip"
            response = _FakeResponse(b"12345678901", headers={})
            with self.assertRaisesRegex(ValueError, "100 MB"):
                FETCH._write_response_bounded(response, destination, max_bytes=10)
            self.assertFalse(destination.exists())
            self.assertLessEqual(max(response.read_sizes), 11)

            early = root / "early.zip"
            with self.assertRaisesRegex(ValueError, "100 MB"):
                FETCH._write_response_bounded(
                    _FakeResponse(b"x", headers={"Content-Length": "11"}),
                    early,
                    max_bytes=10,
                )
            self.assertFalse(early.exists())

            private_file = root / "private.zip"
            self.assertEqual(
                3,
                FETCH._write_response_bounded(
                    _FakeResponse(b"zip", headers={"Content-Length": "3"}),
                    private_file,
                    max_bytes=10,
                ),
            )
            self.assertEqual(0o600, private_file.stat().st_mode & 0o777)

            with self.assertRaisesRegex(ValueError, "at most 100 MB"):
                FETCH._write_response_bounded(
                    _FakeResponse(b"zip", headers={}),
                    root / "too-permissive.zip",
                    max_bytes=TAIWAN_FACTORY_MAX_ARCHIVE_BYTES + 1,
                )

    def test_bad_last_modified_headers_redirects_and_http_errors_fail_cleanly(
        self,
    ) -> None:
        cases = (
            ({"Last-Modified": ""}, "safe to retain"),
            ({"Last-Modified": "not-a-date"}, "valid HTTP date"),
            ({"ETag": "bad\nvalue"}, "safe to retain"),
            ({"Content-Length": "not-a-number"}, "ASCII integer"),
        )
        for headers, expected in cases:
            with (
                self.subTest(headers=headers),
                tempfile.TemporaryDirectory() as temporary,
            ):
                root = Path(temporary)
                temp_parent = root / "temporary"
                temp_parent.mkdir()
                opener = _RecordingOpener(_response(headers=headers))
                stderr = io.StringIO()
                with contextlib.redirect_stderr(stderr):
                    result = FETCH.main(
                        ["--output-dir", str(root / "snapshot")],
                        opener=opener,
                        wall_clock=lambda: COMPLETED_AT,
                        temporary_parent=temp_parent,
                    )
                self.assertEqual(1, result)
                self.assertIn(expected, stderr.getvalue())
                self.assertEqual([], list(temp_parent.iterdir()))
                self.assertFalse((root / "snapshot").exists())

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            opener = _RecordingOpener(
                _response(final_url=TAIWAN_FACTORY_ARCHIVE_URL + "?redirected=1")
            )
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                result = FETCH.main(
                    ["--output-dir", str(root / "redirect")],
                    opener=opener,
                    wall_clock=lambda: COMPLETED_AT,
                )
            self.assertEqual(1, result)
            self.assertIn("redirected", stderr.getvalue())

        def http_error(_request, *, timeout: float):
            self.assertEqual(FETCH.DEFAULT_TIMEOUT_SECONDS, timeout)
            raise HTTPError(
                TAIWAN_FACTORY_ARCHIVE_URL,
                503,
                "Service Unavailable",
                hdrs=None,
                fp=None,
            )

        with tempfile.TemporaryDirectory() as temporary:
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                result = FETCH.main(
                    ["--output-dir", str(Path(temporary) / "http-error")],
                    opener=http_error,
                    wall_clock=lambda: COMPLETED_AT,
                )
            self.assertEqual(1, result)
            self.assertIn("503", stderr.getvalue())

    def test_future_source_clock_and_bad_content_type_are_rejected(self) -> None:
        cases = (
            _response(headers={"Last-Modified": "Tue, 21 Jul 2026 03:37:05 GMT"}),
            _response(headers={"Content-Type": "text/html"}),
            _response(status=500),
        )
        for response in cases:
            with (
                self.subTest(response=response),
                tempfile.TemporaryDirectory() as temporary,
            ):
                output = Path(temporary) / "snapshot"
                stderr = io.StringIO()
                with contextlib.redirect_stderr(stderr):
                    result = FETCH.main(
                        ["--output-dir", str(output)],
                        opener=_RecordingOpener(response),
                        wall_clock=lambda: COMPLETED_AT,
                    )
                self.assertEqual(1, result)
                self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
