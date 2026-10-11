import pytest

from scripts.benchmark_patches import apply_edits


def patch(start, before, after):
    return {"start_line": start, "before": before, "after": after}


def test_patch_preserves_unedited_unicode_and_line_endings():
    original = 'Ж🙂\r\n\tvalue = O\r\nlast'
    edits = [patch(2, ['\tvalue = O'], ['\tvalue = 0'])]
    assert apply_edits(original, {"edits": edits}) == 'Ж🙂\r\n\tvalue = 0\r\nlast'
    assert apply_edits(original, {"edits": []}) == original


def test_patch_handles_insertion_deletion_and_unterminated_last_line():
    assert apply_edits('a\nb\nc', {"edits": [patch(2, ['b'], []), patch(4, [], ['d'])]}) == 'a\nc\nd'
    assert apply_edits('a\nb\n', {"edits": [patch(1, [], ['header'])]}) == 'header\na\nb\n'
    assert apply_edits('a', {"edits": [patch(1, ['a'], ['b', 'c'])]}) == 'b\nc'


@pytest.mark.parametrize('edits', [
    [patch(1, ['wrong'], ['x'])],
    [patch(0, [], ['x'])],
    [patch(True, ['a'], ['x'])],
    [patch(4, [], ['x'])],
    [patch(1, ['a', 'b'], ['x']), patch(2, ['b'], ['y'])],
    [patch(1, [], ['x']), patch(1, [], ['y'])],
    [patch(2, ['b'], ['x']), patch(1, ['a'], ['y'])],
    [patch(1, ['a'], ['x\ny'])],
    [patch(1, ['a', 'b'], [])],
])
def test_unsafe_or_stale_patches_are_rejected(edits):
    with pytest.raises(ValueError):
        apply_edits('a\nb', {"edits": edits})
