import re
from typing import Any

SECRET_PATTERNS = (
    re.compile(r"(?i)(api[_-]?key|token|secret|password)\s*[:=]\s*\S+"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b"),
    re.compile(r"\bghp_[A-Za-z0-9_]{12,}\b"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
)


def safe_payload(value: Any) -> Any:
    if isinstance(value, dict):
        cleaned = {}
        for key, item in value.items():
            cleaned_value = redact_value(key, safe_payload(item))
            if cleaned_value is None or cleaned_value == {} or cleaned_value == []:
                continue
            cleaned[key] = cleaned_value
        return cleaned
    if isinstance(value, list):
        return [item for item in (safe_payload(item) for item in value) if item is not None]
    if isinstance(value, str):
        return redact_secret_patterns(value)
    return value


def redact_value(key: str, value: Any) -> Any:
    lowered = key.lower()
    if any(token in lowered for token in ("secret", "token", "password", "api_key", "apikey", "private_key")):
        return "[redacted]"
    return value


def redact_secret_patterns(value: str) -> str:
    replacements = (
        (r"(?i)(authorization:\s*bearer\s+)[^\s]+", r"\1[redacted]"),
        (r"(?i)(api[_-]?key\s*=\s*)[^\s]+", r"\1[redacted]"),
        (r"(?i)(openai_api_key\s*=\s*)[^\s]+", r"\1[redacted]"),
        (r"(?i)(anthropic_api_key\s*=\s*)[^\s]+", r"\1[redacted]"),
        (r"(?i)(password\s*=\s*)[^\s]+", r"\1[redacted]"),
        (r"(?i)(token\s*=\s*)[^\s]+", r"\1[redacted]"),
        (r"\bsk-[A-Za-z0-9_-]{12,}\b", "[redacted api key]"),
        (r"\bghp_[A-Za-z0-9_]{12,}\b", "[redacted token]"),
        (r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----", "[redacted private key]"),
    )
    redacted = value
    for pattern, replacement in replacements:
        redacted = re.sub(pattern, replacement, redacted)
    return redacted


def contains_secret(value: str) -> bool:
    return any(pattern.search(value) for pattern in SECRET_PATTERNS)
