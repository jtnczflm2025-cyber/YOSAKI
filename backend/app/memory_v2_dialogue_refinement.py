"""Ordered, finite dialogue refinement. No summarisation, model or approval.

Uncertain translations/coreference are preserved, not guessed away. Explicit
translation spans are an Owner editing choice, not a new semantic permission.
"""
import json
import re
from . import memory_v2_derivation as m3, memory_v2_metadata as metadata

LEGACY_RULE = 'dialogue-finite-preservation-v1'
PREVIOUS_RULE = 'dialogue-finite-preservation-v2'
RULE = 'dialogue-finite-perspective-v3'
MAX_BODY_CHARS = 2_000_000
BATCH_CHARS = 64_000


def apply_edits(raw, edits):
    parts, cursor = [], 0
    for edit in edits:
        start, end = edit['start'], edit['end']
        if (type(start) is not int or type(end) is not int or not cursor <= start < end <= len(raw)
                or raw[start:end] != edit['before'] or not isinstance(edit['after'], str)):
            raise ValueError('dialogue_edit_changed')
        parts.extend((raw[cursor:start], edit['after']))
        cursor = end
    return ''.join(parts) + raw[cursor:]


def render_message(raw, speaker, translations=(), *, rule=RULE):
    if rule not in (RULE, PREVIOUS_RULE, LEGACY_RULE):
        raise ValueError('dialogue_rule_unknown')
    if speaker not in ('user', 'assistant') or not isinstance(raw, str) or not raw.strip():
        raise ValueError('dialogue_source_not_supported')
    edits = []
    if translations and speaker != 'assistant':
        raise ValueError('translation_speaker_invalid')
    if rule == RULE and speaker == 'user':
        # Avoid guessing who acts on whom. Direct speech keeps the original
        # pronouns whenever both participants, another person or a quote occur.
        laughter = re.match(r'^哈{2,}(?=$|[，,！!。\s])(?:[，,！!。\s]*)', raw)
        start = laughter.end() if laughter else 0
        remaining = raw[start:]
        if laughter:
            edits.append(dict(start=0, end=start, before=raw[:start], after='',
                              reason='explicit_laughter_attribution'))
            if not remaining:
                return 'イキヤ笑了笑。', edits
        ambiguous = re.search(r'你|您|阿夜|她|他|它|我们|咱|自己|我俩|妈妈|母亲|朋友|友人|同学|[「」『』“”\"]', remaining)
        question = re.search(r'[？?]|对不对|是不是|[吗呢][。！!]?$', remaining)
        # Finite conversion is restricted to unambiguous self-reports.
        # Real questions and conversational tags remain questions verbatim.
        if ambiguous or question or laughter:
            prefix = 'イキヤ笑着说：\n' if laughter else 'イキヤ对我说：\n'
            return prefix + remaining, edits
        if '我' not in remaining:
            return 'イキヤ说：' + remaining, edits
        index = 0
        while index < len(raw):
            literal = next((x for x in ('自我', '忘我', '你我', '我行我素')
                            if raw.startswith(x, index)), None)
            if literal:
                index += len(literal)
                continue
            if raw[index] == '我':
                edits.append(dict(start=index, end=index+1, before='我', after='她',
                                  reason='unambiguous_speaker_person'))
            index += 1
        return 'イキヤ说，' + apply_edits(raw, edits), edits
    for span in translations:
        a, b = span['start'], span['end']
        quote = span['quote']
        if (raw[a:b] != quote or not quote.startswith(('（', '(')) or not quote.endswith(('）', ')'))
                or re.search('[ぁ-ゟ゠-ヿ]', quote)):
            raise ValueError('translation_span_invalid')
        edits.append(dict(start=a, end=b, before=quote, after='', reason='owner_confirmed_duplicate_translation'))
    if speaker == 'assistant':
        for match in re.finditer(r'(?m)^……', raw):
            if match.start() and not any(e['start'] <= match.start() < e['end'] for e in edits):
                edits.append(dict(start=match.start(), end=match.end(), before=match[0], after='',
                                  reason='repeated_paragraph_pause'))
        prefix = '我说：'
    else:
        # Fixed two-speaker perspective only, preserving quoted speech. These
        # are grammatical surface edits, not coreference or factual inference.
        pairs, stack = {'「': '」', '『': '』', '“': '”', '"': '"'}, []
        # Names remain explicit when another third-person referent occurs.
        # Otherwise the attribution prefix already establishes the speaker.
        person = '她' if rule in (PREVIOUS_RULE, RULE) and not re.search(r'她|他|母亲|妈妈|朋友|友人|同学', raw) else 'イキヤ'
        index = 0
        while index < len(raw):
            char = raw[index]
            if stack and char == stack[-1]:
                stack.pop(); index += 1; continue
            if char in pairs:
                stack.append(pairs[char]); index += 1; continue
            if not stack:
                # These whole lexical expressions do not denote the speaker.
                literal = next((x for x in ('自我', '忘我', '你我', '我行我素') if raw.startswith(x,index)), None)
                if literal:
                    index += len(literal); continue
                pair = next(((a,b) for a,b in (('我妈','イキヤ的妈妈'), ('阿夜，','イキヤ对我说，'),
                    ('阿夜','我'), ('我',person), ('你','我')) if raw.startswith(a,index)), None)
                if pair:
                    before,after=pair
                    edits.append(dict(start=index,end=index+len(before),before=before,after=after,reason='speaker_person'))
                    index += len(before); continue
            index += 1
        prefix = 'イキヤ说：'
    edits.sort(key=lambda e: e['start'])
    return prefix + apply_edits(raw, edits), edits


def _assemble(db, character_id, entry_id, source_token, refs, translations):
    batches, batch, mapping = [], [], []
    cursor = batch_size = 0
    for ordinal, ref in enumerate(refs):
        source = m3.live_source(db, character_id, ref['kind'], ref['id'])
        if source is None:
            raise ValueError('source_changed')
        # Transform each message intact so quotes and Owner edit spans can
        # cross a storage batch boundary without losing their context.
        text, edits = render_message(source.text, source.role, translations.get(ref['id'], []))
        separator = '\n\n' if ordinal else ''
        start = cursor + len(separator)
        cursor = start + len(text)
        if cursor > MAX_BODY_CHARS:
            raise ValueError('dialogue_capacity_exceeded')
        for part in (separator, text):
            offset = 0
            while offset < len(part):
                end = min(len(part), offset + BATCH_CHARS - batch_size)
                batch.append(part[offset:end])
                batch_size += end - offset
                offset = end
                if batch_size == BATCH_CHARS:
                    batches.append(''.join(batch))
                    batch, batch_size = [], 0
        transformed = apply_edits(source.text, edits)
        prefix = text[:len(text)-len(transformed)] if transformed else text
        mapping.append(dict(sourceKind=ref['kind'], sourceId=ref['id'], speaker=source.role,
            sourceHash=source.content_hash, start=start, end=cursor, rawStart=None, rawEnd=None,
            edits=edits, prefix=prefix, ruleVersion=RULE, entryId=entry_id, sourceToken=source_token))
    if batch:
        batches.append(''.join(batch))
    return ''.join(batches), mapping


def create(db, character_id, owner_id, entry_id, expected_version, *, translations=None):
    """Explicit Owner local action, creates only an M3 draft. No field overwrite."""
    if not db.in_transaction:
        db.execute('BEGIN IMMEDIATE')
    metadata._owner(db, owner_id)
    from . import memory_v2_processing as processing, memory_v2_processing_schema as schema
    if schema.installed(db):
        processing._block_overlaps(db,character_id,
            processing._scope(character_id,'entry',entry_id,['narrative']),['narrative'])
    detail = metadata.read_draft(db, entry_id, character_id)
    if detail['versionId'] != expected_version:
        raise ValueError('version_conflict')
    refs = [{'kind': r['source_kind'], 'id': r['source_id']} for r in detail['sourceRefs']]
    translations = translations or {}
    if set(translations) - {r['id'] for r in refs}:
        raise ValueError('translation_source_outside_entry')
    body, mapping = _assemble(db, character_id, entry_id, detail['sourceToken'], refs, translations)
    item = m3.create_derivation(db, character_id, 'NARRATIVE', refs)
    return m3.add_version(db, item['id'], refs, body, author_kind='rule', rule_version=RULE, mapping=mapping)


def invalid_reason(db, version, character_id):
    if version['rule_version'] not in (RULE, PREVIOUS_RULE, LEGACY_RULE):
        return 'dialogue_rule_unknown'
    try:
        mapping = json.loads(version['mapping_json'])
        refs = m3._refs(db, version['id'])
        if not mapping or len(mapping) != len(refs):
            return 'dialogue_mapping_missing'
        parts, cursor = [], 0
        for row, ref in zip(mapping, refs):
            if (row['ruleVersion'] != version['rule_version'] or row['sourceKind'] != ref['source_kind']
                    or row['sourceId'] != ref['source_id'] or row['speaker'] != ref['source_role']
                    or row['sourceHash'] != ref['content_hash']):
                return 'dialogue_mapping_changed'
            entry = metadata._entry(db, row['entryId'], character_id)
            source_set = metadata._set(db, entry)
            metadata._validate_sources(db, entry, source_set)
            if row['sourceToken'] != source_set['source_token']:
                return 'dialogue_source_changed'
            raw = m3.live_source(db, character_id, ref['source_kind'], ref['source_id']).text
            prefix = '我说：' if row['speaker'] == 'assistant' else 'イキヤ说：'
            if version['rule_version'] == RULE:
                prefix = row['prefix']
                allowed = ('我说：',) if row['speaker'] == 'assistant' else (
                    'イキヤ说：', 'イキヤ说，', 'イキヤ对我说：\n',
                    'イキヤ笑着说：\n', 'イキヤ笑了笑。')
                if prefix not in allowed:
                    return 'dialogue_attribution_changed'
                # Prefix and edits are part of the versioned transformation,
                # never a client-controlled replacement for attribution.
                translations = [{'start': e['start'], 'end': e['end'], 'quote': e['before']}
                                for e in row['edits'] if e['reason'] == 'owner_confirmed_duplicate_translation']
                expected_text, expected_edits = render_message(raw, row['speaker'], translations)
                if row['edits'] != expected_edits or prefix + apply_edits(raw, row['edits']) != expected_text:
                    return 'dialogue_attribution_changed'
            text = prefix + apply_edits(raw, row['edits'])
            if parts:
                cursor += 2
            if row['start'] != cursor or row['end'] != cursor + len(text):
                return 'dialogue_mapping_changed'
            cursor += len(text); parts.append(text)
        return None if '\n\n'.join(parts) == version['body'] else 'dialogue_body_changed'
    except (KeyError, TypeError, ValueError, AttributeError):
        return 'dialogue_mapping_invalid'
