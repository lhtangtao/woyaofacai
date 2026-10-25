"""只读扫描完整 XLSX 包，保留 XML 数值字符串和公式，绝不重算或保存来源。"""
import hashlib
import json
import posixpath
import re
from pathlib import Path
from xml.etree import ElementTree as ET
from zipfile import ZipFile

NS = {
    "s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "p": "http://schemas.openxmlformats.org/package/2006/relationships",
    "c": "http://schemas.openxmlformats.org/drawingml/2006/chart",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
}


def _text(element):
    return "".join(element.itertext()) if element is not None else ""


def extract_reference(path: Path) -> dict:
    path = Path(path)
    result = {"source_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
              "source_name": path.name, "recalculated": False, "sheets": [], "charts": []}
    with ZipFile(path) as archive:
        shared = []
        if "xl/sharedStrings.xml" in archive.namelist():
            shared = ["".join(node.itertext()) for node in ET.fromstring(archive.read("xl/sharedStrings.xml"))]
        relations = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        targets = {node.attrib["Id"]: node.attrib["Target"] for node in relations}
        workbook = ET.fromstring(archive.read("xl/workbook.xml"))
        result["defined_names"] = {n.attrib["name"]: n.text for n in workbook.findall("s:definedNames/s:definedName", NS)}
        for node in workbook.findall("s:sheets/s:sheet", NS):
            target = targets[node.attrib[f"{{{NS['r']}}}id"]]
            target = target.lstrip("/") if target.startswith("/") else posixpath.normpath("xl/" + target)
            sheet = ET.fromstring(archive.read(target))
            cells = {}
            for cell in sheet.findall("s:sheetData/s:row/s:c", NS):
                value_node = cell.find("s:v", NS)
                value = value_node.text if value_node is not None else None
                kind = cell.attrib.get("t", "n")
                if kind == "s" and value is not None:
                    value = shared[int(value)]
                elif kind == "inlineStr":
                    value = _text(cell.find("s:is", NS))
                formula = cell.find("s:f", NS)
                if value is not None or formula is not None:
                    cells[cell.attrib["r"]] = {
                        "value": value, "type": kind, "style": cell.attrib.get("s"),
                        "formula": formula.text if formula is not None else None,
                        "formula_attributes": dict(formula.attrib) if formula is not None else {},
                    }
            dimension = sheet.find("s:dimension", NS)
            result["sheets"].append({
                "name": node.attrib["name"], "xml": target,
                "dimension": dimension.attrib.get("ref") if dimension is not None else None,
                "headers": [value["value"] for key, value in cells.items() if re.fullmatch(r"[A-Z]+1", key)],
                "cell_count": len(cells),
                "formula_count": sum(c["formula"] is not None for c in cells.values()),
                "cached_errors": {key: c["value"] for key, c in cells.items() if c["type"] == "e"},
                "cells": cells,
            })
        charts = sorted((n for n in archive.namelist() if re.fullmatch(r"xl/charts/chart\d+\.xml", n)),
                        key=lambda n: int(re.search(r"chart(\d+)", n)[1]))
        for name in charts:
            chart = ET.fromstring(archive.read(name))
            title = "".join(n.text or "" for n in chart.findall("c:chart/c:title//a:t", NS))
            series = []
            for series_node in chart.findall(".//c:ser", NS):
                series.append({
                    "title": _text(series_node.find("c:tx", NS)),
                    "references": [n.text for n in series_node.findall(".//c:f", NS)],
                    "cached_values": [n.text for n in series_node.findall("c:val//c:pt/c:v", NS)],
                    "cached_categories": [n.text for n in series_node.findall("c:cat//c:pt/c:v", NS)],
                })
            result["charts"].append({"xml": name, "title": title, "series": series,
                                     "types": sorted({n.tag.rsplit("}", 1)[-1] for n in chart.iter()
                                                      if n.tag.endswith("Chart")})})
    return result


def write_snapshot(input_path: Path, output_path: Path):
    if input_path.resolve() == output_path.resolve():
        raise ValueError("不能覆盖来源工作簿。")
    snapshot = extract_reference(input_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("x", encoding="utf-8") as stream:
        json.dump(snapshot, stream, ensure_ascii=False, indent=2)
    return snapshot
