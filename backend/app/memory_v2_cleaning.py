"""Conservative, versioned cleaning. Unknown translations and code stay verbatim."""
import re

RULE = 'conservative-v1'
# Closed reviewed equivalence, not a general-purpose translation detector.
PAIRS = {'大丈夫（没关系）': '大丈夫', '大丈夫(没关系)': '大丈夫'}


def clean_text(text, *, translation=True, line_endings=True):
    cleaned = text.replace('\r\n', '\n') if line_endings else text
    # Only whole standalone utterances. Never infer equivalence inside quoted/code prose.
    if translation and '\n' not in cleaned and '`' not in cleaned:
        match = re.fullmatch(r'(大丈夫[（(]没关系[）)])([。！!？?]?)', cleaned)
        if match and match[1] in PAIRS:
            cleaned = PAIRS[match[1]] + match[2]
    # Linear prefix/suffix mapping: repeated long messages must not incur quadratic diff.
    prefix = 0
    while prefix < min(len(text), len(cleaned)) and text[prefix] == cleaned[prefix]:
        prefix += 1
    suffix = 0
    while (suffix < min(len(text), len(cleaned)) - prefix
           and text[len(text) - suffix - 1] == cleaned[len(cleaned) - suffix - 1]):
        suffix += 1
    opcodes = [('equal', 0, prefix, 0, prefix)]
    if prefix < max(len(text), len(cleaned)) - suffix:
        opcodes.append(('replace', prefix, len(text) - suffix, prefix, len(cleaned) - suffix))
    if suffix:
        opcodes.append(('equal', len(text) - suffix, len(text), len(cleaned) - suffix, len(cleaned)))
    edits, spans = [], []
    for tag, a, b, c, d in opcodes:
        entry = {'rawStart': a, 'rawEnd': b, 'outputStart': c, 'outputEnd': d}
        if tag == 'equal':
            spans.append(entry)
        else:
            edits.append({**entry, 'operation': tag, 'before': text[a:b], 'after': cleaned[c:d]})
    return {'text': cleaned, 'edits': edits, 'spans': spans, 'ruleVersion': RULE}


def rule_name(translation=True, line_endings=True):
    return f'{RULE}:translation={int(translation)}:line_endings={int(line_endings)}'


def options(rule):
    match = re.fullmatch(RULE + r':translation=([01]):line_endings=([01])', rule)
    if not match:
        raise ValueError('unsupported_cleaning_rule')
    return {'translation': match[1] == '1', 'line_endings': match[2] == '1'}


def render(sources, rule):
    config = options(rule)
    parts, mapping = [], []
    offset = 0
    for source in sources:
        result = clean_text(source.text, **config)
        # Role and time remain explicit: a dialogue is not flattened into one speaker.
        header = f'[{source.role} | {source.kind}:{source.source_id} | {source.occurred_at or "发生时间未知"}]\n'
        part = header + result['text']
        mapping.append({'sourceKind': source.kind, 'sourceId': source.source_id,
            'sourceRole': source.role, 'contentHash': source.content_hash,
            'bodyStart': offset + len(header), 'bodyEnd': offset + len(part), **result})
        parts.append(part)
        offset += len(part) + 2
    return '\n\n'.join(parts), mapping


def create_cleaned(db, character_id, refs, *, translation=True, line_endings=True):
    from . import memory_v2_derivation as core
    core.source_inputs(db, character_id, refs)
    sources = [core.live_source(db, character_id, r['kind'], str(r['id'])) for r in refs]
    rule = rule_name(translation, line_endings)
    body, mapping = render(sources, rule)
    item = core.create_derivation(db, character_id, 'CLEANED', refs)
    version = core.add_version(db, item['id'], refs, body, author_kind='rule', rule_version=rule)
    # No implicit activation; the caller explicitly requests it after preview.
    return {'version': version, 'diff': mapping}


def verified_body(sources, rule, body):
    try:
        expected, _ = render(sources, rule)
    except ValueError:
        return False
    return body == expected
