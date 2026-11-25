"""Offline public-output replay and bounded qualifier counterexamples."""
import copy
import json
import unittest
from pathlib import Path

from backend.app import memory_v2_synopsis as synopsis
from backend.app import memory_v2_synopsis_qualifiers as qualifiers


FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "a34_e2_qualifier_public_replay.json")
    .read_text(encoding="utf-8")
)


def rewrite(*, changes):
    original = FIXTURE["publicOutput"]
    segments = [
        original["text"][u["start"]:u["end"]]
        for u in original["units"]
    ]
    for index, value in changes.items():
        segments[index] = value
    output = copy.deepcopy(original)
    output["text"] = "".join(segments)
    cursor = 0
    for unit, segment in zip(output["units"], segments):
        unit["start"], unit["end"] = cursor, cursor + len(segment)
        cursor += len(segment)
    return output


def candidate(output):
    return synopsis.validate_integrated_final_output(
        output, FIXTURE["sources"], FIXTURE["parts"],
        qualifier_policy=qualifiers.VERSION,
    )


class PublicReplay(unittest.TestCase):
    def test_historical_rejection_is_not_rewritten(self):
        self.assertEqual(FIXTURE["historicalOutcome"]["status"], "failed")
        self.assertIn("synopsis_qualifier_dropped",
                      FIXTURE["historicalOutcome"]["reason"])
        with self.assertRaisesRegex(ValueError, "synopsis_qualifier_dropped"):
            synopsis.validate_integrated_final_output(
                FIXTURE["publicOutput"], FIXTURE["sources"], FIXTURE["parts"])

    def test_candidate_replays_actual_public_output_only(self):
        checked = candidate(FIXTURE["publicOutput"])
        self.assertEqual(checked["text"], FIXTURE["publicOutput"]["text"])
        self.assertEqual(len(checked["units"]), 11)

    def test_extra_citation_and_linked_previous_unit(self):
        # A broad citation includes the correction, while the prior adjacent
        # unit cites the same range and states the correction explicitly.
        output = rewrite(changes={
            2: "白色卡纸尚未购买，名称未对外发布。"
        })
        self.assertEqual(candidate(output)["text"], output["text"])

    def test_unbound_other_unit_does_not_supply_correction(self):
        output = rewrite(changes={
            1: "用户谈到白色卡纸和展牌排版，名称暂写为「降雨观察草案」。",
            2: "白色卡纸尚未购买，名称未对外发布。",
        })
        with self.assertRaisesRegex(ValueError, "synopsis_qualifier_dropped"):
            candidate(output)

    def test_omitted_old_name_correction_rejected(self):
        output = rewrite(changes={
            0: "用户为社区科学展准备说明，主题仍在讨论。"
        })
        with self.assertRaisesRegex(ValueError, "synopsis_correction_relation_dropped"):
            candidate(output)

    def test_tentative_white_becoming_certain_rejected(self):
        output = rewrite(changes={
            1: "用户随后更正为已经确定采用白色卡纸，展示名称暂改为「降雨观察草案」。"
        })
        with self.assertRaisesRegex(ValueError, "synopsis_qualifier_conflict"):
            candidate(output)

    def test_unapproved_becoming_approved_rejected(self):
        output = rewrite(changes={
            9: "用户决定暂缓提交，日文备注表示已经获批。"
        })
        with self.assertRaisesRegex(ValueError, "synopsis_qualifier_conflict"):
            candidate(output)

    def test_unrelated_negation_cannot_license_approval(self):
        output = rewrite(changes={
            9: "用户不提交；日文备注表示已经获批。"
        })
        with self.assertRaisesRegex(ValueError, "synopsis_qualifier_conflict"):
            candidate(output)

    def test_real_test_condition_cannot_disappear(self):
        output = rewrite(changes={
            8: "助手强调，若之后拿到许可，就提交；现在不能写成展品已可展示。"
        })
        with self.assertRaisesRegex(ValueError, "synopsis_condition_dependency_dropped"):
            candidate(output)

    def test_friends_device_cannot_become_users(self):
        output = rewrite(changes={
            5: "友人说用户的装置已经运转，用户也未看到测试记录。"
        })
        with self.assertRaisesRegex(ValueError, "synopsis_attribution_conflict"):
            candidate(output)


class IndependentWording(unittest.TestCase):
    @staticmethod
    def _single(source, text):
        ref = {"kind": "message", "id": "1", "speaker": "user",
               "start": 0, "end": len(source), "quote": source}
        return ({"text": text,
                 "units": [{"start": 0, "end": len(text), "evidence": [ref]}],
                 "sourceCoverage": [{"kind": "message", "id": "1", "used": True}]},
                [{"kind": "message", "id": "1", "speaker": "user",
                  "text": source, "sourceStatus": "imported"}])

    def test_same_proposition_paraphrase_on_unrelated_subject(self):
        output, sources = self._single(
            "供应商报价单写明：它不表示已经付款。",
            "供应商报价单并非已经付款。",
        )
        with self.assertRaisesRegex(ValueError, "synopsis_qualifier_dropped"):
            synopsis.validate_output(output, sources)
        self.assertEqual(
            synopsis.validate_output(
                output, sources, qualifier_policy=qualifiers.VERSION)["text"],
            output["text"])

    def test_changed_subject_does_not_pass_shared_proposition(self):
        output, sources = self._single(
            "甲方合同不表示已经付款。",
            "乙方合同并非已经付款。",
        )
        with self.assertRaisesRegex(ValueError, "synopsis_qualifier_dropped"):
            synopsis.validate_output(
                output, sources, qualifier_policy=qualifiers.VERSION)

    def test_missing_conjunct_on_unrelated_topic(self):
        output, sources = self._single(
            "若合同签字并完成付款，再寄出设备。",
            "若合同签字，就寄出设备。",
        )
        with self.assertRaisesRegex(ValueError, "synopsis_condition_dependency_dropped"):
            synopsis.validate_output(
                output, sources, qualifier_policy=qualifiers.VERSION)


    def test_changed_subject_with_shared_topic_anchor(self):
        output, sources = self._single(
            "甲方合同写明：它不表示已经付款。",
            "乙方合同写明，并非已经付款。",
        )
        with self.assertRaisesRegex(ValueError, "synopsis_qualifier_dropped"):
            synopsis.validate_output(
                output, sources, qualifier_policy=qualifiers.VERSION)


if __name__ == "__main__":
    unittest.main()
