from calendar import monthrange
from datetime import date
from io import BytesIO
from pathlib import Path

import arabic_reshaper
from bidi.algorithm import get_display
from django.http import HttpResponse
from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from apps.core.dates import month_label
from apps.core.models import BusinessProfile

from .services import daily_report, monthly_report

FONT_DIRECTORY = Path(__file__).resolve().parent / "fonts"
REGULAR_FONT = "IBMPlexSansArabic"
BOLD_FONT = "IBMPlexSansArabic-Bold"


def register_fonts():
    if REGULAR_FONT not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont(REGULAR_FONT, str(FONT_DIRECTORY / "IBMPlexSansArabic-Regular.ttf")))
        pdfmetrics.registerFont(TTFont(BOLD_FONT, str(FONT_DIRECTORY / "IBMPlexSansArabic-Bold.ttf")))


def shape(value):
    text = f"{value:,}" if isinstance(value, int) else str(value)
    return get_display(arabic_reshaper.reshape(text))


def monthly_table(first_day):
    report = monthly_report(first_day)
    kpis = report["kpis"]
    accounting = report["accounting"]
    rows = [
        ("الطلبيات الواردة", kpis["orders"]),
        ("الطلبيات المؤكدة", kpis["confirmed"]),
        ("نسبة التأكيد", f"{kpis['confirmation_rate']}%"),
        ("الطرود المسلّمة", kpis["delivered"]),
        ("الطرود المرتجعة", kpis["returned"]),
        ("نسبة التسليم", f"{kpis['delivery_rate']}%"),
        ("المبيعات", accounting["sales"]),
        ("إجمالي المصاريف", accounting["expenses_total"]),
        ("صافي الربح", accounting["net_profit"]),
    ]
    rows += [(item["name"], item["quantity"]) for item in report["product_split"]["products"]]
    rows += [(f"نسبة النجاح: {item['wilaya']}", f"{item['success_rate']}%") for item in report["wilayas"]]
    return f"التقرير الشهري · {month_label(first_day)}", ["البيان", "القيمة"], rows


def daily_table(first_day):
    last_day = date(first_day.year, first_day.month, monthrange(first_day.year, first_day.month)[1])
    rows = [
        (str(row["date"]), row["orders"], row["confirmed"], f"{row['confirmation_rate']}%", row["delivered"], row["sales"])
        for row in daily_report(first_day, last_day)
    ]
    return f"التقرير اليومي · {month_label(first_day)}", ["اليوم", "طلبيات", "مؤكدة", "نسبة التأكيد", "مسلّمة", "المبيعات"], rows


def pdf_response(title, headers, rows, file_name):
    register_fonts()
    output = BytesIO()
    document = SimpleDocTemplate(output, pagesize=A4, rightMargin=15 * mm, leftMargin=15 * mm, topMargin=15 * mm, bottomMargin=15 * mm)
    heading = ParagraphStyle("heading", fontName=BOLD_FONT, fontSize=16, leading=24, alignment=TA_RIGHT)
    caption = ParagraphStyle("caption", fontName=REGULAR_FONT, fontSize=10, leading=16, alignment=TA_RIGHT)
    table_rows = [[shape(cell) for cell in reversed(headers)]]
    table_rows += [[shape(cell) for cell in reversed(row)] for row in rows]
    table = Table(table_rows, repeatRows=1, hAlign="RIGHT")
    table.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (-1, -1), REGULAR_FONT),
                ("FONTNAME", (0, 0), (-1, 0), BOLD_FONT),
                ("FONTSIZE", (0, 0), (-1, -1), 10),
                ("ALIGN", (0, 0), (-1, -1), "RIGHT"),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1F62E0")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("LINEBELOW", (0, 0), (-1, -1), 0.5, colors.HexColor("#D6DFEF")),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F3F6FC")]),
            ]
        )
    )
    story = [
        Paragraph(shape(BusinessProfile.load().name), caption),
        Paragraph(shape(title), heading),
        Spacer(1, 6 * mm),
        table,
    ]
    document.build(story)
    response = HttpResponse(output.getvalue(), content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{file_name}"'
    return response
