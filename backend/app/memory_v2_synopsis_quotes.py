"""Exact RAW quote localization. No fuzzy search, text rewriting or permissions."""
from copy import deepcopy
import hashlib
import json

VERSION = 'raw-quote-unique-codepoints-v1'
REPAIR_VERSION = 'synopsis-local-evidence-interval-repair-v1'
ARTIFACT_CAP = 60000


def digest(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,
        separators=(',',':'),allow_nan=False).encode('utf8')).hexdigest()


def artifact(wire, normalized, adjustments):
    result={'kind':'synopsis_unique_raw_quotes_v1','ruleVersion':VERSION,
        'rawOutput':wire,'rawHash':digest(wire),'normalizedHash':digest(normalized),
        'adjustments':adjustments}
    if len(json.dumps(result,ensure_ascii=False,sort_keys=True,separators=(',',':'))) > ARTIFACT_CAP:
        raise ValueError('synopsis_quote_artifact_over_limit')
    return result


def checked_artifact(normalized, record, sources):
    if (not isinstance(record,dict) or set(record)!={'kind','ruleVersion','rawOutput','rawHash','normalizedHash','adjustments'} or
        record['kind']!='synopsis_unique_raw_quotes_v1' or record['ruleVersion']!=VERSION or
        record['rawHash']!=digest(record['rawOutput']) or record['normalizedHash']!=digest(normalized)):
        raise ValueError('synopsis_quote_binding_invalid')
    rebuilt,adjustments=resolve(record['rawOutput'],sources)
    if rebuilt!=normalized or adjustments!=record['adjustments']:
        raise ValueError('synopsis_quote_binding_invalid')
    artifact(record['rawOutput'],normalized,adjustments)
    return normalized


def _lookup(sources):
    result = {}
    for s in sources:
        key = (s['kind'], s['id'])
        if key in result or not isinstance(s['text'], str):
            raise ValueError('synopsis_quote_sources_invalid')
        result[key] = s
    return result


def locate(source, quote):
    if not isinstance(quote, str) or not quote or len(quote) > 12000:
        raise ValueError('synopsis_quote_invalid')
    first = source.find(quote)
    if first < 0:
        raise ValueError('synopsis_quote_not_found')
    if source.find(quote, first + 1) >= 0:
        raise ValueError('synopsis_quote_ambiguous')
    return first, first + len(quote)


def resolve(output, sources):
    """New wire format omits RAW start/end; Python code points are authoritative."""
    by_id = _lookup(sources)
    if not isinstance(output, dict) or set(output) != {'text','units','sourceCoverage'}:
        raise ValueError('synopsis_quote_output_invalid')
    if not isinstance(output['units'], list) or not 1 <= len(output['units']) <= 512:
        raise ValueError('synopsis_quote_units_invalid')
    normalized = deepcopy(output); adjustments = []
    for i,u in enumerate(normalized['units']):
        if not isinstance(u,dict) or set(u) != {'start','end','evidence'}:
            raise ValueError('synopsis_quote_unit_invalid')
        if not isinstance(u['evidence'],list) or not 1 <= len(u['evidence']) <= 64:
            raise ValueError('synopsis_quote_evidence_invalid')
        for j,e in enumerate(u['evidence']):
            if not isinstance(e,dict) or set(e) != {'kind','id','speaker','quote'}:
                raise ValueError('synopsis_quote_wire_shape_invalid')
            s=by_id.get((e['kind'],e['id']))
            if s is None or e['speaker'] != s['speaker']:
                raise ValueError('synopsis_quote_identity_invalid')
            a,b=locate(s['text'],e['quote']);e.update(start=a,end=b)
            adjustments.append({'unit':i,'evidence':j,'start':a,'end':b})
    return normalized,adjustments


def repair_legacy(output, sources, *, allow_escaped_newlines=False):
    """Explicit local repair of archived output; never upgrades a failed job."""
    by_id=_lookup(sources);normalized=deepcopy(output);changes=[]
    for i,u in enumerate(normalized['units']):
        for j,e in enumerate(u['evidence']):
            if set(e) != {'kind','id','start','end','quote','speaker'}:
                raise ValueError('synopsis_repair_shape_invalid')
            s=by_id.get((e['kind'],e['id']))
            if s is None or e['speaker']!=s['speaker']:
                raise ValueError('synopsis_quote_identity_invalid')
            if (type(e['start']) is int and type(e['end']) is int and
                0 <= e['start'] < e['end'] <= len(s['text']) and
                s['text'][e['start']:e['end']]==e['quote']):
                continue
            q=e['quote'];kind='interval_only'
            try:a,b=locate(s['text'],q)
            except ValueError as exc:
                # Opt-in for an archived double-escaped newline, never general
                # unicode_escape, whitespace folding, trimming or NFKC.
                if (str(exc)!='synopsis_quote_not_found' or not allow_escaped_newlines or
                    '\\n' not in q or '\\r' in q or '\\\\' in q):raise
                q=q.replace('\\n','\n');a,b=locate(s['text'],q);kind='literal_newline_escape'
            changes.append({'unit':i+1,'evidence':j+1,'sourceId':e['id'],
                'before':[e['start'],e['end']],'after':[a,b],
                'quoteChanged':q!=e['quote'],'kind':kind})
            e.update(start=a,end=b,quote=q)
    if normalized['text']!=output['text']:
        raise AssertionError('body_changed')
    return normalized,changes
