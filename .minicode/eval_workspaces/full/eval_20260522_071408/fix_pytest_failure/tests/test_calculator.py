from calculator import add, divide, multiply


def test_add() -> None:
    assert add(2, 3) == 5


def test_divide() -> None:
    assert divide(6, 2) == 3


def test_multiply() -> None:
    assert multiply(2, 3) == 6
