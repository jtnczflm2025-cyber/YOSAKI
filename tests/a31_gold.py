"""Synthetic-only A3.1 finite-rule examples; no historical chat excerpts."""


def case(name, turns, expected=()):
    return {'name': name, 'turns': turns, 'expected': set(expected)}


GOLD = (
    case('C1', [('user', '这个 API 为什么返回 409？'), ('assistant', '因为游标绑定的来源版本变了。')],
         [('form', '技术讨论'), ('domain', '技术')]),
    case('C2', [('user', '先讨论脚本的错误怎么修。'), ('assistant', '检查请求体的字段。'),
                ('user', '再讨论短篇的结尾怎么改。'), ('assistant', '把最后一段留白。')],
         [('form', '技术讨论'), ('form', '创作讨论'), ('domain', '技术')]),
    case('C3', [('user', 'API の応答が 409 になる理由を調べたい。'),
                ('assistant', '参照元の版が変わったためです。')],
         [('form', '技术讨论'), ('domain', '技术')]),
    case('C4', [('user', '今晚做咖喱，味付けはどうする？'),
                ('assistant', '先放少量盐，再尝味道。')], [('domain', '生活')]),
    case('C5', [('user', '明天的工作报告要改排期。'), ('assistant', '先核对交付日期。'),
                ('user', '下班后我想练吉他，和弦怎么按？'), ('assistant', '先练这两个指位。')],
         [('domain', '工作')]),
    case('C6', [('user', '这次不是技术讨论，只是提到了一个软件名称。'),
                ('assistant', '那就先记软件名字。')]),
    case('C7', [('user', '我们讨论了和解，但没有和解。'),
                ('assistant', '知道了，不把它写成已经和好。')]),
    case('C8', [('user', '她引用别人说的“谢谢你支持我”。'),
                ('assistant', '这是引述，不能当作我们做过的事。')]),
    case('C9', [('user', '如果以后能和好就好了。'),
                ('assistant', '现在没有这样的确认。')]),
    case('C10', [('user', '哈哈，给我们颁个“最佳协作奖”吧。'),
                 ('assistant', '只是开玩笑。')]),
    case('C11', [('user', '这个 API 的状态码是什么？'),
                 ('assistant', '409 表示当前游标已过期。')],
         [('form', '技术讨论'), ('domain', '技术')]),
    case('C12', [('user', 'API 为什么返回 409？'),
                 ('assistant', '因为来源版本变了。')],
         [('form', '技术讨论'), ('domain', '技术')]),
    case('C13', [('user', '要改工作报告的排期。'),
                 ('assistant', '先核对交付日期。')]),
    case('C14', [('user', '嗯。'), ('assistant', '知道了。')]),
)

# C5b corrects the former interest positive: intent to practice is not an
# explicit hobby statement. These extra variants were not used to write rules.
VARIANTS = (
    case('C5b', [('user', '我把练吉他当作爱好，和弦怎么按？'),
                 ('assistant', '先练这两个指位。')], [('domain', '兴趣')]),
    case('V1', [('user', '接口为什么返回 409？'),
                ('assistant', '因为来源版本变了。')],
         [('form', '技术讨论'), ('domain', '技术')]),
    case('V2', [('user', '这个 API 为什么返回 409？'),
                ('assistant', '等会儿再聊晚饭。')]),
    case('V3', [('user', '引用别人说“这个 API 为什么返回 409？”'),
                ('assistant', '因为来源版本变了。')]),
    case('V4', [('user', '先说工作报告要改排期。'),
                ('assistant', '先核对交付日期。'),
                ('user', '今晚做咖喱，怎么调味？'),
                ('assistant', '先放少量盐，再尝味道。')], [('domain', '工作'), ('domain', '生活')]),
)
