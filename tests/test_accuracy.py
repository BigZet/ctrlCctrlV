from itertools import product

import pytest

from scripts.accuracy import compare_errors, error_positions
from scripts.benchmark_review import edit_distance


def test_net_improvement_does_not_hide_new_error():
    result = compare_errors("abcdef", "abXdeY", "abcZef")
    assert result == {"before_errors": 2, "after_errors": 1,
                      "corrected_error_positions": 2, "introduced_error_positions": 1,
                      "remaining_error_positions": 0}


def test_changing_wrong_character_to_another_wrong_character_is_not_a_fix():
    result = compare_errors("hello", "hell0", "hella")
    assert result["corrected_error_positions"] == 0
    assert result["introduced_error_positions"] == 0
    assert result["remaining_error_positions"] == 1


def test_alignment_counts_match_edit_distance_for_short_exhaustive_pairs():
    words = ["".join(chars) for length in range(4) for chars in product("ab", repeat=length)]
    for expected in words:
        for actual in words:
            assert len(error_positions(expected, actual)) == edit_distance(expected, actual)


def test_insertions_deletions_unicode_and_budget():
    assert compare_errors("a🙂b", "aX🙂b", "a🙂b")["corrected_error_positions"] == 1
    assert compare_errors("a🙂b", "a🙂b", "ab")["introduced_error_positions"] == 1
    assert len(error_positions("", "abc")) == 3
    assert len(error_positions("abc", "")) == 3
    with pytest.raises(ValueError, match="memory"):
        error_positions("a" * 4000, "b" * 4000)
