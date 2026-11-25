"""P1-A2 versioned contracts. Pure functions: no database, network or publication."""
import hashlib
import json
import unicodedata

CONTRACT = 'chronicle-fields-v2'
A3_CONTRACT = 'chronicle-processing-a3-v1'
A3_PURPOSES = ('browse', 'managementSearch', 'draftManagementSearch',
               'lexicalCandidateRecall', 'vectorCandidateRecall',
               'p2Projection', 'formalContext')
A3_DIGEST_PROFILES = ('raw_sources_v1', 'narrative_v1', 'synopsis_v1',
                      'child_digests_v1', 'composite_v1')
FIELDS = ('title', 'categories', 'tags', 'keywords', 'synopsis', 'narrative', 'time')
PROFILES = {'raw_sources_v1': (), 'synopsis_v1': ('synopsis',),
            'narrative_v1': ('narrative',)}
CATEGORIES = {
    'form': ('日常交流', '深入讨论', '技术讨论', '创作讨论'),
    'dynamic': ('约定', '分歧', '矛盾', '和解', '相互支持', '协作'),
    'domain': ('生活', '工作', '技术', '兴趣', '关系', '世界观'),
}


def token(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()


def normalized(value):
    return unicodedata.normalize('NFKC', value).casefold().strip()


def time_tokens(evidence, offset_minutes=480):
    """Evidence is ordered; import time never supplies occurrence or age."""
    if type(offset_minutes) is not int or not -840 <= offset_minutes <= 840:
        raise ValueError('invalid_projection_timezone')
    keys = ('kind', 'id', 'sourceOrdinal', 'rawTime', 'occurredAt', 'occurredDate',
            'precision', 'timezoneBasis')
    snapshots = [{key: row.get(key) for key in keys} for row in evidence]
    evidence_token = token(['time-evidence-v1', snapshots])
    return {'timeEvidenceToken': evidence_token,
            'timeToken': token(['time-projection-v1', evidence_token, offset_minutes]),
            'ageEligible': bool(snapshots) and all(
                (r['precision'] == 'instant' and r['occurredAt']) or
                (r['precision'] == 'day' and r['occurredDate']) for r in snapshots)}


def digest_token(profile, source_token, members, children, field_tokens=None, digest_revision=None):
    if profile not in PROFILES:
        raise ValueError('unsupported_digest_profile')
    fields = field_tokens or {}
    required = PROFILES[profile]
    if any(name not in fields for name in required):
        raise ValueError('digest_input_missing')
    return token(['digest-input-v1', profile, source_token, members, children,
                  {name: fields[name] for name in required}, digest_revision])


def digest_input_token_v2(manifest):
    """Bind the actual ordered inputs; v1 RAW token semantics stay unchanged."""
    if (not isinstance(manifest, dict) or
            manifest.get('profile') not in A3_DIGEST_PROFILES or
            not isinstance(manifest.get('members'), list) or not manifest['members'] or
            not isinstance(manifest.get('inputs'), list) or not manifest['inputs']):
        raise ValueError('invalid_digest_manifest')
    return token(['digest-input-v2', manifest])


def qualifications(*, source_valid, field_statuses, entry_published=False):
    """Formal integration remains disabled independently of local eligibility."""
    available = {name for name, status in field_statuses.items() if status == 'available'}
    candidate = bool(source_valid and entry_published and 'title' in available
                     and available & {'synopsis', 'narrative'})
    projected = bool(source_valid and entry_published and 'title' in available)
    return {'browse': bool(source_valid), 'search': bool(source_valid and available),
            'candidateRecall': candidate, 'p2Projection': projected,
            'formalContext': False}


def a3_qualifications(*, source_valid, field_statuses, entry_published=False,
                      authorized=True, lexical_match=False, vector_index_valid=False):
    """A3 purpose gates. A match/index cannot grant publication or source access."""
    valid = {key for key, value in field_statuses.items() if value == 'available'}
    readable = bool(authorized and source_valid)
    title = 'title' in valid
    published = bool(readable and entry_published and title)
    return {
        'browse': readable,
        'managementSearch': bool(readable and valid),
        'draftManagementSearch': readable,
        'lexicalCandidateRecall': bool(published and lexical_match and
                                       valid & {'tags', 'keywords', 'synopsis', 'narrative'}),
        'vectorCandidateRecall': bool(published and 'synopsis' in valid and vector_index_valid),
        'p2Projection': published,
        'formalContext': False,
    }
