"""Password and token hashing shared by the auth API, password reset and scripts."""
import hashlib
import hmac
import os


def hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt or os.urandom(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1)
    return f"scrypt${salt.hex()}${digest.hex()}"


def verify_password(password: str, encoded: str | None) -> bool:
    try:
        _, salt, digest = (encoded or "").split("$", 2)
        return hmac.compare_digest(hash_password(password, bytes.fromhex(salt)).split("$", 2)[2], digest)
    except (ValueError, TypeError):
        return False


# Verified against when a username does not exist, so response time does not
# reveal whether an account exists.
DUMMY_PASSWORD_HASH = hash_password("packcomply-timing-equalizer")


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()
