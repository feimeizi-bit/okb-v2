# 双口径指标 + 检查族拆分（v2）

- 口径 A=assessed-only；口径 B=unassessable 按失败（下界）；检查族=按 check id 归类。
- 绑定：plan `7643b43a83aa…` · run `v2r3_20261010`

## 总体

| Agent | 计划 | 通过 | 失败 | 不可判 | 口径A | 口径B | 覆盖率 | 第一失败族构成 |
|---|---|---|---|---|---|---|---|---|
| hermes | 276 | 194 | 43 | 39 | 0.8186 | 0.7029 | 0.8587 | {"memory-store": 13, "artifact": 6, "reply": 4, "other": 20} |
| openclaw | 276 | 132 | 116 | 28 | 0.5323 | 0.4783 | 0.8986 | {"memory-store": 36, "artifact": 26, "reply": 6, "task-application": 30, "other": 18} |

## 分维度（口径A / 口径B / 覆盖率 / 第一失败族）

| 维度 | Agent | A | B | 覆盖 | 失败族 |
|---|---|---|---|---|---|
| 长期保持 | hermes | 1.0 | 0.8667 | 0.8667 | {} |
| 长期保持 | openclaw | 0.4737 | 0.4 | 0.8444 | {"memory-store": 1, "artifact": 2, "task-application": 17} |
| 记忆调用 | hermes | 0.9024 | 0.8222 | 0.9111 | {"memory-store": 1, "artifact": 2, "other": 1} |
| 记忆调用 | openclaw | 0.6889 | 0.6889 | 1.0 | {"memory-store": 10, "task-application": 4} |
| 动态更新 | hermes | 0.675 | 0.6 | 0.8889 | {"memory-store": 8, "artifact": 1, "other": 4} |
| 动态更新 | openclaw | 0.3659 | 0.3333 | 0.9111 | {"memory-store": 18, "artifact": 6, "other": 2} |
| 相近区分 | hermes | 0.9318 | 0.9111 | 0.9778 | {"artifact": 1, "other": 2} |
| 相近区分 | openclaw | 0.8095 | 0.7556 | 0.9333 | {"artifact": 3, "task-application": 1, "other": 4} |
| 边界识别 | hermes | 0.8 | 0.7111 | 0.8889 | {"reply": 4, "other": 4} |
| 边界识别 | openclaw | 0.5111 | 0.5111 | 1.0 | {"reply": 6, "task-application": 8, "other": 8} |
| 任务复用 | hermes | 0.5455 | 0.3529 | 0.6471 | {"memory-store": 4, "artifact": 2, "other": 9} |
| 任务复用 | openclaw | 0.2973 | 0.2157 | 0.7255 | {"memory-store": 7, "artifact": 15, "other": 4} |
