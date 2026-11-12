import unicodedata
from io import BytesIO
from zipfile import BadZipFile

from django.conf import settings
from django.http import HttpResponse
from openpyxl import Workbook, load_workbook
from openpyxl.utils.exceptions import InvalidFileException

from .exceptions import ApiError

XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def normalize_text(value):
    text = str(value if value is not None else "").strip().casefold()
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(char for char in decomposed if not unicodedata.combining(char))


def read_rows(uploaded):
    if uploaded is None:
        raise ApiError("أرفق ملف Excel", "file_required")
    if not uploaded.name.lower().endswith(".xlsx"):
        raise ApiError("الصيغة المدعومة هي xlsx فقط", "unsupported_file")
    if uploaded.size > settings.MAX_IMPORT_FILE_BYTES:
        raise ApiError("حجم الملف كبير جداً", "file_too_large")
    try:
        workbook = load_workbook(uploaded, read_only=True, data_only=True)
    except (BadZipFile, InvalidFileException, KeyError, OSError, ValueError) as error:
        raise ApiError("تعذّرت قراءة الملف", "unreadable_file") from error
    rows = list(workbook.active.iter_rows(values_only=True))
    workbook.close()
    if not rows or not any(cell not in (None, "") for cell in rows[0]):
        raise ApiError("الملف فارغ", "empty_file")
    if len(rows) - 1 > settings.MAX_IMPORT_ROWS:
        raise ApiError(f"الحد الأقصى {settings.MAX_IMPORT_ROWS} سطر", "too_many_rows")
    headers = [str(cell).strip() if cell is not None else "" for cell in rows[0]]
    body = []
    for row in rows[1:]:
        values = list(row) + [None] * (len(headers) - len(row))
        body.append([cell if isinstance(cell, (int, float)) or cell is None else str(cell) for cell in values[: len(headers)]])
    return headers, body


def match_column(headers, aliases):
    wanted = {normalize_text(alias) for alias in aliases}
    for header in headers:
        if normalize_text(header) in wanted:
            return header
    return None


def build_workbook(title, headers, rows):
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = title[:31]
    sheet.sheet_view.rightToLeft = True
    sheet.append(list(headers))
    for row in rows:
        sheet.append(list(row))
    output = BytesIO()
    workbook.save(output)
    return output.getvalue()


def workbook_response(title, headers, rows, file_name):
    response = HttpResponse(build_workbook(title, headers, rows), content_type=XLSX_TYPE)
    response["Content-Disposition"] = f'attachment; filename="{file_name}"'
    return response
