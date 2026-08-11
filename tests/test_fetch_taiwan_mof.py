from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import ssl
import sys
import tempfile
import unittest
from datetime import UTC, datetime
from email.message import Message
from pathlib import Path
from unittest import mock
from urllib.error import HTTPError
from urllib.request import HTTPHandler, Request, build_opener
from urllib.response import addinfourl

from semiconductor_atlas.adapters.taiwan_mof_tax_registry import (
    TAIWAN_MOF_ARCHIVE_URL,
    TAIWAN_MOF_MAX_ARCHIVE_BYTES,
)
from semiconductor_atlas.taiwan_mof_snapshot import (
    TAIWAN_MOF_SCOPE,
    verify_taiwan_mof_snapshot,
)

from tests._taiwan_mof_fixtures import (
    MOF_LAST_MODIFIED,
    create_source_snapshots,
    mof_archive_raw,
)


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "fetch_taiwan_mof.py"
COMPLETED_AT = datetime(2026, 7, 20, 6, 57, 50, tzinfo=UTC)


def _load_fetch_module():
    specification = importlib.util.spec_from_file_location(
        "_semiconductor_atlas_fetch_taiwan_mof_test", SCRIPT
    )
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)
    return module


FETCH = _load_fetch_module()


class _FakeResponse:
    def __init__(
        self,
        raw: bytes,
        *,
        headers: dict[str, str] | None = None,
        final_url: str = TAIWAN_MOF_ARCHIVE_URL,
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
    body = mof_archive_raw() if raw is None else raw
    headers = {
        "Content-Length": str(len(body)),
        "Content-Type": "application/zip",
        "ETag": 'W/"fixture"',
        "Last-Modified": MOF_LAST_MODIFIED,
    }
    headers.update(overrides.pop("headers", {}))
    return _FakeResponse(body, headers=headers, **overrides)


class FetchTaiwanMOFTests(unittest.TestCase):
    def test_official_tls_context_is_verified_and_endpoint_restricted(self) -> None:
        strict = getattr(ssl, "VERIFY_X509_STRICT", 0)
        context = ssl.create_default_context()
        context.verify_flags |= strict
        initial_flags = context.verify_flags
        with mock.patch.object(
            FETCH.ssl, "create_default_context", return_value=context
        ):
            result = FETCH._official_ssl_context()
        self.assertEqual(initial_flags & ~strict, result.verify_flags)
        self.assertEqual(ssl.CERT_REQUIRED, result.verify_mode)
        self.assertTrue(result.check_hostname)

        request = Request(TAIWAN_MOF_ARCHIVE_URL, method="GET")
        response = object()
        opener = mock.Mock()
        opener.open.return_value = response
        with (
            mock.patch.object(
                FETCH.ssl, "create_default_context", wraps=ssl.create_default_context
            ),
            mock.patch.object(FETCH.ssl, "_create_unverified_context") as unverified,
            mock.patch.object(FETCH, "build_opener", return_value=opener) as built,
        ):
            self.assertIs(response, FETCH._official_urlopen(request, timeout=12.5))
        unverified.assert_not_called()
        opener.open.assert_called_once_with(request, timeout=12.5)
        handlers = built.call_args.args
        https_handler = next(
            handler for handler in handlers if isinstance(handler, FETCH.HTTPSHandler)
        )
        self.assertTrue(
            any(
                isinstance(handler, FETCH._RejectRedirectHandler)
                for handler in handlers
            )
        )
        tls_context = https_handler._context
        self.assertEqual(ssl.CERT_REQUIRED, tls_context.verify_mode)
        self.assertTrue(tls_context.check_hostname)

        with self.assertRaisesRegex(ValueError, "fixed MOF archive URL"):
            FETCH._official_urlopen(
                Request("https://example.com/", method="GET"), timeout=1
            )
        with self.assertRaisesRegex(ValueError, "only GET"):
            FETCH._official_urlopen(
                Request(TAIWAN_MOF_ARCHIVE_URL, data=b"", method="POST"), timeout=1
            )

    def test_official_opener_rejects_redirect_before_destination_request(self) -> None:
        source_url = "http://official.test/BGMOPEN1.zip"
        redirected_url = "http://redirected.test/private-destination"
        contacted: list[str] = []

        class SyntheticHTTPHandler(HTTPHandler):
            def http_open(self, request):
                contacted.append(request.full_url)
                if request.full_url != source_url:
                    raise AssertionError("redirect destination was contacted")
                headers = Message()
                headers["Location"] = redirected_url
                response = addinfourl(
                    io.BytesIO(b"redirect body"),
                    headers,
                    source_url,
                    code=302,
                )
                response.msg = "Found"
                return response

        def synthetic_build_opener(*handlers):
            return build_opener(*handlers, SyntheticHTTPHandler())

        with (
            mock.patch.object(FETCH, "TAIWAN_MOF_ARCHIVE_URL", source_url),
            mock.patch.object(
                FETCH, "build_opener", side_effect=synthetic_build_opener
            ),
            self.assertRaisesRegex(ValueError, "redirects are forbidden"),
        ):
            FETCH._official_urlopen(Request(source_url, method="GET"), timeout=1)
        self.assertEqual([source_url], contacted)

    def test_live_cli_gets_exact_url_and_installs_source_bound_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            moenv, factory = create_source_snapshots(root)
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
                    [
                        "--output-dir",
                        str(output),
                        "--moenv-snapshot",
                        str(moenv),
                        "--factory-snapshot",
                        str(factory),
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
            self.assertEqual(TAIWAN_MOF_ARCHIVE_URL, request.full_url)
            self.assertEqual("GET", request.get_method())
            self.assertIsNone(request.data)
            self.assertEqual(FETCH.USER_AGENT, request.get_header("User-agent"))
            self.assertIsNone(request.get_header("Authorization"))
            self.assertIsNone(request.get_header("Cookie"))

            snapshot = verify_taiwan_mof_snapshot(output)
            self.assertEqual("2026-07-20T06:57:50Z", snapshot.retrieved_at)
            self.assertEqual("2026-07-19T21:12:27Z", snapshot.source_updated_at)
            self.assertEqual("2026-07-20", snapshot.publisher_date)
            manifest = json.loads(snapshot.manifest_bytes)
            metadata = manifest["source_scopes"][TAIWAN_MOF_SCOPE]["upstream_archive"][
                "response_metadata"
            ]
            self.assertEqual(
                {
                    "content_length": len(mof_archive_raw()),
                    "content_type": "application/zip",
                    "etag": 'W/"fixture"',
                    "last_modified": MOF_LAST_MODIFIED,
                },
                metadata,
            )
            summary = json.loads(stdout.getvalue())
            self.assertEqual(snapshot.manifest_sha256, summary["manifest_sha256"])
            self.assertEqual(3, summary["allowlist_count"])
            self.assertEqual(2, summary["matched_count"])
            self.assertEqual(1, summary["missing_count"])

    def test_verify_only_is_offline_and_rejects_live_arguments(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            moenv, factory = create_source_snapshots(root)
            output = root / "snapshot"
            opener = _RecordingOpener(_response())
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(
                    0,
                    FETCH.main(
                        [
                            "--output-dir",
                            str(output),
                            "--moenv-snapshot",
                            str(moenv),
                            "--factory-snapshot",
                            str(factory),
                        ],
                        opener=opener,
                        wall_clock=lambda: COMPLETED_AT,
                    ),
                )
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
            self.assertEqual(
                verify_taiwan_mof_snapshot(output).manifest_sha256,
                json.loads(stdout.getvalue())["manifest_sha256"],
            )

            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                result = FETCH.main(
                    ["--verify-only", str(output), "--moenv-snapshot", str(moenv)]
                )
            self.assertEqual(1, result)
            self.assertIn("apply only", stderr.getvalue())

    def test_requires_both_source_snapshots_before_network(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            opener = _RecordingOpener(_response())
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                result = FETCH.main(
                    ["--output-dir", str(Path(temporary) / "snapshot")], opener=opener
                )
            self.assertEqual(1, result)
            self.assertIn("are required", stderr.getvalue())
            self.assertEqual([], opener.calls)

    def test_existing_output_is_rejected_before_network(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            moenv, factory = create_source_snapshots(root)
            output = root / "existing"
            output.mkdir()
            opener = _RecordingOpener(_response())
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                result = FETCH.main(
                    [
                        "--output-dir",
                        str(output),
                        "--moenv-snapshot",
                        str(moenv),
                        "--factory-snapshot",
                        str(factory),
                    ],
                    opener=opener,
                )
            self.assertEqual(1, result)
            self.assertIn("refusing to overwrite", stderr.getvalue())
            self.assertEqual([], opener.calls)

    def test_bounded_stream_enforces_100mb_ceiling_and_cleans_partial_file(
        self,
    ) -> None:
        self.assertEqual(100_000_000, TAIWAN_MOF_MAX_ARCHIVE_BYTES)
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
                FETCH.acquire_taiwan_mof_snapshot(
                    root / "never",
                    moenv_snapshot_dir=root / "missing-moenv",
                    factory_snapshot_dir=root / "missing-factory",
                    max_download_bytes=TAIWAN_MOF_MAX_ARCHIVE_BYTES + 1,
                    opener=lambda *_args, **_kwargs: self.fail("network used"),
                )

    def test_bad_headers_redirect_status_and_http_errors_fail_cleanly(self) -> None:
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
                moenv, factory = create_source_snapshots(root)
                temp_parent = root / "temporary"
                temp_parent.mkdir()
                stderr = io.StringIO()
                with contextlib.redirect_stderr(stderr):
                    result = FETCH.main(
                        [
                            "--output-dir",
                            str(root / "snapshot"),
                            "--moenv-snapshot",
                            str(moenv),
                            "--factory-snapshot",
                            str(factory),
                        ],
                        opener=_RecordingOpener(_response(headers=headers)),
                        wall_clock=lambda: COMPLETED_AT,
                        temporary_parent=temp_parent,
                    )
                self.assertEqual(1, result)
                self.assertIn(expected, stderr.getvalue())
                self.assertEqual([], list(temp_parent.iterdir()))

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            moenv, factory = create_source_snapshots(root)
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                result = FETCH.main(
                    [
                        "--output-dir",
                        str(root / "redirect"),
                        "--moenv-snapshot",
                        str(moenv),
                        "--factory-snapshot",
                        str(factory),
                    ],
                    opener=_RecordingOpener(
                        _response(final_url=TAIWAN_MOF_ARCHIVE_URL + "?redirected=1")
                    ),
                    wall_clock=lambda: COMPLETED_AT,
                )
            self.assertEqual(1, result)
            self.assertIn("redirected", stderr.getvalue())

        def http_error(_request, *, timeout: float):
            self.assertEqual(FETCH.DEFAULT_TIMEOUT_SECONDS, timeout)
            raise HTTPError(
                TAIWAN_MOF_ARCHIVE_URL,
                503,
                "Service Unavailable",
                hdrs=None,
                fp=None,
            )

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            moenv, factory = create_source_snapshots(root)
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                result = FETCH.main(
                    [
                        "--output-dir",
                        str(root / "http-error"),
                        "--moenv-snapshot",
                        str(moenv),
                        "--factory-snapshot",
                        str(factory),
                    ],
                    opener=http_error,
                )
            self.assertEqual(1, result)
            self.assertIn("503", stderr.getvalue())

    def test_future_source_clock_bad_content_type_and_status_are_rejected(self) -> None:
        responses = (
            _response(headers={"Last-Modified": "Tue, 21 Jul 2026 03:37:05 GMT"}),
            _response(headers={"Content-Type": "text/html"}),
            _response(status=500),
        )
        for response in responses:
            with (
                self.subTest(response=response),
                tempfile.TemporaryDirectory() as temporary,
            ):
                root = Path(temporary)
                moenv, factory = create_source_snapshots(root)
                output = root / "snapshot"
                stderr = io.StringIO()
                with contextlib.redirect_stderr(stderr):
                    result = FETCH.main(
                        [
                            "--output-dir",
                            str(output),
                            "--moenv-snapshot",
                            str(moenv),
                            "--factory-snapshot",
                            str(factory),
                        ],
                        opener=_RecordingOpener(response),
                        wall_clock=lambda: COMPLETED_AT,
                    )
                self.assertEqual(1, result)
                self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
