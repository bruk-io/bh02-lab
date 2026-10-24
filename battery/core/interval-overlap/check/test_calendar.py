from calendar_utils import merge, overlaps


def test_back_to_back_do_not_overlap():
    assert not overlaps((9, 10), (10, 11))
    assert not overlaps((10, 11), (9, 10))


def test_real_overlap():
    assert overlaps((9, 11), (10, 12))


def test_containment_overlaps():
    assert overlaps((9, 17), (12, 13))


def test_merge_unsorted():
    assert merge([(5, 7), (1, 3), (2, 4)]) == [(1, 4), (5, 7)]


def test_merge_keeps_the_longer_end():
    assert merge([(1, 10), (2, 3)]) == [(1, 10)]


def test_merge_touching():
    assert merge([(3, 4), (1, 3)]) == [(1, 4)]
