import base64
import hashlib

from cryptography.fernet import Fernet
from django.conf import settings


def _fernet() -> Fernet:
    key_source = settings.WALLBIT_ENCRYPTION_KEY or settings.SECRET_KEY
    digest = hashlib.sha256(key_source.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_api_key(plain: str) -> str:
    return _fernet().encrypt(plain.encode()).decode()


def decrypt_api_key(token: str) -> str:
    return _fernet().decrypt(token.encode()).decode()
