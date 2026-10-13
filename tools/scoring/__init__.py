"""okb · B 侧规则评分器包（CONTRACT §5/§7/§13）。

入口：tools/score.py（CLI）。本包只做「读 runs/<id>/ 证据 → 产出 verdicts.jsonl 行」。
不写 evidence、不读样本 expect 之外的答案字段（判官路才用，见 judge.py）。
"""
