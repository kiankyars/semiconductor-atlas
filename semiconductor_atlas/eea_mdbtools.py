"""Fail-closed extraction of reviewed EEA ACCDB tables with pinned mdbtools."""

from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import stat
import subprocess
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO


EEA_MDBTOOLS_FORMAT = "semiconductor-atlas-eea-mdbtools-extraction-v1"
EEA_MDBTOOLS_VERSION = "mdbtools v1.0.1"
EEA_MDBTOOLS_PACKAGE_VERSION = "1.0.1-0.1"
EEA_MDBTOOLS_PLATFORM = "linux/arm64"
EEA_MDBTOOLS_NULL_SENTINEL = "__SEMICONDUCTOR_ATLAS_MDB_NULL_48c374a5__"
EEA_MDBTOOLS_DATE_FORMAT = "%Y-%m-%d"
EEA_MDBTOOLS_DATETIME_FORMAT = "%Y-%m-%dT%H:%M:%S"
EEA_MDBTOOLS_PACKAGE_MANIFEST_SHA256 = (
    "06bca70ff01f7d46cc165bf6c6ab617150466a0c348de1c075d23c70f44b152d"
)
EEA_MDBTOOLS_BINARY_MANIFEST_SHA256 = (
    "f20a40f80f827e04b62853fced25de7b9481ce023d7ed870041d7dac2771054f"
)

EEA_MDBTOOLS_MAX_ACCDB_BYTES = 4 * 1024 * 1024 * 1024
EEA_MDBTOOLS_MAX_EXPORT_BYTES = 16 * 1024 * 1024 * 1024
EEA_MDBTOOLS_MAX_TABLES = 256
EEA_MDBTOOLS_MAX_ROWS_PER_TABLE = 100_000_000

_CONTAINER_INPUT_PATH = "/input/source.accdb"
_CONTAINER_USER = "65534:65534"
_DOCKER_CPUS = 2
_DOCKER_MEMORY = "4g"
_DOCKER_MEMORY_BYTES = 4 * 1024 * 1024 * 1024
_DOCKER_PIDS_LIMIT = 64
_DOCKER_NOFILE_LIMIT = 256
_DOCKER_TMPFS_SIZE = "64m"
_COMMAND_TIMEOUT_SECONDS = 10 * 60
_EXPORT_TIMEOUT_SECONDS = 6 * 60 * 60
_MAX_STDERR_BYTES = 1024 * 1024
_MAX_SMALL_STDOUT_BYTES = 1024 * 1024
_MAX_INVENTORY_BYTES = 16 * 1024 * 1024
_MAX_SCHEMA_BYTES = 256 * 1024 * 1024
_READ_CHUNK_BYTES = 1024 * 1024
_IMAGE_ID_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_COUNT_RE = re.compile(rb"(?:0|[1-9][0-9]*)\n")
_DATABASE_FORMAT_RE = re.compile(rb"ACE(?:12|14|15|16|17|18|19)\n")
_FORBIDDEN_TABLE_PATTERN_CHARACTERS = frozenset("*?[]{}")

CommandRunner = Callable[..., subprocess.CompletedProcess[bytes]]


class EEAMDBToolsError(RuntimeError):
    """Raised when the pinned extractor cannot produce a verified result."""


@dataclass(frozen=True, slots=True)
class EEAMDBTableExport:
    name: str
    csv_path: Path
    csv_sha256: str
    csv_size: int
    row_count: int
    column_count: int
    null_field_count: int


@dataclass(frozen=True, slots=True)
class EEAMDBToolsExtraction:
    root: Path
    metadata_path: Path
    metadata_sha256: str
    input_sha256: str
    input_size: int
    database_format: str
    inventory_tables: tuple[str, ...]
    table_exports: tuple[EEAMDBTableExport, ...]


@dataclass(frozen=True, slots=True)
class _InputIdentity:
    device: int
    inode: int
    size: int
    modified_ns: int
    changed_ns: int
    sha256: str


@dataclass(frozen=True, slots=True)
class _CommandArtifacts:
    stdout_path: Path
    stderr_path: Path


@dataclass(frozen=True, slots=True)
class _CSVScan:
    row_count: int
    column_count: int
    null_field_count: int


def _canonical_json_bytes(value: object) -> bytes:
    return (
        json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(_READ_CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def _stat_signature(value: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


def _validate_accdb_path(value: str | os.PathLike[str]) -> tuple[Path, _InputIdentity]:
    path = Path(value)
    if not path.is_absolute():
        raise ValueError("ACCDB path must be absolute")
    if path.suffix.casefold() != ".accdb":
        raise ValueError("ACCDB path must have an .accdb extension")
    raw_path = os.fspath(path)
    if "," in raw_path or any(ord(character) < 0x20 for character in raw_path):
        raise ValueError("ACCDB path contains a character unsafe for a Docker mount")
    try:
        path_stat = path.lstat()
    except OSError as error:
        raise ValueError("ACCDB path must identify a readable regular file") from error
    if not stat.S_ISREG(path_stat.st_mode):
        raise ValueError("ACCDB path must identify a regular file, not a symlink")
    if path_stat.st_size <= 0 or path_stat.st_size > EEA_MDBTOOLS_MAX_ACCDB_BYTES:
        raise ValueError(
            "ACCDB file size must be between 1 byte and "
            f"{EEA_MDBTOOLS_MAX_ACCDB_BYTES} bytes"
        )

    flags = os.O_RDONLY
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise ValueError("ACCDB path must identify a readable regular file") from error
    digest = hashlib.sha256()
    try:
        opened_before = os.fstat(descriptor)
        if not stat.S_ISREG(opened_before.st_mode):
            raise ValueError("ACCDB path must identify a regular file")
        if _stat_signature(opened_before) != _stat_signature(path_stat):
            raise ValueError("ACCDB file changed while it was being opened")
        while chunk := os.read(descriptor, _READ_CHUNK_BYTES):
            digest.update(chunk)
        opened_after = os.fstat(descriptor)
        if _stat_signature(opened_after) != _stat_signature(opened_before):
            raise ValueError("ACCDB file changed while it was being hashed")
    finally:
        os.close(descriptor)
    return path, _InputIdentity(
        device=path_stat.st_dev,
        inode=path_stat.st_ino,
        size=path_stat.st_size,
        modified_ns=path_stat.st_mtime_ns,
        changed_ns=path_stat.st_ctime_ns,
        sha256=digest.hexdigest(),
    )


def _assert_input_unchanged(path: Path, identity: _InputIdentity) -> None:
    try:
        current = path.lstat()
    except OSError as error:
        raise EEAMDBToolsError("ACCDB file disappeared during extraction") from error
    expected = (
        identity.device,
        identity.inode,
        identity.size,
        identity.modified_ns,
        identity.changed_ns,
    )
    if not stat.S_ISREG(current.st_mode) or _stat_signature(current) != expected:
        raise EEAMDBToolsError("ACCDB file changed during extraction")


def _validate_output_directory(value: str | os.PathLike[str]) -> Path:
    path = Path(value)
    if not path.is_absolute():
        raise ValueError("output directory must be absolute")
    if os.path.lexists(path):
        raise FileExistsError(f"output directory already exists: {path}")
    try:
        parent_stat = path.parent.lstat()
    except OSError as error:
        raise ValueError("output directory parent must already exist") from error
    if not stat.S_ISDIR(parent_stat.st_mode):
        raise ValueError("output directory parent must be a non-symlink directory")
    return path


def _validate_allowed_tables(value: object) -> tuple[str, ...]:
    if type(value) is not tuple:
        raise ValueError("allowed_tables must be an immutable tuple")
    tables = value
    if not tables or len(tables) > EEA_MDBTOOLS_MAX_TABLES:
        raise ValueError(
            "allowed_tables must contain between 1 and "
            f"{EEA_MDBTOOLS_MAX_TABLES} exact names"
        )
    for table in tables:
        if not isinstance(table, str) or not table or table != table.strip():
            raise ValueError("allowed table names must be non-empty exact strings")
        if len(table.encode("utf-8")) > 255:
            raise ValueError("allowed table names must not exceed 255 UTF-8 bytes")
        if table.startswith("-"):
            raise ValueError("allowed table names must not look like command options")
        if any(character in table for character in _FORBIDDEN_TABLE_PATTERN_CHARACTERS):
            raise ValueError("allowed table names must not contain pattern characters")
        if "/" in table or "\\" in table:
            raise ValueError("allowed table names must not contain path separators")
        if any(unicodedata.category(character).startswith("C") for character in table):
            raise ValueError("allowed table names must not contain control characters")
    if len(set(tables)) != len(tables):
        raise ValueError("allowed table names must be unique")
    if tables != tuple(sorted(tables, key=lambda item: item.encode("utf-8"))):
        raise ValueError("allowed table names must be sorted by UTF-8 bytes")
    return tables


def _create_output_tree(root: Path) -> tuple[Path, Path, Path]:
    try:
        os.mkdir(root, 0o700)
    except FileExistsError:
        raise FileExistsError(f"output directory already exists: {root}") from None
    except OSError as error:
        raise EEAMDBToolsError(f"could not create output directory: {root}") from error
    runtime = root / "runtime"
    inventory = root / "inventory"
    tables = root / "tables"
    for directory in (runtime, inventory, tables):
        os.mkdir(directory, 0o700)
    return runtime, inventory, tables


def _open_new_binary(path: Path) -> BinaryIO:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o600)
    return os.fdopen(descriptor, "wb")


def _flush_and_sync(stream: BinaryIO) -> None:
    stream.flush()
    os.fsync(stream.fileno())


def _run_to_files(
    *,
    label: str,
    argv: tuple[str, ...],
    stdout_path: Path,
    stderr_path: Path,
    timeout_seconds: int,
    maximum_stdout_bytes: int,
    runner: CommandRunner,
) -> _CommandArtifacts:
    try:
        with (
            _open_new_binary(stdout_path) as stdout,
            _open_new_binary(stderr_path) as stderr,
        ):
            try:
                completed = runner(
                    argv,
                    stdin=subprocess.DEVNULL,
                    stdout=stdout,
                    stderr=stderr,
                    check=False,
                    timeout=timeout_seconds,
                )
            finally:
                _flush_and_sync(stdout)
                _flush_and_sync(stderr)
    except subprocess.TimeoutExpired as error:
        raise EEAMDBToolsError(
            f"{label} timed out after {timeout_seconds} seconds; stderr was retained"
        ) from error
    except OSError as error:
        raise EEAMDBToolsError(f"could not execute {label}") from error

    stdout_size = stdout_path.stat().st_size
    stderr_size = stderr_path.stat().st_size
    if stdout_size > maximum_stdout_bytes:
        raise EEAMDBToolsError(f"{label} stdout exceeded its byte limit")
    if stderr_size > _MAX_STDERR_BYTES:
        raise EEAMDBToolsError(f"{label} stderr exceeded its byte limit")
    returncode = getattr(completed, "returncode", None)
    if isinstance(returncode, bool) or not isinstance(returncode, int):
        raise EEAMDBToolsError(f"{label} runner returned no integer status")
    if returncode != 0:
        raise EEAMDBToolsError(
            f"{label} exited with status {returncode}; stderr was retained"
        )
    if stderr_size:
        raise EEAMDBToolsError(
            f"{label} emitted stderr; warnings are fatal and stderr was retained"
        )
    return _CommandArtifacts(stdout_path=stdout_path, stderr_path=stderr_path)


def _docker_run_argv(
    *,
    image_id: str,
    accdb_path: Path,
    command: tuple[str, ...],
) -> tuple[str, ...]:
    return (
        "docker",
        "run",
        "--rm",
        "--pull=never",
        "--platform=linux/arm64",
        "--network=none",
        "--read-only",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges:true",
        f"--pids-limit={_DOCKER_PIDS_LIMIT}",
        f"--memory={_DOCKER_MEMORY}",
        f"--memory-swap={_DOCKER_MEMORY}",
        f"--cpus={_DOCKER_CPUS}",
        f"--ulimit=nofile={_DOCKER_NOFILE_LIMIT}:{_DOCKER_NOFILE_LIMIT}",
        "--ulimit=core=0:0",
        "--log-driver=none",
        f"--user={_CONTAINER_USER}",
        "--workdir=/tmp",
        f"--tmpfs=/tmp:rw,noexec,nosuid,nodev,size={_DOCKER_TMPFS_SIZE}",
        "--env=LC_ALL=C.UTF-8",
        "--env=LANG=C.UTF-8",
        "--env=TZ=UTC",
        "--env=HOME=/tmp",
        (f"--mount=type=bind,src={accdb_path},dst={_CONTAINER_INPUT_PATH},readonly"),
        image_id,
        *command,
    )


def _run_container_command(
    *,
    label: str,
    command: tuple[str, ...],
    image_id: str,
    accdb_path: Path,
    identity: _InputIdentity,
    stdout_path: Path,
    stderr_path: Path,
    timeout_seconds: int,
    maximum_stdout_bytes: int,
    runner: CommandRunner,
) -> _CommandArtifacts:
    _assert_input_unchanged(accdb_path, identity)
    try:
        return _run_to_files(
            label=label,
            argv=_docker_run_argv(
                image_id=image_id,
                accdb_path=accdb_path,
                command=command,
            ),
            stdout_path=stdout_path,
            stderr_path=stderr_path,
            timeout_seconds=timeout_seconds,
            maximum_stdout_bytes=maximum_stdout_bytes,
            runner=runner,
        )
    finally:
        _assert_input_unchanged(accdb_path, identity)


def _read_bounded(path: Path, maximum_bytes: int, context: str) -> bytes:
    size = path.stat().st_size
    if size > maximum_bytes:
        raise EEAMDBToolsError(f"{context} exceeded its byte limit")
    raw = path.read_bytes()
    if len(raw) != size:
        raise EEAMDBToolsError(f"{context} changed while it was being read")
    return raw


def _parse_image_inspect(raw: bytes, image_id: str) -> None:
    expected = f"{image_id}\tlinux\tarm64\n".encode("ascii")
    if raw != expected:
        raise EEAMDBToolsError(
            "Docker image inspect did not return the exact requested linux/arm64 image ID"
        )


def _parse_inventory(raw: bytes) -> tuple[str, ...]:
    if not raw or not raw.endswith(b"\n") or b"\x00" in raw or b"\r" in raw:
        raise EEAMDBToolsError(
            "table inventory is empty, truncated, or not LF-delimited"
        )
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise EEAMDBToolsError("table inventory is not valid UTF-8") from error
    names = tuple(text[:-1].split("\n"))
    if any(
        not name
        or len(name.encode("utf-8")) > 1024
        or any(unicodedata.category(character).startswith("C") for character in name)
        for name in names
    ):
        raise EEAMDBToolsError("table inventory contains an invalid exact name")
    if len(set(names)) != len(names):
        raise EEAMDBToolsError("table inventory contains duplicate exact names")
    return names


def _validate_schema(raw: bytes) -> None:
    if not raw or not raw.endswith(b"\n") or b"\x00" in raw:
        raise EEAMDBToolsError("schema output is empty or truncated")
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise EEAMDBToolsError("schema output is not valid UTF-8") from error
    if "CREATE TABLE" not in text:
        raise EEAMDBToolsError("schema output contains no CREATE TABLE statement")


def _parse_count(raw: bytes, table: str) -> int:
    if _COUNT_RE.fullmatch(raw) is None:
        raise EEAMDBToolsError(
            f"mdb-count output for {table!r} is malformed or truncated"
        )
    count = int(raw[:-1])
    if count > EEA_MDBTOOLS_MAX_ROWS_PER_TABLE:
        raise EEAMDBToolsError(f"mdb-count for {table!r} exceeds the row limit")
    return count


def _scan_csv(path: Path, table: str) -> _CSVScan:
    size = path.stat().st_size
    if size <= 0 or size > EEA_MDBTOOLS_MAX_EXPORT_BYTES:
        raise EEAMDBToolsError(
            f"CSV export for {table!r} is empty or exceeds its limit"
        )
    with path.open("rb") as stream:
        stream.seek(-1, os.SEEK_END)
        if stream.read(1) != b"\n":
            raise EEAMDBToolsError(f"CSV export for {table!r} is truncated")

    try:
        with path.open("r", encoding="utf-8", errors="strict", newline="") as stream:
            reader = csv.reader(stream, dialect="excel", strict=True)
            try:
                header = next(reader)
            except StopIteration as error:
                raise EEAMDBToolsError(
                    f"CSV export for {table!r} has no header"
                ) from error
            if (
                not header
                or len(header) > 4096
                or any(
                    not column
                    or len(column.encode("utf-8")) > 1024
                    or any(
                        unicodedata.category(character).startswith("C")
                        for character in column
                    )
                    for column in header
                )
                or len(set(header)) != len(header)
            ):
                raise EEAMDBToolsError(
                    f"CSV export for {table!r} has an invalid header"
                )
            row_count = 0
            null_field_count = 0
            for row in reader:
                if len(row) != len(header):
                    raise EEAMDBToolsError(
                        f"CSV export for {table!r} has a non-rectangular row"
                    )
                row_count += 1
                if row_count > EEA_MDBTOOLS_MAX_ROWS_PER_TABLE:
                    raise EEAMDBToolsError(
                        f"CSV export for {table!r} exceeds the row limit"
                    )
                null_field_count += sum(
                    field == EEA_MDBTOOLS_NULL_SENTINEL for field in row
                )
    except (UnicodeDecodeError, csv.Error) as error:
        raise EEAMDBToolsError(
            f"CSV export for {table!r} is malformed or not valid UTF-8"
        ) from error
    return _CSVScan(
        row_count=row_count,
        column_count=len(header),
        null_field_count=null_field_count,
    )


def _artifact_metadata(path: Path, root: Path) -> dict[str, Any]:
    path_stat = path.lstat()
    if not stat.S_ISREG(path_stat.st_mode):
        raise EEAMDBToolsError(f"output artifact is not a regular file: {path}")
    return {
        "bytes": path_stat.st_size,
        "path": path.relative_to(root).as_posix(),
        "sha256": _sha256_file(path),
    }


def _write_new(path: Path, raw: bytes) -> None:
    with _open_new_binary(path) as stream:
        stream.write(raw)
        _flush_and_sync(stream)


def _table_artifact_stem(index: int, name: str) -> str:
    digest = hashlib.sha256(name.encode("utf-8")).hexdigest()[:16]
    return f"{index:03d}-{digest}"


def extract_eea_accdb(
    *,
    image_id: str,
    accdb_path: str | os.PathLike[str],
    output_directory: str | os.PathLike[str],
    allowed_tables: tuple[str, ...],
    command_runner: CommandRunner | None = None,
) -> EEAMDBToolsExtraction:
    """Export an exact reviewed table tuple into a new auditable directory.

    The caller must pin the adapter's reviewed table tuple and a full local Docker
    image ID. No network operation, image pull, shell, or writable input mount is
    used. Any stderr, non-zero exit, malformed output, or count mismatch fails the
    extraction; diagnostic stderr files remain in the new output directory.
    """

    if not isinstance(image_id, str) or _IMAGE_ID_RE.fullmatch(image_id) is None:
        raise ValueError("image_id must be a full lowercase local sha256 image ID")
    tables = _validate_allowed_tables(allowed_tables)
    input_path, identity = _validate_accdb_path(accdb_path)
    output_root = _validate_output_directory(output_directory)
    runner = command_runner or subprocess.run
    runtime_root, inventory_root, tables_root = _create_output_tree(output_root)

    inspect = _run_to_files(
        label="docker image inspect",
        argv=(
            "docker",
            "image",
            "inspect",
            "--format",
            "{{.Id}}\t{{.Os}}\t{{.Architecture}}",
            image_id,
        ),
        stdout_path=runtime_root / "docker-image-inspect.stdout.txt",
        stderr_path=runtime_root / "docker-image-inspect.stderr.txt",
        timeout_seconds=_COMMAND_TIMEOUT_SECONDS,
        maximum_stdout_bytes=_MAX_SMALL_STDOUT_BYTES,
        runner=runner,
    )
    _parse_image_inspect(
        _read_bounded(
            inspect.stdout_path,
            _MAX_SMALL_STDOUT_BYTES,
            "Docker image inspect stdout",
        ),
        image_id,
    )

    runtime_commands: dict[str, tuple[tuple[str, ...], _CommandArtifacts]] = {}
    for key, filename, command in (
        (
            "mdbtools_version",
            "mdbtools-version",
            ("mdb-export", "--version"),
        ),
        (
            "package_manifest",
            "packages",
            ("/bin/cat", "/opt/tool-manifest/packages.tsv"),
        ),
        (
            "binary_manifest",
            "binaries",
            ("/bin/cat", "/opt/tool-manifest/binaries.sha256"),
        ),
    ):
        artifacts = _run_container_command(
            label=key.replace("_", " "),
            command=command,
            image_id=image_id,
            accdb_path=input_path,
            identity=identity,
            stdout_path=runtime_root / f"{filename}.stdout.txt",
            stderr_path=runtime_root / f"{filename}.stderr.txt",
            timeout_seconds=_COMMAND_TIMEOUT_SECONDS,
            maximum_stdout_bytes=_MAX_SMALL_STDOUT_BYTES,
            runner=runner,
        )
        runtime_commands[key] = (command, artifacts)

    version_raw = _read_bounded(
        runtime_commands["mdbtools_version"][1].stdout_path,
        _MAX_SMALL_STDOUT_BYTES,
        "mdbtools version stdout",
    )
    if version_raw != f"{EEA_MDBTOOLS_VERSION}\n".encode("ascii"):
        raise EEAMDBToolsError("container does not provide the pinned mdbtools version")
    package_manifest = runtime_commands["package_manifest"][1].stdout_path
    binary_manifest = runtime_commands["binary_manifest"][1].stdout_path
    if _sha256_file(package_manifest) != EEA_MDBTOOLS_PACKAGE_MANIFEST_SHA256:
        raise EEAMDBToolsError(
            "container package manifest does not match the proven image"
        )
    if _sha256_file(binary_manifest) != EEA_MDBTOOLS_BINARY_MANIFEST_SHA256:
        raise EEAMDBToolsError(
            "container binary manifest does not match the proven image"
        )

    inventory_commands: dict[str, tuple[tuple[str, ...], _CommandArtifacts]] = {}
    for key, stdout_name, command, maximum in (
        (
            "database_format",
            "database-format.stdout.txt",
            ("mdb-ver", _CONTAINER_INPUT_PATH),
            _MAX_SMALL_STDOUT_BYTES,
        ),
        (
            "tables",
            "tables.stdout.txt",
            ("mdb-tables", "--single-column", "--type=table", _CONTAINER_INPUT_PATH),
            _MAX_INVENTORY_BYTES,
        ),
        (
            "schema",
            "schema.access.sql",
            ("mdb-schema", _CONTAINER_INPUT_PATH, "access"),
            _MAX_SCHEMA_BYTES,
        ),
    ):
        stderr_name = stdout_name.rsplit(".", 2)[0] + ".stderr.txt"
        artifacts = _run_container_command(
            label=f"mdb {key}",
            command=command,
            image_id=image_id,
            accdb_path=input_path,
            identity=identity,
            stdout_path=inventory_root / stdout_name,
            stderr_path=inventory_root / stderr_name,
            timeout_seconds=_COMMAND_TIMEOUT_SECONDS,
            maximum_stdout_bytes=maximum,
            runner=runner,
        )
        inventory_commands[key] = (command, artifacts)

    database_format_raw = _read_bounded(
        inventory_commands["database_format"][1].stdout_path,
        _MAX_SMALL_STDOUT_BYTES,
        "database format stdout",
    )
    if _DATABASE_FORMAT_RE.fullmatch(database_format_raw) is None:
        raise EEAMDBToolsError("ACCDB database format is unsupported or malformed")
    database_format = database_format_raw[:-1].decode("ascii")
    inventory_raw = _read_bounded(
        inventory_commands["tables"][1].stdout_path,
        _MAX_INVENTORY_BYTES,
        "table inventory stdout",
    )
    inventory_tables = _parse_inventory(inventory_raw)
    missing_tables = sorted(set(tables) - set(inventory_tables))
    if missing_tables:
        raise EEAMDBToolsError(
            f"allowed tables are absent from the exact inventory: {missing_tables!r}"
        )
    schema_raw = _read_bounded(
        inventory_commands["schema"][1].stdout_path,
        _MAX_SCHEMA_BYTES,
        "schema stdout",
    )
    _validate_schema(schema_raw)

    exported: list[
        tuple[
            EEAMDBTableExport,
            tuple[str, ...],
            _CommandArtifacts,
            tuple[str, ...],
            _CommandArtifacts,
        ]
    ] = []
    for index, table in enumerate(tables):
        stem = _table_artifact_stem(index, table)
        count_command = ("mdb-count", _CONTAINER_INPUT_PATH, table)
        count_artifacts = _run_container_command(
            label=f"mdb-count for {table!r}",
            command=count_command,
            image_id=image_id,
            accdb_path=input_path,
            identity=identity,
            stdout_path=tables_root / f"{stem}.count.stdout.txt",
            stderr_path=tables_root / f"{stem}.count.stderr.txt",
            timeout_seconds=_COMMAND_TIMEOUT_SECONDS,
            maximum_stdout_bytes=_MAX_SMALL_STDOUT_BYTES,
            runner=runner,
        )
        expected_rows = _parse_count(
            _read_bounded(
                count_artifacts.stdout_path,
                _MAX_SMALL_STDOUT_BYTES,
                f"mdb-count stdout for {table!r}",
            ),
            table,
        )
        export_command = (
            "mdb-export",
            f"--date-format={EEA_MDBTOOLS_DATE_FORMAT}",
            f"--datetime-format={EEA_MDBTOOLS_DATETIME_FORMAT}",
            f"--null={EEA_MDBTOOLS_NULL_SENTINEL}",
            "--bin=hex",
            _CONTAINER_INPUT_PATH,
            table,
        )
        export_artifacts = _run_container_command(
            label=f"mdb-export for {table!r}",
            command=export_command,
            image_id=image_id,
            accdb_path=input_path,
            identity=identity,
            stdout_path=tables_root / f"{stem}.csv",
            stderr_path=tables_root / f"{stem}.export.stderr.txt",
            timeout_seconds=_EXPORT_TIMEOUT_SECONDS,
            maximum_stdout_bytes=EEA_MDBTOOLS_MAX_EXPORT_BYTES,
            runner=runner,
        )
        scan = _scan_csv(export_artifacts.stdout_path, table)
        if scan.row_count != expected_rows:
            raise EEAMDBToolsError(
                f"CSV parser counted {scan.row_count} rows for {table!r}; "
                f"mdb-count reported {expected_rows}"
            )
        export = EEAMDBTableExport(
            name=table,
            csv_path=export_artifacts.stdout_path,
            csv_sha256=_sha256_file(export_artifacts.stdout_path),
            csv_size=export_artifacts.stdout_path.stat().st_size,
            row_count=scan.row_count,
            column_count=scan.column_count,
            null_field_count=scan.null_field_count,
        )
        exported.append(
            (
                export,
                count_command,
                count_artifacts,
                export_command,
                export_artifacts,
            )
        )

    _assert_input_unchanged(input_path, identity)
    allowlist_raw = "".join(f"{table}\n" for table in tables).encode("utf-8")
    metadata: dict[str, Any] = {
        "allowed_tables": {
            "count": len(tables),
            "names": list(tables),
            "sha256": hashlib.sha256(allowlist_raw).hexdigest(),
        },
        "database_format": database_format,
        "execution_policy": {
            "capabilities": "none",
            "cpus": _DOCKER_CPUS,
            "export_timeout_seconds": _EXPORT_TIMEOUT_SECONDS,
            "image_pull": "never",
            "memory_bytes": _DOCKER_MEMORY_BYTES,
            "memory_swap_bytes": _DOCKER_MEMORY_BYTES,
            "network": "none",
            "no_new_privileges": True,
            "nofile_limit": _DOCKER_NOFILE_LIMIT,
            "pids_limit": _DOCKER_PIDS_LIMIT,
            "read_only_input": True,
            "read_only_root": True,
            "standard_timeout_seconds": _COMMAND_TIMEOUT_SECONDS,
            "tmpfs_bytes": 64 * 1024 * 1024,
            "user": _CONTAINER_USER,
        },
        "format": EEA_MDBTOOLS_FORMAT,
        "input": {
            "bytes": identity.size,
            "filename": input_path.name,
            "sha256": identity.sha256,
        },
        "inventory": {
            "database_format": {
                "command": list(inventory_commands["database_format"][0]),
                "stderr": _artifact_metadata(
                    inventory_commands["database_format"][1].stderr_path,
                    output_root,
                ),
                "stdout": _artifact_metadata(
                    inventory_commands["database_format"][1].stdout_path,
                    output_root,
                ),
            },
            "schema": {
                "command": list(inventory_commands["schema"][0]),
                "stderr": _artifact_metadata(
                    inventory_commands["schema"][1].stderr_path,
                    output_root,
                ),
                "stdout": _artifact_metadata(
                    inventory_commands["schema"][1].stdout_path,
                    output_root,
                ),
            },
            "tables": {
                "command": list(inventory_commands["tables"][0]),
                "count": len(inventory_tables),
                "names": sorted(
                    inventory_tables, key=lambda item: item.encode("utf-8")
                ),
                "stderr": _artifact_metadata(
                    inventory_commands["tables"][1].stderr_path,
                    output_root,
                ),
                "stdout": _artifact_metadata(
                    inventory_commands["tables"][1].stdout_path,
                    output_root,
                ),
            },
        },
        "serialization": {
            "binary_format": "hex",
            "csv_dialect": "mdb-export_v1.0.1_defaults",
            "csv_delimiter": ",",
            "csv_encoding": "utf-8",
            "csv_escape": "double_quote",
            "csv_header": True,
            "csv_quote": '"',
            "csv_row_delimiter": "LF",
            "date_format": EEA_MDBTOOLS_DATE_FORMAT,
            "datetime_format": EEA_MDBTOOLS_DATETIME_FORMAT,
            "null_sentinel": EEA_MDBTOOLS_NULL_SENTINEL,
        },
        "tables": [],
        "toolchain": {
            "binary_manifest_expected_sha256": EEA_MDBTOOLS_BINARY_MANIFEST_SHA256,
            "container_architecture": "arm64",
            "container_image_id": image_id,
            "container_os": "linux",
            "mdbtools_package_version": EEA_MDBTOOLS_PACKAGE_VERSION,
            "mdbtools_version": EEA_MDBTOOLS_VERSION,
            "package_manifest_expected_sha256": EEA_MDBTOOLS_PACKAGE_MANIFEST_SHA256,
            "runtime_evidence": {
                key: {
                    "command": list(command),
                    "stderr": _artifact_metadata(artifacts.stderr_path, output_root),
                    "stdout": _artifact_metadata(artifacts.stdout_path, output_root),
                }
                for key, (command, artifacts) in sorted(runtime_commands.items())
            },
            "image_inspect": {
                "command": [
                    "docker",
                    "image",
                    "inspect",
                    "--format",
                    "{{.Id}}\t{{.Os}}\t{{.Architecture}}",
                    image_id,
                ],
                "stderr": _artifact_metadata(inspect.stderr_path, output_root),
                "stdout": _artifact_metadata(inspect.stdout_path, output_root),
            },
        },
    }
    table_metadata: list[dict[str, Any]] = []
    for (
        export,
        count_command,
        count_artifacts,
        export_command,
        export_artifacts,
    ) in exported:
        table_metadata.append(
            {
                "column_count": export.column_count,
                "count": {
                    "command": list(count_command),
                    "stderr": _artifact_metadata(
                        count_artifacts.stderr_path, output_root
                    ),
                    "stdout": _artifact_metadata(
                        count_artifacts.stdout_path, output_root
                    ),
                },
                "csv": _artifact_metadata(export.csv_path, output_root),
                "export": {
                    "command": list(export_command),
                    "stderr": _artifact_metadata(
                        export_artifacts.stderr_path, output_root
                    ),
                },
                "name": export.name,
                "null_field_count": export.null_field_count,
                "row_count": export.row_count,
            }
        )
    metadata["tables"] = table_metadata
    metadata_raw = _canonical_json_bytes(metadata)
    metadata_path = output_root / "metadata.json"
    _write_new(metadata_path, metadata_raw)
    return EEAMDBToolsExtraction(
        root=output_root,
        metadata_path=metadata_path,
        metadata_sha256=hashlib.sha256(metadata_raw).hexdigest(),
        input_sha256=identity.sha256,
        input_size=identity.size,
        database_format=database_format,
        inventory_tables=inventory_tables,
        table_exports=tuple(item[0] for item in exported),
    )
