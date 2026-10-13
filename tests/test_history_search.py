import pytest

from ctrlcctrlv.history import History


@pytest.mark.parametrize("query", ["папка", "ПАПКА", "КаРтОнНаЯ"])
def test_history_search_is_case_insensitive_for_cyrillic(tmp_path, query):
    history = History(tmp_path)
    original = 'Папка, картонная\r\nКоличество: 001\n'
    identifier = history.add("ocr", original)
    history.add("ocr", "Ручка")
    found = History(tmp_path).entries(query)
    assert [(entry.id, entry.text) for entry in found] == [(identifier, original)]


def test_history_search_casefolds_unicode_and_keeps_query_literal(tmp_path):
    history = History(tmp_path)
    identifier = history.add("ocr", "Straße_50%")
    history.add("ocr", "STRASSE A50B")
    assert [entry.id for entry in history.entries("STRASSE_")] == [identifier]
    assert [entry.id for entry in history.entries("50%")] == [identifier]
    assert history.get(identifier).text == "Straße_50%"
