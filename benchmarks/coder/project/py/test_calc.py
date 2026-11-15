from calc import add, average, clamp


def test_add():
    assert add(2, 3) == 5


def test_average():
    assert average([2, 4, 6]) == 4


def test_clamp():
    assert clamp(15, 0, 10) == 10
    assert clamp(-1, 0, 10) == 0
