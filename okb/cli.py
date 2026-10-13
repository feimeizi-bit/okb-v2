"""okb 命令行入口（thin dispatcher）。

约定：**本包不含业务逻辑**——全部子命令转发到 `tools/` 下既有的 main(argv)，
工具链单点维护；打包（.deb / pip）只需装本仓库 + 一条 entry-point。
子命令 ⇔ 工具映射见 COMMANDS；`okb <cmd> --help` 直达各工具自己的帮助。
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_TOOLS = str(ROOT / "tools")

# 子命令 → (tools 内模块名, 该模块 main 接受的 argv 是否需剥首参)
COMMANDS = {
    "run": "run_benchmark",      # 一键主链路：运行→采集→评分→报告→雷达（默认 v2/scoped-v2，默认离线 prepare）
    "score": "score",            # 规则(+判官 live)评分 → verdicts.jsonl
    "report": "report",          # 成绩单：六维×Agent、敏感性、抽查
    "score-vnext": "score_four_layer",  # 独立四层旁路，默认prepare
    "report-vnext": "report_four_layer",  # v2只读诊断，不产正式排名
    "report-multirun": "report_multirun",  # 冻结计划驱动的跨run诊断
    "capture-identity": "capture_runtime_identity",  # 只读配置；不启动Agent
    "review-judge-cost": "review_judge_cost",  # 离线请求清单/声明报价，不授权调用
    "review-cost-group": "review_cost_group",
    "build-portable": "build_portable",
    "verify-offline": "verify_offline",
    "annotate-semantic": "annotate_semantic",  # 开发集双人三态标注准备，不代标
    "review-semantic-split": "review_semantic_split",  # 家族隔离与冻结候选清单离线审阅
    "validate": "validate",      # 样本 lint（E/W 码套件）
    "c2-run": "c2_run",          # C-1 矩阵/消融（离线模拟器链路）
    "c2-parity": "c2_parity",    # C-2 正式对拍（harness↔模拟器）
    "simulate": "simulate",      # 五档策略离线跑批
    "discriminability": "discriminability",  # 题级区分度与跨轮稳定性（unassessable 不折成 0 分）
    "freeze-status": "freeze_status",
    "anchorcal": "anchorcal",                # 红线4：锚点在真实证据形状上的可执行性校准（只读）
    "freeze-broadcast": "freeze_broadcast",   # 冻结广播件：paper/plan sha + 缺口声明，全部现场读盘   # 冻结就绪度：逐条量 P5 配额与五条红线（数字来自磁盘，不来自记忆）
    "register-run": "register_run",          # 跑批前签发预登记：题目/计划/Agent身份/任务表
    "baseline-attribution": "baseline_attribution",  # 写入归因四象限：omitted 拆成"没写"与"丢了"
    "annotate-checks": "annotate_checks",  # 检查项级人审盲标：判官答案密封到双方交卷后
    "audit-claims": "audit_review_claims",            # 评审文档里的数字与产物重算比对
    "run-suite": "run_suite",                        # 按输出判定的全量回归（不信退出码）  # 检查项级盲标：判官 vs 真人 κ（私有钥匙最后才揭示）
    "annotate": "annotate",      # κ 盲标包 make-worklist / validate
    "kappa": "kappa",            # κ 读数与不一致案例集
    "drift": "judge_drift",      # 判官漂移台账（十列）
}


def _usage(rc: int = 0) -> int:
    print("用法: okb <命令> [参数...]   （参数原样转发给底层工具）")
    for c, m in sorted(COMMANDS.items()):
        print(f"  {c:<12} → tools/{m}.py")
    return rc


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help"):
        return _usage()
    if argv[0] == "--version":
        print("okb cli 0.1 (dispatcher)")
        return 0
    cmd, rest = argv[0], argv[1:]
    if cmd not in COMMANDS:
        print(f"未知命令: {cmd}", file=sys.stderr)
        return _usage(2)
    if _TOOLS not in sys.path:
        sys.path.insert(0, _TOOLS)
    mod = importlib.import_module(COMMANDS[cmd])
    fn = getattr(mod, "main", None)
    if fn is None:
        print(f"内部错误：tools/{COMMANDS[cmd]}.py 无 main(argv)", file=sys.stderr)
        return 3
    sys.argv = [f"okb {cmd}"] + rest   # 帮助头显示 okb <子命令>（argparse prog 取 argv[0]）
    import inspect
    sig = inspect.signature(fn)
    if len(sig.parameters) == 0:
        sys.argv = [f"okb {cmd}"] + rest   # 老式工具：读 sys.argv
        return int(fn() or 0)
    return int(fn(rest) or 0)


if __name__ == "__main__":
    sys.exit(main())
