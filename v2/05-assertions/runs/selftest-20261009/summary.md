# assertion-bench 报告（自测）

- macro: {"decided": 14, "fp_rate": 0.07142857142857142, "fn_rate": 0.21428571428571427}
- impl 指标: {"impl:A": {"engine": "rule", "applicable": 5, "decided": 5, "fp": 1, "fn": 1, "insuff_truth": 0, "coverage": 1.0, "fp_rate": 0.2, "fn_rate": 0.2, "gate_ok": false}, "impl:B": {"engine": "semantic", "applicable": 5, "decided": 5, "fp": 0, "fn": 0, "insuff_truth": 0, "coverage": 1.0, "fp_rate": 0.0, "fn_rate": 0.0, "gate_ok": true}, "impl:C": {"engine": "rule", "applicable": 4, "decided": 4, "fp": 0, "fn": 2, "insuff_truth": 0, "coverage": 1.0, "fp_rate": 0.0, "fn_rate": 0.5, "gate_ok": false}}

## 晋升建议（不自动生效）
- `s_bad_0:R_Y`: impl:C → （无合格候选） — incumbent fails gates and no qualified candidate
- `s_bad_1:R_Y`: impl:C → （无合格候选） — incumbent fails gates and no qualified candidate
- `s_bad_2:R_Y`: impl:C → （无合格候选） — incumbent fails gates and no qualified candidate
- `s_bad_3:R_Y`: impl:C → （无合格候选） — incumbent fails gates and no qualified candidate
- `s_ok_0:R_X`: impl:A → impl:B — candidate meets gates and outranks incumbent
- `s_ok_1:R_X`: impl:A → impl:B — candidate meets gates and outranks incumbent
- `s_ok_2:R_X`: impl:A → impl:B — candidate meets gates and outranks incumbent
- `s_ok_3:R_X`: impl:A → impl:B — candidate meets gates and outranks incumbent
- `s_ok_4:R_X`: impl:A → impl:B — candidate meets gates and outranks incumbent