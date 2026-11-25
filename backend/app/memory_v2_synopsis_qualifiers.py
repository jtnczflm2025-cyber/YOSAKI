"""Candidate-only bounded proposition checks for integrated Synopsis evidence.

This module does not certify semantic fidelity. Ambiguous rewrites retain the
legacy rejection. The version is not active in persisted tasks or read paths.
"""
import re

VERSION = "synopsis-qualifier-proposition-v2"
VERSION_V3 = "synopsis-qualifier-proposition-v3"
_HAN_RUN = re.compile(r"[\u4e00-\u9fffA-Za-z0-9]{4,}")
_CLAUSE_SPLIT = re.compile(r"[，,；;。]")
_TENTATIVE = re.compile(r"(?:考虑|暂定|待确认)([\u4e00-\u9fff]{2,10})")
_FINAL_CHOICE = re.compile(r"决定|确定|采用|选定|改用")
_APPROVAL_NEGATIVE = re.compile(r"(?:不表示|不代表|不意味着|并非|尚未|未|没有)(?:已经)?获批")
_APPROVAL_AFFIRMATIVE = re.compile(r"(?:已经|已)?获批")
_CONDITION = re.compile(r"(?:如果|若)([^，,；;。]{1,80})[，,](?:再|才|就)([^；;。]{1,80})")
_FRIEND_PROPERTY = re.compile(
    r"(?:友人|朋友|同事|第三人).{0,50}(?:他|她|对方)(?:的)?(装置|设备|机器|作品)"
)
_CORRECTED_NAME = re.compile(r"从「([^」]{1,40})」[^。]{0,30}?(?:暂)?改为「([^」]{1,40})」")


def _anchor(*values):
    """Require the same concrete span beyond the qualifier itself."""
    for run in _HAN_RUN.findall(values[0]):
        for i in range(len(run) - 3):
            part = run[i:i + 4]
            if all(part in value for value in values[1:]):
                return True
    return False


def _negative_paraphrase(segment, quotes):
    """A narrow same-proposition form: 'does not mean X' -> 'is not X'."""
    for quote in quotes:
        match = re.search(r"不(?:表示|代表|意味着)([^，,；;。]{2,30})", quote)
        if match is None:
            return False
        proposition = match.group(1)
        subject = re.match(r"([\u4e00-\u9fffA-Za-z0-9]{2,20})(?:是|写明|注明|说明|表示|不)", quote)
        if subject is None or subject.group(1) not in segment:
            return False
        if ("并非" + proposition) not in segment:
            return False
        if segment.find(subject.group(1)) > segment.find("并非" + proposition):
            return False
        if not _anchor(quote.replace(proposition, ""),
                       segment.replace(proposition, "")):
            return False
    return bool(quotes)


def _negative_paraphrase_v3(segment, quotes):
    """A bounded, same-clause 'does not mean approved' restatement.

    The exact proposition and an independent subject anchor must survive.
    A negative word elsewhere in the output never satisfies this check.
    """
    for quote in quotes:
        match = re.search(r"不(?:表示|代表|意味着)(已经获批|已获批|获批)", quote)
        if match is None:
            return False
        proposition = match.group(1)
        rendered = re.search(r"(?:并非|不表示|不代表|不意味着)" +
                             re.escape(proposition), segment)
        if rendered is None:
            return False
        # The shared concrete span must identify the same statement, beyond
        # the generic approval proposition. Quoted notes can be the anchor.
        source_context = re.split(r'[，,；;。]', quote[:match.start()].rstrip('，,；;。'))[-1]
        output_context = re.split(r'[，,；;。]', segment[:rendered.start()].rstrip('，,；;。'))[-1]
        if not _anchor(source_context, output_context):
            return False
        if _APPROVAL_AFFIRMATIVE.search(segment) and not _APPROVAL_NEGATIVE.search(segment):
            return False
    return bool(quotes)


def _conditional_after_v3(segment, quotes):
    """Only a same-order A-and-B-then-discuss-whether-C rewrite is supported.

    This does not infer that a condition happened. Each prerequisite and the
    deliberative consequent must occur in one contiguous output clause.
    """
    for quote in quotes:
        match = re.search(r"(?:若|如果)(?:之后)?([^，,；;。]{2,80})[，,]再(讨论是否[^；;。]{2,40})", quote)
        if match is None:
            return False
        before, after = match.groups()
        if not re.search(r"(?:并|且|和)", before):
            return False
        if not re.search(re.escape(before) + r"后再" + re.escape(after), segment):
            return False
        if re.search(r"(?:即可|可以|已经|已)(?:直接)?提交|提交(?:完成|成功)", segment):
            return False
    return bool(quotes)


def _correction_paraphrase(output, index, refs, quotes):
    unit = output["units"][index]
    segment = output["text"][unit["start"]:unit["end"]]
    for ref, quote in zip(refs, quotes):
        if "修订" in segment and re.search(r"原|旧|先|后|改", segment) and _anchor(quote, segment):
            continue
        if index == 0:
            return False
        previous = output["units"][index - 1]
        previous_text = output["text"][previous["start"]:previous["end"]]
        if (ref not in previous["evidence"] or
                not re.search(r"更正|修订|改为", previous_text) or
                not _anchor(quote, segment, previous_text)):
            return False
    return bool(quotes)


def _assert_propositions(segment, quotes):
    clauses = [part for part in _CLAUSE_SPLIT.split(segment) if part.strip()]
    for quote in quotes:
        if _APPROVAL_NEGATIVE.search(quote):
            for clause in clauses:
                if _APPROVAL_AFFIRMATIVE.search(clause) and not _APPROVAL_NEGATIVE.search(clause):
                    raise ValueError("synopsis_qualifier_conflict")
        for match in _TENTATIVE.finditer(quote):
            topic = match.group(1)
            for clause in clauses:
                if (topic in clause and _FINAL_CHOICE.search(clause) and
                        not re.search(r"考虑|暂|待确认|可能|拟", clause)):
                    raise ValueError("synopsis_qualifier_conflict")
        if re.search(r"(?:没有|未|尚未)(?:实际)?测试", quote):
            for clause in clauses:
                if re.search(r"(?:已经|已|完成).{0,4}测试|测试(?:已)?完成|测试通过", clause):
                    if not re.search(r"没有|尚未|未|不", clause):
                        raise ValueError("synopsis_qualifier_conflict")
        conditional = _CONDITION.search(quote)
        if conditional:
            consequence = re.findall(r"[\u4e00-\u9fff]{2,}", conditional.group(2))
            head = consequence[-1][-2:] if consequence else ""
            rendered = _CONDITION.search(segment)
            if head and head in segment and rendered:
                parts = [x for x in re.split(r"并|且|和", conditional.group(1)) if x]
                required = [m[-1][-2:] for x in parts
                            if (m := re.findall(r"[\u4e00-\u9fff]{2,}", x))]
                if any(item not in rendered.group(1) for item in required):
                    raise ValueError("synopsis_condition_dependency_dropped")
        friend = _FRIEND_PROPERTY.search(quote)
        if friend and re.search(
                r"(?:用户|我)(?:的|这台)?" + friend.group(1) +
                r".{0,4}(?:已|已经|完成|通过|运转)", segment):
            raise ValueError("synopsis_attribution_conflict")


def validate_unit(output, index, refs, quotes, marker_groups, *, policy=VERSION):
    if policy not in (VERSION, VERSION_V3):
        raise ValueError('synopsis_qualifier_policy_invalid')
    unit = output["units"][index]
    segment = output["text"][unit["start"]:unit["end"]]
    if policy == VERSION_V3:
        actor = re.match(r'^\s*(用户|助手)(?:[：:，,\s]|(?=在|说|建议|表示|提醒|提出|未|没|不|更正|决定|还))',
                         segment)
        if actor and ('user' if actor.group(1) == '用户' else 'assistant') not in {
                ref['speaker'] for ref in refs}:
            raise ValueError('synopsis_attribution_conflict')
    _assert_propositions(segment, quotes)
    for group_index, group in enumerate(marker_groups):
        relevant = [quote for quote in quotes if any(marker in quote for marker in group)]
        if not relevant or any(marker in segment for marker in group):
            continue
        if group_index == 0 and (_negative_paraphrase(segment, relevant) or
                                 (policy == VERSION_V3 and
                                  _negative_paraphrase_v3(segment, relevant))):
            continue
        if group_index == 1 and policy == VERSION_V3 and _conditional_after_v3(segment, relevant):
            continue
        if group_index == 2 and _correction_paraphrase(
                output, index,
                [ref for ref, quote in zip(refs, quotes) if quote in relevant],
                relevant):
            continue
        raise ValueError("synopsis_qualifier_dropped")


def validate_global(output):
    """Names with an explicit old->new correction need an anchored transition."""
    for unit in output["units"]:
        for ref in unit["evidence"]:
            match = _CORRECTED_NAME.search(ref["quote"])
            if not match:
                continue
            old, new = match.groups()
            text = output["text"]
            if new not in text:
                continue  # An omitted subject is a human-review question.
            old_units = [i for i, row in enumerate(output["units"])
                         if old in text[row["start"]:row["end"]]]
            new_units = [i for i, row in enumerate(output["units"])
                         if new in text[row["start"]:row["end"]]]
            if not old_units or not any(
                    oi <= ni and re.search(
                        r"更正|修订|改为|从.+到|先.+后",
                        text[output["units"][ni]["start"]:output["units"][ni]["end"]])
                    for oi in old_units for ni in new_units):
                raise ValueError("synopsis_correction_relation_dropped")
