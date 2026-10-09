"""Minimal, dependency-free XLSX writer (inline strings, numbers, frozen header, autofilter)."""

from __future__ import annotations

import re
import zipfile
from pathlib import Path
from typing import Any, Iterable
from xml.sax.saxutils import escape

_ILLEGAL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
_FIXED_TIME = (2000, 1, 1, 0, 0, 0)  # stable zip timestamps for reproducible bytes


def _col(index: int) -> str:
    name = ""
    index += 1
    while index:
        index, rem = divmod(index - 1, 26)
        name = chr(65 + rem) + name
    return name


def _cell(ref: str, value: Any, style: int = 0) -> str:
    s = f' s="{style}"' if style else ""
    if value is None or value == "":
        return ""
    if isinstance(value, bool):
        return f'<c r="{ref}" t="b"{s}><v>{int(value)}</v></c>'
    if isinstance(value, (int, float)):
        return f'<c r="{ref}"{s}><v>{value!r}</v></c>'
    text = escape(_ILLEGAL.sub("", str(value)))[:32767]
    return f'<c r="{ref}" t="inlineStr"{s}><is><t xml:space="preserve">{text}</t></is></c>'


def _sheet(columns: list[str], rows: Iterable[list[Any]]) -> str:
    out = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
           '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">',
           '<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" '
           'activePane="bottomLeft" state="frozen"/></sheetView></sheetViews><sheetData>']
    header = "".join(_cell(f"{_col(i)}1", name, 1) for i, name in enumerate(columns))
    out.append(f'<row r="1">{header}</row>')
    count = 1
    for r, row in enumerate(rows, start=2):
        cells = "".join(_cell(f"{_col(i)}{r}", v) for i, v in enumerate(row))
        out.append(f'<row r="{r}">{cells}</row>')
        count = r
    out.append("</sheetData>")
    if columns:
        out.append(f'<autoFilter ref="A1:{_col(len(columns) - 1)}{count}"/>')
    out.append("</worksheet>")
    return "".join(out)


def write_xlsx(path: Path, sheets: list[tuple[str, list[str], list[list[Any]]]]) -> None:
    """Write sheets given as (name, columns, rows)."""
    def info(name: str) -> zipfile.ZipInfo:
        item = zipfile.ZipInfo(name, date_time=_FIXED_TIME)
        item.compress_type = zipfile.ZIP_DEFLATED
        return item

    with zipfile.ZipFile(path, "w") as zf:
        overrides = "".join(
            f'<Override PartName="/xl/worksheets/sheet{i}.xml" ContentType="application/'
            f'vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            for i in range(1, len(sheets) + 1)
        )
        zf.writestr(info("[Content_Types].xml"),
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.'
            'relationships+xml"/><Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-'
            'officedocument.spreadsheetml.sheet.main+xml"/>'
            '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-'
            'officedocument.spreadsheetml.styles+xml"/>' + overrides + "</Types>")
        zf.writestr(info("_rels/.rels"),
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
            'relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        sheet_entries = "".join(
            f'<sheet name="{escape(name[:31])}" sheetId="{i}" r:id="rId{i}"/>'
            for i, (name, _, _) in enumerate(sheets, start=1)
        )
        zf.writestr(info("xl/workbook.xml"),
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            f"<sheets>{sheet_entries}</sheets></workbook>")
        rels = "".join(
            f'<Relationship Id="rId{i}" Type="http://schemas.openxmlformats.org/officeDocument/'
            f'2006/relationships/worksheet" Target="worksheets/sheet{i}.xml"/>'
            for i in range(1, len(sheets) + 1)
        )
        style_id = len(sheets) + 1
        rels += (f'<Relationship Id="rId{style_id}" Type="http://schemas.openxmlformats.org/'
                 'officeDocument/2006/relationships/styles" Target="styles.xml"/>')
        zf.writestr(info("xl/_rels/workbook.xml.rels"),
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            + rels + "</Relationships>")
        zf.writestr(info("xl/styles.xml"),
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            '<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font>'
            '<font><b/><sz val="11"/><name val="Calibri"/></font></fonts>'
            '<fills count="2"><fill><patternFill patternType="none"/></fill>'
            '<fill><patternFill patternType="gray125"/></fill></fills>'
            '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border>'
            '</borders><cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" '
            'borderId="0"/></cellStyleXfs><cellXfs count="2"><xf numFmtId="0" fontId="0" '
            'fillId="0" borderId="0" xfId="0"/><xf numFmtId="0" fontId="1" fillId="0" '
            'borderId="0" xfId="0" applyFont="1"/></cellXfs></styleSheet>')
        for i, (_, columns, rows) in enumerate(sheets, start=1):
            zf.writestr(info(f"xl/worksheets/sheet{i}.xml"), _sheet(columns, rows))
