"""Password policy shared by registration, password reset and the admin CLI."""

MIN_PASSWORD_LENGTH = 8

# The 20 most common passwords in public breach corpora (compared case-insensitively).
COMMON_PASSWORDS = frozenset({
    "123456", "password", "12345678", "qwerty", "123456789",
    "12345", "1234", "111111", "1234567", "dragon",
    "123123", "baseball", "abc123", "football", "monkey",
    "letmein", "shadow", "master", "qwertyuiop", "password1",
})


def password_problem(password: str, username: str) -> str | None:
    """Return why `password` is not acceptable for `username`, or None when it is."""
    if len(password) < MIN_PASSWORD_LENGTH:
        return f"Password must be at least {MIN_PASSWORD_LENGTH} characters."
    if password.strip().lower() == username.strip().lower():
        return "Password must not be the same as the username."
    if password.lower() in COMMON_PASSWORDS:
        return "This password is too common. Choose another."
    return None
