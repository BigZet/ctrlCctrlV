from scripts.ocr_layout import reconstruct_characters


def char(text, x, y=10, width=10, height=10):
    return {'text': text, 'x': x, 'y': y, 'width': width, 'height': height}


def test_coordinate_layout_keeps_indentation_spaces_and_blank_rows():
    blocks = [{'characters': [char('a', 20), char('b', 40)]},
              {'characters': [char('Ж', 60, 30), char('🙂', 70, 30)]},
              {'characters': [char('c', 20, 70)]}]
    assert reconstruct_characters(blocks) == 'a b\n    Ж🙂\n\nc'


def test_layout_joins_fragments_and_deduplicates_only_same_overlapping_glyph():
    blocks = [{'characters': [char('a', 20), char('{', 30)]},
              {'characters': [char('{', 31), char('{', 40), char('b', 50)]}]
    assert reconstruct_characters(blocks) == 'a{{b'
    blocks[1]['characters'][0]['text'] = '}'
    assert reconstruct_characters(blocks) == 'a{}{b'


def test_empty_layout_does_not_invent_text():
    assert reconstruct_characters([]) == ''
    assert reconstruct_characters([{'characters': []}]) == ''
