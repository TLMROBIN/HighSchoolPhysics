import base64
import hashlib
import hmac
import os
import secrets


PASSWORD_ALGORITHM = "pbkdf2_sha256"
PASSWORD_ITERATIONS = 200_000


def hash_password(password, salt=None):
    """Return a salted PBKDF2 password hash string."""
    if salt is None:
        salt = secrets.token_hex(16)
    password_bytes = password.encode("utf-8")
    salt_bytes = salt.encode("utf-8")
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password_bytes,
        salt_bytes,
        PASSWORD_ITERATIONS,
    )
    encoded = base64.b64encode(digest).decode("ascii")
    return "%s$%s$%s$%s" % (
        PASSWORD_ALGORITHM,
        PASSWORD_ITERATIONS,
        salt,
        encoded,
    )


def verify_password(password, stored_hash):
    try:
        algorithm, iterations, salt, expected = stored_hash.split("$", 3)
    except ValueError:
        return False
    if algorithm != PASSWORD_ALGORITHM:
        return False
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        int(iterations),
    )
    actual = base64.b64encode(digest).decode("ascii")
    return hmac.compare_digest(actual, expected)


def new_session_token():
    return secrets.token_urlsafe(32)


def hash_token(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def mask_secret(secret):
    if not secret:
        return ""
    if len(secret) <= 8:
        return "*" * len(secret)
    return secret[:4] + "********" + secret[-4:]


def local_secret_key(path):
    if os.path.exists(path):
        with open(path, "rb") as handle:
            return handle.read()
    key = secrets.token_bytes(32)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as handle:
        handle.write(key)
    os.chmod(path, 0o600)
    return key


def protect_secret(secret, key):
    """Obfuscate a local API key with a server-local key.

    The MVP avoids external crypto dependencies. This is not a replacement for
    managed key storage, but it keeps raw keys out of the SQLite text field.
    """
    payload = secret.encode("utf-8")
    stream = hashlib.sha256(key).digest()
    encrypted = bytes(payload[i] ^ stream[i % len(stream)] for i in range(len(payload)))
    return base64.urlsafe_b64encode(encrypted).decode("ascii")


def reveal_secret(ciphertext, key):
    encrypted = base64.urlsafe_b64decode(ciphertext.encode("ascii"))
    stream = hashlib.sha256(key).digest()
    payload = bytes(encrypted[i] ^ stream[i % len(stream)] for i in range(len(encrypted)))
    return payload.decode("utf-8")
