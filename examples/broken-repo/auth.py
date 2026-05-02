"""auth.py — demo file with deliberate security and quality issues.

Used by the mesh-review golden test to verify the Sigma falsification gate:
- 4 true positives (real bugs that should survive the gate)
- 1 near-false-positive (MD5 used as a *non-crypto* checksum — technically
  flagged by every LLM but defensible in context, so a calibrated falsifier
  should return falsified=True for it)

DO NOT USE THIS IN PRODUCTION.
"""
import hashlib
import hmac
import os
import pickle  # noqa: S403  (deliberate: insecure deserialisation — true positive #1)
import sqlite3


SECRET_KEY = "hardcoded-secret-key-do-not-ship"  # true positive #2: hard-coded secret


def login(username: str, password: str) -> bool:
    """Authenticate a user against the database.

    BUG #3 (true positive): SQL injection via f-string interpolation.
    """
    conn = sqlite3.connect("users.db")
    cur = conn.cursor()
    # SQL injection: username is interpolated directly
    cur.execute(f"SELECT password FROM users WHERE username = '{username}'")  # noqa: S608
    row = cur.fetchone()
    conn.close()
    if row is None:
        return False
    return row[0] == password  # BUG #4: plaintext password comparison (true positive)


def session_token(user_id: int) -> str:
    """Generate a session token using HMAC-SHA256."""
    return hmac.new(SECRET_KEY.encode(), str(user_id).encode(), "sha256").hexdigest()


def file_checksum(path: str) -> str:
    """Return an MD5 hex-digest of a file for integrity checking.

    NOTE: MD5 is used here only as a fast non-cryptographic checksum to
    detect accidental corruption (e.g. incomplete downloads), NOT for any
    security purpose.  This is the near-false-positive (#5) that an
    adversarial LLM can correctly defend: MD5 is inappropriate for
    *password hashing* or *signature verification* but is entirely
    reasonable for checksumming file contents where collision resistance
    is not required.
    """
    h = hashlib.md5()  # noqa: S324
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def load_user_session(blob: bytes):
    """Deserialise a session object from a trusted internal cache.

    BUG #1 (true positive): uses pickle on data that could be attacker-
    controlled if the cache store is compromised.
    """
    return pickle.loads(blob)  # noqa: S301
