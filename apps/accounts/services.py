import pyotp
from django.db.models import F
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken
from rest_framework_simplejwt.tokens import RefreshToken

from .models import User


def verify_otp(user, code):
    secret = user.get_totp_secret()
    if not secret or not code:
        return False
    return pyotp.TOTP(secret).verify(str(code).strip(), valid_window=1)


def issue_tokens(user):
    refresh = RefreshToken.for_user(user)
    refresh["sv"] = user.session_version
    return refresh


def end_all_sessions(user):
    User.objects.filter(pk=user.pk).update(session_version=F("session_version") + 1)
    user.refresh_from_db(fields=["session_version"])
    for token in OutstandingToken.objects.filter(user=user):
        BlacklistedToken.objects.get_or_create(token=token)
