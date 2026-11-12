from rest_framework import status
from rest_framework.exceptions import APIException


class ApiError(APIException):
    def __init__(self, detail, code, status_code=status.HTTP_400_BAD_REQUEST):
        super().__init__(detail=detail, code=code)
        self.status_code = status_code


def conflict(detail, code):
    return ApiError(detail, code, status.HTTP_409_CONFLICT)
