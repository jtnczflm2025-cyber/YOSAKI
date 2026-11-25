"""Synthetic-only A3 contract cases; never populated from private archives."""
CASES = tuple(
    {'group': group, 'ordinal': i, 'text': f'合成消息 {group} {i}',
     'speaker': 'user' if i % 2 else 'assistant'}
    for group in ('category_ambiguity', 'entity_alias', 'zh_ja_dialogue', 'time_boundary')
    for i in range(8)
)
