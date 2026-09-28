"""Authenticated integration ciphertext, bound to the owning account."""

from uuid import UUID

from app.core.config import Settings


def encrypt_destination(value: str, account_id: UUID, settings: Settings) -> str:
    from cryptography.fernet import Fernet

    key = settings.integration_encryption_key.get_secret_value()
    if not key:
        raise ValueError("Integration encryption key is not configured")
    plaintext = (str(account_id) + "\n" + value).encode()
    return "enc:v1:" + Fernet(key.encode()).encrypt(plaintext).decode()


def decrypt_destination(value: str, account_id: UUID, settings: Settings) -> str:
    from cryptography.fernet import Fernet

    # Historical plaintext is disabled until explicitly migrated by the operator.
    if not value.startswith("enc:v1:"):
        raise ValueError("Integration needs encryption migration")
    plaintext = (
        Fernet(settings.integration_encryption_key.get_secret_value().encode())
        .decrypt(value[7:].encode(), ttl=None)
        .decode()
    )
    owner, destination = plaintext.split("\n", 1)
    if owner != str(account_id):
        raise ValueError("Integration ownership mismatch")
    return destination
