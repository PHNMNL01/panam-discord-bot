import json
import re
from pathlib import Path
from typing import Any

from openpyxl import Workbook


def safe_sheet_name(name: str) -> str:
    sheet_name = re.sub(r"[\[\]:*?/\\]", "_", str(name or "Data")).strip()
    if not sheet_name:
        sheet_name = "Data"
    return sheet_name[:31]


def flatten_value(value: Any) -> str | int | float | bool | None:
    if value is None:
        return ""

    if isinstance(value, (str, int, float, bool)):
        return value

    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False)

    return str(value)


def collect_headers(items: list[dict]) -> list[str]:
    headers = []
    seen = set()

    for item in items:
        for key in item.keys():
            header = str(key)
            if header in seen:
                continue

            seen.add(header)
            headers.append(header)

    return headers


def create_xlsx_from_items(
    items: list[dict],
    output_path: Path,
    sheet_name: str = "Data",
) -> Path:
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = safe_sheet_name(sheet_name)

    if not items:
        worksheet.append(["note"])
        worksheet.append(["V dokumentu jsem nenašla požadovaná data."])
    else:
        headers = collect_headers(items)
        if not headers:
            headers = ["value"]

        worksheet.append(headers)
        for item in items:
            worksheet.append([flatten_value(item.get(header, "")) for header in headers])

    output_path.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(output_path)
    workbook.close()
    return output_path
