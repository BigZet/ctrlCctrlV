"""Experimental layout reconstruction from OCR character boxes, without fixtures."""
from statistics import median


def reconstruct_characters(blocks):
    characters = [char for block in blocks for char in block['characters'] if char['text']]
    if not characters:
        return ''
    characters.sort(key=lambda char: (char['y'], char['x']))
    heights = [char['height'] for char in characters if char['height'] > 0]
    widths = [char['width'] / len(char['text']) for char in characters if char['width'] > 0]
    height = median(heights) if heights else 1
    cell = median(widths) if widths else 1
    rows = []
    for char in characters:
        if rows and abs(char['y'] - median(item['y'] for item in rows[-1])) < height * 0.5:
            rows[-1].append(char)
        else:
            rows.append([char])
    centers = [median(char['y'] for char in row) for row in rows]
    gaps = [b-a for a,b in zip(centers,centers[1:]) if b-a >= height*0.6]
    # Blank rows increase some gaps; use the lower cluster as the row pitch.
    step = median(gap for gap in gaps if gap <= median(gaps)) if gaps else height*1.4
    origin = min(char['x'] for char in characters)
    output = []
    for index, row in enumerate(rows):
        if index:
            output.extend([''] * max(0, round((centers[index]-centers[index-1])/max(step,0.01))-1))
        line, previous = '', None
        for char in sorted(row,key=lambda item:item['x']):
            if previous is None:
                line += ' ' * max(0,round((char['x']-origin)/max(cell,0.01)))
            else:
                overlap = min(previous['x']+previous['width'],char['x']+char['width'])-max(previous['x'],char['x'])
                if char['text'] == previous['text'] and overlap > min(previous['width'],char['width'])*0.5:
                    continue
                gap = char['x']-(previous['x']+previous['width'])
                line += ' ' * max(0,round(gap/max(cell,0.01)))
            line += char['text']
            previous = char
        output.append(line)
    return '\n'.join(output)
