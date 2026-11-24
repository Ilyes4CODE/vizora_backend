from calendar import monthrange
from datetime import date

from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core import audit
from apps.core.dates import month_key, parse_day, parse_month
from apps.core.excel import workbook_response

from .exports import daily_table, monthly_table, pdf_response
from .agents import agent_performance
from .services import daily_report, dashboard, monthly_report

MAX_DAILY_RANGE_DAYS = 366


class DashboardView(APIView):
    def get(self, request):
        from apps.shipping.live import refresh_stock_soon

        refresh_stock_soon()
        return Response(dashboard(request.user, parse_month(request.query_params.get("month"))))


class MonthlyReportView(APIView):
    section = "reports"

    def get(self, request):
        return Response(monthly_report(parse_month(request.query_params.get("month"))))


class DailyReportView(APIView):
    section = "reports"

    def get(self, request):
        params = request.query_params
        if params.get("date_from") or params.get("date_to"):
            first_day = parse_day(params.get("date_from"), "date_from")
            last_day = parse_day(params.get("date_to"), "date_to")
            if last_day < first_day or (last_day - first_day).days > MAX_DAILY_RANGE_DAYS:
                raise ValidationError({"date_to": "الفترة غير صالحة"})
        else:
            first_day = parse_month(params.get("month"))
            last_day = date(first_day.year, first_day.month, monthrange(first_day.year, first_day.month)[1])
        return Response({"from": first_day, "to": last_day, "rows": daily_report(first_day, last_day)})


class AgentPerformanceView(APIView):
    section = "team"

    def get(self, request):
        params = request.query_params
        if params.get("date_from") or params.get("date_to"):
            first_day = parse_day(params.get("date_from"), "date_from")
            last_day = parse_day(params.get("date_to"), "date_to")
            if last_day < first_day or (last_day - first_day).days > MAX_DAILY_RANGE_DAYS:
                raise ValidationError({"date_to": "الفترة غير صالحة"})
        else:
            first_day = parse_month(params.get("month"))
            last_day = date(first_day.year, first_day.month, monthrange(first_day.year, first_day.month)[1])
        user = params.get("user")
        if user and not user.isdigit():
            raise ValidationError({"user": "موظف غير صالح"})
        return Response(agent_performance(first_day, last_day, int(user) if user else None))


class ReportExportView(APIView):
    section = "reports"

    def get(self, request):
        params = request.query_params
        kind = params.get("kind", "monthly")
        file_type = params.get("file_type", "xlsx")
        if kind not in ("monthly", "daily"):
            raise ValidationError({"kind": "القيمة يجب أن تكون monthly أو daily"})
        if file_type not in ("xlsx", "pdf"):
            raise ValidationError({"file_type": "القيمة يجب أن تكون xlsx أو pdf"})
        first_day = parse_month(params.get("month"))
        title, headers, rows = (monthly_table if kind == "monthly" else daily_table)(first_day)
        file_name = f"report-{kind}-{month_key(first_day)}.{file_type}"
        audit.record("report_exported", f"صدّر {title}", request=request)
        if file_type == "pdf":
            return pdf_response(title, headers, rows, file_name)
        return workbook_response(kind, headers, rows, file_name)
