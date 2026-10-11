"""Deterministic reference alignment for measuring corrected and introduced OCR errors."""
from array import array


def error_positions(expected, actual):
    """Return wrong reference characters and extra-character slots using Levenshtein alignment.

    Replacement and deletion at the same reference character are both an existing
    error. Changing one incorrect character to another does not count as a fix.
    Equal-cost alignments prefer substitution, then insertion, then deletion.
    Repeated text can still have ambiguous alignment; these are positional counts,
    not a semantic judgement about correctness.
    """
    if expected == actual:
        return frozenset()
    if (len(expected) + 1) * (len(actual) + 1) > 10_000_000:
        raise ValueError("Reference alignment exceeds the benchmark memory limit")
    code = "H" if len(expected) + len(actual) < 65536 else "I"
    rows = [array(code, range(len(actual) + 1))]
    for i, left in enumerate(expected, 1):
        previous = rows[-1]
        row = array(code, [i])
        for j, right in enumerate(actual, 1):
            row.append(min(row[-1] + 1, previous[j] + 1, previous[j - 1] + (left != right)))
        rows.append(row)
    i, j = len(expected), len(actual)
    errors, extras = set(), {}
    while i or j:
        if i and j and expected[i - 1] == actual[j - 1] and rows[i][j] == rows[i - 1][j - 1]:
            i, j = i - 1, j - 1
        elif i and j and rows[i][j] == rows[i - 1][j - 1] + 1:
            errors.add(("character", i - 1, 0))
            i, j = i - 1, j - 1
        elif j and rows[i][j] == rows[i][j - 1] + 1:
            index = extras.get(i, 0)
            errors.add(("extra", i, index))
            extras[i] = index + 1
            j -= 1
        else:
            errors.add(("character", i - 1, 0))
            i -= 1
    return frozenset(errors)


def compare_errors(expected, original, proposed):
    before, after = error_positions(expected, original), error_positions(expected, proposed)
    return {"before_errors": len(before), "after_errors": len(after),
            "corrected_error_positions": len(before - after),
            "introduced_error_positions": len(after - before),
            "remaining_error_positions": len(before & after)}
