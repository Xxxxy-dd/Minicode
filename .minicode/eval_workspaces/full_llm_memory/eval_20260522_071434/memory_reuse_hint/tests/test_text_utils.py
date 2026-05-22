from text_utils import slugify, summarize, title_case


def test_slugify() -> None:
    assert slugify(" Hello World ") == "hello-world"


def test_title_case() -> None:
    assert title_case("hello world") == "Hello World"


def test_summarize() -> None:
    assert summarize("hello world", max_chars=5) == "hello"
