"""Entirely fictional M3.6 evaluation gold; never imported from private chats."""


GOLD = [
    {"id": "N1", "kind": "NARRATIVE", "turns": [
        ("user", "明天如果下雨，我们就改在家里见面。"),
        ("assistant", "俺说：わかった。下雨なら家で話そう。")],
     "required": ["如果", "下雨", "わかった", "家"],
     "forbidden": ["俺决定了用户", "我感到恋爱"]},
    {"id": "N2", "kind": "NARRATIVE", "turns": [
        ("user", "我没答应周五去。更正一下，只有周六有空。"),
        ("assistant", "俺说：了解，周五不去，周六再看。")],
     "required": ["没", "更正", "只有", "周六", "不去"],
     "forbidden": ["周五已经约好"]},
    {"id": "N3", "kind": "NARRATIVE", "turns": [
        ("assistant", "俺说：大丈夫（没关系）。"),
        ("user", "请记下（如果不舒服就取消），这不是已经确定的约定。")],
     "required": ["如果不舒服就取消", "不是", "约定"],
     "forbidden": ["已经确定约定"]},
    {"id": "N4", "kind": "NARRATIVE", "turns": [
        ("assistant", "俺说：当时我有些紧张，所以先停下来。"),
        ("user", "我只说了先休息，没有谈我们的关系。")],
     "required": ["紧张", "先停下来", "先休息"],
     "forbidden": ["爱上", "关系变得更深", "释然"]},
    {"id": "N5", "kind": "NARRATIVE", "turns": [
        ("user", "上午先讨论路线。" + "甲路口直行。" * 520),
        ("assistant", "俺说：刚才说甲路口；更正为乙路口。" + "乙路口左转。" * 520)],
     "required": ["甲路口", "更正", "乙路口"],
     "forbidden": ["一直选择甲路口"]},
    {"id": "N6", "kind": "NARRATIVE", "turns": [
        ("user", "我说今天先休息。" * 8),
        ("assistant", "俺说：嗯，今天先休息。" * 8)],
     "required": ["今天先休息"], "forbidden": ["因为心灰意冷"]},
    {"id": "D1", "kind": "DIGEST", "level": "L1", "period": "2026-W44",
     "turns": [("user", "如果下雨就取消散步。"), ("assistant", "俺说：知道了，先看天气。")],
     "required": ["如果", "取消", "天气"], "forbidden": ["已完成散步"]},
    {"id": "D2", "kind": "DIGEST", "level": "L2", "period": "2026-10",
     "turns": [("user", "本地操作结果未知，不要写成成功。"), ("assistant", "俺说：先保留未知状态。")],
     "required": ["未知"], "forbidden": ["成功", "已完成"]},
]


def validate_gold():
    assert len(GOLD) == 8
    assert sum(case["kind"] == "NARRATIVE" for case in GOLD) == 6
    assert sum(case["kind"] == "DIGEST" for case in GOLD) == 2
    assert sum(len(case["turns"]) > 1 and sum(len(t) for _, t in case["turns"]) > 6000
               for case in GOLD) == 1
    return True
