from __future__ import annotations

import csv
import hashlib
import io
import json
import zipfile
from pathlib import Path

from semiconductor_atlas.adapters.moenv_ems import MOENV_FIELDS
from semiconductor_atlas.adapters.taiwan_factory_registry import TAIWAN_FACTORY_FIELDS
from semiconductor_atlas.adapters.taiwan_mof_tax_registry import TAIWAN_MOF_FIELDS
from semiconductor_atlas.moenv_snapshot import (
    MOENV_FULL_PACKAGE_URL,
    MOENV_PACKAGE_PID,
    MOENV_RESOURCE_RID,
    create_moenv_snapshot,
)
from semiconductor_atlas.taiwan_factory_snapshot import create_taiwan_factory_snapshot


RETRIEVED_AT = "2026-07-20T06:57:50Z"
MOENV_UPDATED_AT = "2026-07-19T23:15:13Z"
FACTORY_LAST_MODIFIED = "Mon, 20 Jul 2026 03:37:05 GMT"
MOF_LAST_MODIFIED = "Sun, 19 Jul 2026 21:12:27 GMT"
MOENV_MEMBER = "環境保護許可管理系統(暨解除列管)對象基本資料.json"


def _moenv_row(emsno: str, uniformno: str, *, name: str) -> dict[str, object]:
    row: dict[str, object] = {field: "" for field in MOENV_FIELDS}
    row.update(
        {
            "emsno": emsno,
            "facilityname": name,
            "uniformno": uniformno,
            "county": "新竹市",
            "township": "東區",
            "facilityaddress": "新竹市東區測試路1號",
            "industryid": "2611",
            "industryname": "積體電路製造業",
            "industrygroup": "261",
            "wgs84lon": "121.0123",
            "wgs84lat": "24.8004",
            "isair": "1",
            "iswater": "0",
            "iswaste": "0",
            "istoxic": "0",
            "issoil": "0",
            "admino": None,
            "facno": None,
        }
    )
    return row


def moenv_archive_raw() -> bytes:
    rows = [
        _moenv_row("A0000001", "00123456", name="允許公司"),
        _moenv_row("H43F3888", "1234567", name="衝突公司"),
        _moenv_row("H43F3888", "00999999", name="衝突公司"),
        _moenv_row("B0000002", "", name="空白公司"),
    ]
    member_raw = json.dumps(rows, ensure_ascii=False, separators=(",", ":")).encode(
        "utf-8"
    )
    publisher_md5 = hashlib.md5(member_raw).hexdigest()  # noqa: S324 - upstream
    checksum_raw = f"{MOENV_MEMBER}：{publisher_md5}，演算法：MD5\n".encode()
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, raw in (("hash.txt", checksum_raw), (MOENV_MEMBER, member_raw)):
            info = zipfile.ZipInfo(name, date_time=(2026, 7, 20, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, raw)
    return stream.getvalue()


def _factory_row(
    registration_number: str, business_number: str, name: str
) -> dict[str, str]:
    return {
        "工廠名稱": name,
        "工廠登記編號": registration_number,
        "工廠設立許可案號": "",
        "工廠地址": "新竹市東區測試路1號",
        "工廠市鎮鄉村里": "新竹市東區",
        "工廠負責人姓名": "不保留的人名",
        "統一編號": business_number,
        "工廠組織型態": "股份有限公司",
        "工廠設立核准日期": "",
        "工廠登記核准日期": "1010913000000",
        "工廠登記狀態": "生產中",
        "產業類別": "26電子零組件製造業",
        "主要產品": "261半導體",
    }


def factory_archive_raw() -> bytes:
    text = io.StringIO(newline="")
    writer = csv.DictWriter(
        text, fieldnames=TAIWAN_FACTORY_FIELDS, lineterminator="\r\n"
    )
    writer.writeheader()
    writer.writerow(_factory_row("12345678", "00888888", "工廠公司"))
    writer.writerow(_factory_row("87654321", "00999999", "衝突值獨立來源"))
    csv_raw = b"\xef\xbb\xbf" + text.getvalue().encode()
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        info = zipfile.ZipInfo("11506.csv", date_time=(2026, 7, 20, 3, 37, 4))
        info.compress_type = zipfile.ZIP_DEFLATED
        info.external_attr = 0o100644 << 16
        archive.writestr(info, csv_raw)
    return stream.getvalue()


def _mof_row(
    ubn: str,
    name: str,
    organization_type: str,
    *,
    head_office: str = "",
) -> list[str]:
    row = {field: "" for field in TAIWAN_MOF_FIELDS}
    row.update(
        {
            "營業地址": "新竹市東區測試路1號",
            "統一編號": ubn,
            "總機構統一編號": head_office,
            "營業人名稱": name,
            "資本額": "1000000",
            "設立日期": "1100101",
            "組織別名稱": organization_type,
            "使用統一發票": "Y",
            "行業代號": "261100",
            "名稱": "積體電路製造",
        }
    )
    return [row[field] for field in TAIWAN_MOF_FIELDS]


def mof_archive_raw(
    *, duplicate: bool = False, publisher_date: str = "20-JUL-26"
) -> bytes:
    text = io.StringIO(newline="")
    writer = csv.writer(text, lineterminator="\r\n")
    writer.writerow(TAIWAN_MOF_FIELDS)
    writer.writerow([publisher_date, *("" for _ in range(15))])
    writer.writerow(_mof_row("00123456", "允許公司", "股份有限公司"))
    writer.writerow(
        _mof_row(
            "00888888",
            "允許分公司",
            "本國公司設立之分公司",
            head_office="00123456",
        )
    )
    writer.writerow(_mof_row("00777777", "非允許公司", "有限公司"))
    if duplicate:
        writer.writerow(_mof_row("00123456", "重複公司", "股份有限公司"))
    csv_raw = b"\xef\xbb\xbf" + text.getvalue().encode()
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        info = zipfile.ZipInfo("BGMOPEN1.csv", date_time=(2026, 7, 20, 0, 0, 0))
        info.compress_type = zipfile.ZIP_DEFLATED
        info.external_attr = 0o100644 << 16
        archive.writestr(info, csv_raw)
    return stream.getvalue()


def create_source_snapshots(root: Path) -> tuple[Path, Path]:
    moenv_archive = root / "moenv.zip"
    moenv_archive.write_bytes(moenv_archive_raw())
    moenv_root = root / "moenv-snapshot"
    create_moenv_snapshot(
        moenv_archive,
        moenv_root,
        retrieved_at=RETRIEVED_AT,
        dataset_updated_at=MOENV_UPDATED_AT,
        dataset_updated_at_basis="official_dataset_page_displayed_asia_taipei",
        download_url=MOENV_FULL_PACKAGE_URL,
        acquisition_request_body={
            "rid": [MOENV_RESOURCE_RID],
            "download_type": "json",
            "pid": MOENV_PACKAGE_PID,
        },
    )

    factory_archive = root / "factory.zip"
    raw = factory_archive_raw()
    factory_archive.write_bytes(raw)
    factory_root = root / "factory-snapshot"
    create_taiwan_factory_snapshot(
        factory_archive,
        factory_root,
        retrieved_at=RETRIEVED_AT,
        upstream_last_modified=FACTORY_LAST_MODIFIED,
        upstream_content_type="application/zip",
        upstream_content_length=len(raw),
    )
    return moenv_root, factory_root
