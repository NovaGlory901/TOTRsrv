import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from . import MAX_PASSWORD, MIN_PASSWORD

_hasher = PasswordHasher()  # Argon2id with the library's current recommended cost settings

# A real hash of a random value. Verifying against it when the username does not exist makes an
# unknown user cost the same time as a wrong password, so timing cannot reveal which names exist.
DUMMY_HASH = _hasher.hash(secrets.token_urlsafe(24))

# A small list of the passwords people reach for first. Length does most of the work; this just
# stops the embarrassing ones.
COMMON = {
    "password1234",
    "123456789012",
    "qwertyuiopas",
    "iloveyou1234",
    "letmein12345",
    "admin1234567",
    "welcome12345",
    "password12345",
    "changeme1234",
    "1234567890ab",
    "abcdefghijkl",
    "qwerty123456",
    "passwordpassword",
    "111111111111",
    "000000000000",
}


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(stored_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(stored_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(stored_hash: str) -> bool:
    """True when the hash was made with older/weaker settings; it gets upgraded on the next login."""
    try:
        return _hasher.check_needs_rehash(stored_hash)
    except InvalidHashError:
        return False


def check_new_password(password: str, username: str = "") -> str | None:
    """Returns a reason the password is unacceptable, or None. Length over composition rules."""
    if len(password) < MIN_PASSWORD:
        return f"Use at least {MIN_PASSWORD} characters. A few random words works well."
    if len(password) > MAX_PASSWORD:
        return f"Use at most {MAX_PASSWORD} characters."
    if len(set(password)) < 5:
        return "That password is too repetitive."
    if password.casefold() in COMMON:
        return "That password is far too common."
    if username and len(username) >= 3 and username.casefold() in password.casefold():
        return "The password should not contain your username."
    return None
