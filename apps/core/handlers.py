from django.http import Http404
from rest_framework.exceptions import APIException
from rest_framework.views import exception_handler as drf_exception_handler


def api_exception_handler(exc, context):
    response = drf_exception_handler(exc, context)
    if response is None:
        return None
    if isinstance(exc, APIException):
        if isinstance(exc.detail, dict) and "code" in exc.detail and "detail" in exc.detail:
            response.data = {"code": str(exc.detail["code"]), "detail": str(exc.detail["detail"])}
            return response
        codes = exc.get_codes()
        if isinstance(codes, str):
            response.data = {"code": codes, "detail": str(exc.detail)}
        else:
            response.data = {
                "code": "validation_error",
                "detail": "البيانات المرسلة غير صالحة",
                "errors": response.data,
            }
        return response
    if isinstance(exc, Http404):
        response.data = {"code": "not_found", "detail": "العنصر المطلوب غير موجود"}
    else:
        response.data = {"code": "permission_denied", "detail": str(response.data.get("detail", ""))}
    return response
