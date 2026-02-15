from app.auth import hash_password, verify_password


def test_password_hashing_roundtrip():
    hashed = hash_password("secret123")
    assert hashed.startswith("pbkdf2_sha256$")
    assert verify_password("secret123", hashed)
    assert not verify_password("bad", hashed)
