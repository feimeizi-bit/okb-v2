# schemas — 数据契约

> 状态：`sample.schema.json` **v0.2**（已通过示例校验：0 错误）
> 权威性：字段与枚举以 `../docs/CONTRACT.md` 为唯一权威

## 文件

| 文件 | 内容 | 负责人 | 状态 |
|---|---|---|---|
| `sample.schema.json` | 单条样本结构（JSON Schema draft-07） | B | **v0.2** |
| `example-samples.yaml` | 两条示例样本（动态更新 / 边界识别） | B | v0.2 同步 |
| `CHANGELOG.md` | 变更记录（只允许新增字段） | B | 持续 |
| `evidence.schema.json` | 证据流水字段基线（对齐接口单 I-1） | A | 待编写 |

## 写作与校验链路

```
人类写 YAML（schemas/*.yaml 或 samples/）
   → tools/build_paper.py   转成 paper.jsonl（每行一个 JSON 对象）
   → tools/validate.py      用 sample.schema.json 校验 + loader 级跨字段检查
   → 交给引擎执行
```

⚠️ `tools/` 目录尚不存在，是下一步要补的（当前校验只能手工跑 `jsonschema`）。

## 两层校验（缺一不可）

1. **schema 层**（`sample.schema.json`）：字段名、类型、枚举、模式
2. **loader 层**（schema 表达不了的跨字段约束，见 `docs/CONTRACT.md` §5）：
   会话唯一且连续 / phase 非递减 / 锚点可解析 / 至少一条 hard+present /
   `rule_only ⇒ 全 hard` / pii id 唯一且占位符全部声明 / 路径容器校验 / 等 10 项

**实测教训**：11 类语义坏样本（同一 session 出现两次、`phase` 顺序颠倒、`key:"*"` 未配 `value_regex`、`rule_only + soft`、`anchors.turn` 指向不存在的轮次……）**全部能通过纯 schema 校验**。所以 loader 层不是可选项。

## 冻结规则

- 字段与枚举**只允许新增，不允许改名或删除**；变更登记到 `CHANGELOG.md` 并通知双方
- 生产数据的 `normalize` 版本号随 `CONTRACT.md` 冻结，供离线再评分复现
- 判官缓存键改用稳定投影，避免新增字段导致历史缓存全部失效
