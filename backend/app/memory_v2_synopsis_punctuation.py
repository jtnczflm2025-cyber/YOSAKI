"""Bounded Synopsis output-unit punctuation normalization.

Only the explicitly versioned batch profile may call this before the ordinary
structural and RAW evidence validator. Output offsets are Python Unicode code
point offsets. The caller persists the unmodified output and adjustment record.
"""

from copy import deepcopy


VERSION = 'synopsis-punctuation-boundary-v1'
_OPEN_TO_CLOSE = {
    '「': '」', '『': '』', '“': '”', '‘': '’',
    '(': ')', '（': '）', '[': ']', '【': '】',
}
_CLOSERS = set(_OPEN_TO_CLOSE.values())
_AMBIGUOUS_MARKS = set('。！？!?.．')


def _clear_positions(text):
    clear = []
    stack = []
    for char in text:
        clear.append(not stack)
        if char in _OPEN_TO_CLOSE:
            stack.append(_OPEN_TO_CLOSE[char])
        elif char in _CLOSERS:
            if not stack or stack[-1] != char:
                raise ValueError('punctuation_delimiter_ambiguous')
            stack.pop()
    clear.append(not stack)
    if stack:
        raise ValueError('punctuation_delimiter_ambiguous')
    return clear


def _safe_terminal(text, position, clear):
    if (not 0 < position < len(text)-1 or text[position] != '。' or
            not clear[position] or text[position-1].isspace() or
            text[position-1] in _AMBIGUOUS_MARKS or
            text[position-1] in _CLOSERS or text[position-1].isdigit() or
            text[position+1].isspace() or
            text[position+1] in _AMBIGUOUS_MARKS or
            text[position+1] in _OPEN_TO_CLOSE or
            text[position+1].isdigit()):
        raise ValueError('punctuation_boundary_ambiguous')


def normalize_terminal_boundaries_v1(output):
    """Return a copy, changing only uniquely attributable `。` boundaries.

No nearby search or substantive text edit is performed. The full output and
evidence validator must run on the returned copy even when no move was needed.
    """
    if (not isinstance(output, dict) or set(output) !=
            {'text', 'units', 'sourceCoverage'} or
            not isinstance(output['text'], str) or
            not isinstance(output['units'], list) or not output['units']):
        raise ValueError('punctuation_output_shape_invalid')
    text = output['text']
    clear = _clear_positions(text)
    result = deepcopy(output)
    units = result['units']
    cursor = 0
    for unit in units:
        if (not isinstance(unit, dict) or set(unit) != {'start', 'end', 'evidence'} or
                type(unit['start']) is not int or type(unit['end']) is not int or
                unit['start'] != cursor or not cursor < unit['end'] <= len(text)):
            raise ValueError('punctuation_intervals_invalid')
        cursor = unit['end']
    moves = []
    for i in range(len(units)-1):
        left, right = units[i], units[i+1]
        boundary = left['end']
        if boundary < len(text) and text[boundary] in _AMBIGUOUS_MARKS:
            if moves:
                raise ValueError('punctuation_too_many_adjustments')
            _safe_terminal(text, boundary, clear)
            if right['end'] <= boundary + 1:
                raise ValueError('punctuation_boundary_ambiguous')
            left['end'] = boundary + 1
            right['start'] = boundary + 1
            moves.append({'kind': 'adjacent_terminal', 'unit': i,
                          'codepoint': boundary, 'character': '。',
                          'from': i+1, 'to': i})
    end = units[-1]['end']
    if end != len(text):
        if end != len(text)-1 or text[end] != '。' or not clear[end]:
            raise ValueError('punctuation_unmapped_content')
        if (end == 0 or text[end-1].isspace() or
                text[end-1] in _AMBIGUOUS_MARKS or
                text[end-1] in _CLOSERS or text[end-1].isdigit()):
            raise ValueError('punctuation_boundary_ambiguous')
        units[-1]['end'] = len(text)
        moves.append({'kind': 'terminal_coverage', 'unit': len(units)-1,
                      'codepoint': end, 'character': '。'})
    return result, {'version': VERSION, 'moves': moves}
