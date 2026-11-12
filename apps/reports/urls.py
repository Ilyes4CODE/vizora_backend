from django.urls import path

from .views import DailyReportView, DashboardView, MonthlyReportView, ReportExportView

urlpatterns = [
    path("dashboard/", DashboardView.as_view(), name="dashboard"),
    path("reports/monthly/", MonthlyReportView.as_view(), name="report-monthly"),
    path("reports/daily/", DailyReportView.as_view(), name="report-daily"),
    path("reports/export/", ReportExportView.as_view(), name="report-export"),
]
