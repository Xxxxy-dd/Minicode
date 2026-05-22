def slugify(value: str) -> str:
    return value.strip().lower().replace(" ", "-")


def title_case(value: str) -> str:
    return " ".join(part.capitalize() for part in value.split(" "))


def summarize(value: str, max_chars: int = 20) -> str:
    if len(value) <= max_chars:
        return value
    return value[:max_chars]
