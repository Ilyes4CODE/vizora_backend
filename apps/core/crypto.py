from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings


def _cipher():
    return Fernet(settings.FIELD_ENCRYPTION_KEY.encode())


def encrypt_bytes(value):
    return _cipher().encrypt(value)


def decrypt_bytes(value):
    return _cipher().decrypt(value)


def encrypt_text(value):
    if not value:
        return ""
    return encrypt_bytes(value.encode("utf-8")).decode("ascii")


def decrypt_text(value):
    if not value:
        return ""
    return decrypt_bytes(value.encode("ascii")).decode("utf-8")


__all__ = ["InvalidToken", "encrypt_bytes", "decrypt_bytes", "encrypt_text", "decrypt_text"]
