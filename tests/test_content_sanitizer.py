from __future__ import annotations

from enterprise_adapters.content_sanitizer import sanitize_document_content


def test_sanitize_private_keys() -> None:
    text = (
        "Here is a key:\n"
        "-----BEGIN RSA PRIVATE KEY-----\n"
        "MIIEowIBAAKCAQEA0Y3...\n"
        "-----END RSA PRIVATE KEY-----\n"
        "Keep it safe."
    )
    sanitized = sanitize_document_content(text)
    assert "-----BEGIN RSA PRIVATE KEY-----" not in sanitized
    assert "[REDACTED_PRIVATE_KEY]" in sanitized
    assert "Keep it safe." in sanitized


def test_sanitize_aws_access_keys() -> None:
    text = "Deploy using AKIAIOSFODNN7EXAMPLE and secret."
    sanitized = sanitize_document_content(text)
    assert "AKIAIOSFODNN7EXAMPLE" not in sanitized
    assert "[REDACTED_AWS_KEY]" in sanitized


def test_sanitize_bearer_tokens() -> None:
    text = "curl -H 'Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.xyz' http://api"
    sanitized = sanitize_document_content(text)
    assert "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.xyz" not in sanitized
    assert "Bearer [REDACTED]" in sanitized


def test_sanitize_basic_auth() -> None:
    text = "Authorization: Basic dXNlcjpwYXNzd29yZA=="
    sanitized = sanitize_document_content(text)
    assert "dXNlcjpwYXNzd29yZA==" not in sanitized
    assert "Basic [REDACTED]" in sanitized


def test_sanitize_api_keys() -> None:
    text = "openai_api_key = 'sk-abcdef1234567890abcdef1234567890'"
    sanitized = sanitize_document_content(text)
    assert "sk-abcdef1234567890abcdef1234567890" not in sanitized
    assert "[REDACTED_API_KEY]" in sanitized


def test_sanitize_github_tokens() -> None:
    text = "GITHUB_PAT=ghp_0123456789abcdefghijklmnopqrstuvwxyz01"
    sanitized = sanitize_document_content(text)
    assert "ghp_0123456789abcdefghijklmnopqrstuvwxyz01" not in sanitized
    assert "[REDACTED_GITHUB_TOKEN]" in sanitized


def test_sanitize_database_credentials() -> None:
    text = "connect to postgresql://postgres:supersecretpassword@localhost:5432/mydb"
    sanitized = sanitize_document_content(text)
    assert "supersecretpassword" not in sanitized
    assert "postgresql://postgres:[REDACTED]@localhost:5432/mydb" in sanitized


def test_sanitize_clean_text_unchanged() -> None:
    text = "This is a clean document describing architecture with no credentials."
    assert sanitize_document_content(text) == text
