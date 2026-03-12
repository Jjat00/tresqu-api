from cryptography.fernet import Fernet
from django.conf import settings


def get_fernet():
    """Obtiene una instancia de Fernet con la clave de cifrado configurada."""
    key = settings.GMAIL_TOKEN_ENCRYPTION_KEY
    return Fernet(key.encode() if isinstance(key, str) else key)


def encrypt_token(plain_text: str) -> bytes:
    """Cifra un token en texto plano y devuelve bytes cifrados."""
    f = get_fernet()
    return f.encrypt(plain_text.encode())


def decrypt_token(cipher_bytes: bytes) -> str:
    """Descifra bytes cifrados y devuelve el token en texto plano."""
    f = get_fernet()
    return f.decrypt(cipher_bytes).decode()
