import base64
import hashlib

from cryptography.fernet import Fernet
from django.conf import settings


def token_cipher():
    secret = settings.FEELOOM_TOKEN_ENCRYPTION_KEY.strip() or settings.SECRET_KEY
    key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest())
    return Fernet(key)


def encrypt_token(value):
    return token_cipher().encrypt(value.encode()).decode()


def decrypt_token(value):
    return token_cipher().decrypt(value.encode()).decode()
