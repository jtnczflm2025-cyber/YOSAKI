"""Structural and bounded semantic checks; never certify free prose as true."""
import json
import re

NEGATIONS = ('不', '没', '無', 'ない', 'ぬ')
CONDITIONS = ('如果', '只有', '除非', 'なら', 'たら')
CORRECTIONS = ('更正', '其实', '訂正')
SUBJECTIVE = ('难过', '害怕', '心疼', '释然', '成熟', '成长', '爱上', '明白了自己的心')


def validate_chunk(inputs, response, *, kind):
    if not isinstance(response, dict) or response.get('finishReason') != 'stop':
        raise ValueError('model_output_truncated_or_unknown')
    data = response.get('output')
    try:
        parsed = json.loads(data) if isinstance(data, str) else data
    except (ValueError, TypeError):
        raise ValueError('model_output_invalid_json') from None
    if not isinstance(parsed, dict) or set(parsed) != {'items'} or not isinstance(parsed['items'], list):
        raise ValueError('model_output_invalid_shape')
    items = parsed['items']
    if len(items) != len(inputs):
        raise ValueError('source_coverage_invalid')
    warnings = []
    for idx, (source, item) in enumerate(zip(inputs, items)):
        if not isinstance(item, dict) or set(item) != {'sourceKind', 'sourceId', 'speaker', 'text'}:
            raise ValueError('model_output_invalid_shape')
        if (item['sourceKind'] != source['sourceKind'] or item['sourceId'] != source['sourceId']
                or item['speaker'] != source['speaker']):
            raise ValueError('source_order_or_speaker_invalid')
        text = item['text']
        if not isinstance(text, str) or not text.strip():
            raise ValueError('empty_model_segment')
        if kind == 'NARRATIVE':
            if '俺说' in text:
                raise ValueError('mixed_language_attribution')
            if re.match(r'\s*我告诉她(?=[:：，,\s])', text):
                raise ValueError('unsupported_attribution_prefix')
            if re.match(r'\s*(?:\[(?:message|activity_operation):|用户说|助手说)', text):
                raise ValueError('external_transcript_style')
            if (source['speaker'] == 'assistant' and
                    re.match(r'\s*我说[:：]\s*我说[:：]', text)):
                warnings.append('duplicate_first_person_prefix')
            if source['speaker'] == 'assistant' and re.match(r'\s*她说', text):
                raise ValueError('assistant_utterance_misattributed')
        if source['speaker'] == 'user' and re.search(r'(^|[。\n])\s*(?:我|俺)(?:说|觉得|经历)', text):
            raise ValueError('user_utterance_misattributed')
        if kind == 'NARRATIVE' and source['speaker'] in ('user', 'assistant'):
            expected_prefix = '她说' if source['speaker'] == 'user' else '我说'
            if not re.match(r'\s*' + expected_prefix, text):
                raise ValueError('narrative_attribution_prefix_required')
        original = source['content']
        if kind == 'DIGEST' and source['sourceStatus'] in ('unknown','failed') and re.search(r'成功|已完成|办妥|做到了|执行完毕',text):
            raise ValueError('unverified_operation_success')
        if any(w in original for w in NEGATIONS) and not any(w in text for w in NEGATIONS):
            raise ValueError('negation_dropped')
        if any(w in original for w in CONDITIONS) and not any(w in text for w in CONDITIONS):
            raise ValueError('condition_dropped')
        if any(w in original for w in CORRECTIONS) and not any(w in text for w in CORRECTIONS):
            raise ValueError('correction_dropped')
        if kind == 'NARRATIVE':
            for phrase in re.findall(r'[ぁ-ゖァ-ヺー]{2,}', original):
                if phrase not in text:
                    raise ValueError('japanese_expression_dropped')
            for bracketed in re.findall(r'[（(]([^（）()]+)[）)]', original):
                if bracketed not in text and original not in ('大丈夫（没关系）', '大丈夫(没关系)'):
                    warnings.append('parenthetical_content_changed_needs_review')
        if any(w in text and w not in original for w in SUBJECTIVE):
            raise ValueError('unsupported_subjective_claim')
        if idx == 0 and re.match(r'\s*(他|她|它|それ|これ)(?=[说做是、，。])', text):
            warnings.append('boundary_pronoun_ambiguous')
    return items, warnings


def join_checked(chunks):
    if not chunks:
        raise ValueError('missing_model_batch')
    all_items, seen, warnings = [], set(), []
    for chunk in chunks:
        for item in chunk['items']:
            key = (item['sourceKind'], item['sourceId'])
            if key in seen:
                raise ValueError('duplicate_source_across_batches')
            seen.add(key)
            all_items.append(item)
        warnings.extend(chunk.get('warnings', []))
    for before, after in zip(all_items, all_items[1:]):
        if before['text'] == after['text'] and (before['sourceKind'],before['sourceId']) != (after['sourceKind'],after['sourceId']):
            warnings.append('possible_repeated_narration')
    return all_items, warnings


def validate_synopsis(output, raw_sources):
    """A3.4 structural gate; owner still decides whole-entry semantic fidelity."""
    from . import memory_v2_synopsis
    return memory_v2_synopsis.validate_output(output, raw_sources)


def validate_temporal_chunk(inputs, response):
    """A3.6 finite guard. The Owner, not this check, judges digest fidelity."""
    if not isinstance(response, dict) or response.get('finishReason') != 'stop':
        raise ValueError('digest_result_unknown')
    data = response.get('output')
    if not isinstance(data, dict) or set(data) != {'items'} or not isinstance(data['items'], list):
        raise ValueError('digest_output_invalid_shape')
    items = data['items']
    if len(items) != len(inputs):
        raise ValueError('digest_output_coverage_invalid')
    for source, item in zip(inputs, items):
        if (not isinstance(item, dict) or set(item) != {'inputId', 'text'}
                or item.get('inputId') != source['inputId']
                or not isinstance(item.get('text'), str) or not item['text'].strip()):
            raise ValueError('digest_output_mapping_invalid')
        original, output = source['text'], item['text']
        if (any(phrase in output and phrase not in original for phrase in SUBJECTIVE)
                or ('未完成' in original and ('已经完成' in output or '已完成' in output))
                or ('不知道是否完成' in original and '已完成' in output)):
            raise ValueError('digest_unsupported_claim')
        if source['kind'] == 'raw':
            try:
                messages = json.loads(original)
                if (any(m.get('status') in ('unknown', 'failed') for m in messages)
                        and re.search(r'成功|已完成|办妥|做到了|执行完毕', output)):
                    raise ValueError('unverified_operation_success')
            except (TypeError, KeyError, AttributeError, json.JSONDecodeError):
                raise ValueError('digest_raw_input_invalid') from None
    return items
