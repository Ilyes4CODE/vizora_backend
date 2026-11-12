from datetime import timedelta

from django.utils import timezone
from rest_framework.exceptions import AuthenticationFailed
from rest_framework_simplejwt.authentication import JWTAuthentication

from apps.core.models import SecuritySettings

from .models import User

ACTIVITY_WRITE_INTERVAL = timedelta(seconds=60)


class SessionExpired(AuthenticationFailed):
    default_detail = "انتهت الجلسة بسبب عدم النشاط، سجّل الدخول من جديد"
    default_code = "session_expired"


class SessionRevoked(AuthenticationFailed):
    default_detail = "تغيّرت بيانات الدخول، سجّل الدخول من جديد"
    default_code = "session_revoked"


def ensure_token_current(user, token):
    if token.get("sv", 0) != user.session_version:
        raise SessionRevoked()


def ensure_session_active(user):
    security = SecuritySettings.load()
    if not security.auto_logout_enabled or user.last_activity_at is None:
        return
    limit = timedelta(minutes=security.auto_logout_minutes)
    if timezone.now() - user.last_activity_at > limit:
        raise SessionExpired()


def touch_activity(user):
    now = timezone.now()
    User.objects.filter(pk=user.pk).update(last_activity_at=now)
    user.last_activity_at = now


class ActivityJWTAuthentication(JWTAuthentication):
    def authenticate(self, request):
        result = super().authenticate(request)
        if result is None:
            return None
        user, token = result
        ensure_token_current(user, token)
        ensure_session_active(user)
        if user.last_activity_at is None or timezone.now() - user.last_activity_at > ACTIVITY_WRITE_INTERVAL:
            touch_activity(user)
        return result
