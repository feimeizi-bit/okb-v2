"""okb 引擎 · 声明式 CLI 适配器（第三个及以后智能体的通用入口，ISS-7 抽象动作映射）。

为什么不是 `kylinbot.py`：任务书点名 KylinBot / kylin-agent，但本仓对这两个 CLI 的 argv、
输出格式、记忆导出方式**零取证**（`docs/SETUP_VM_AGENTS.md` 只装了 OpenClaw/Hermes，
A 侧盘点里 Agent 版本均为 null）。把命令猜出来写死，只会让 VM 上跑出一堆 env_error，
并把「已支持」变成虚报。所以这里把「怎么调用一个智能体 CLI」外置成配置，
命令与抽取路径都由 `deploy/agents.yaml` 的 config 段声明；取证到什么就声明什么，
声明不了的动作留空并在 capabilities 里如实降级。

安全与诚实口径：
- 只用 argv 数组 + stdin 传对话文本，**绝不调用 shell**；对话文本永不进 argv（防注入、防超长）。
- 模板里的占位符白名单校验，未知占位符在任何子进程启动前就报错（不静默把 `{typo}` 传给 CLI）。
- `model` / `usage` / `session_id` 抽不到就是 `None`：不写 0、不写空 dict 冒充观测值。
- `manifest_extra` 只写 `safe_config()` 脱敏后的模板，凭据不入库。

用法（agents.yaml）：
    agents:
      - name: kylinbot
        adapter: cli
        config:
          cli: kylinbot
          send_argv: [kylinbot, chat, --session, "{session_key}", --json]
          send_stdin: true
          reply_from: responses[0].output.text      # engine.paths.resolve 语法
          model_from: model
          usage_from: usage
          reset_argv: [[kylinbot, memory, clear, --yes]]
          memory_argv: [kylinbot, memory, export, --json]
          memory_entries_from: entries
          capabilities: {dialogue: true, memory: true, file: false, tool: false}
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

from engine import paths as P
from engine.adapter import (AgentAdapter, AdapterError, AdapterTimeout, Capabilities,
                            MemoryEntry, MemorySnapshot, TaskContext, TurnResult, safe_config)
from engine.collectors import now_iso

ROOT = Path(__file__).resolve().parents[2]

PLACEHOLDERS = ("session_key", "run_id", "task_id", "workspace", "state_dir", "session",
                "turn", "phase")
# {state_dir} = 任务私有、**在被评工作区之外**的智能体内部状态目录。
# 依据（实测）：替身把 state.json/state.calls 写在 {workspace} 里，runner 把它们采成 file_write
# 之后它们还在变 ⇒ 末态清单与已采内容不一致 ⇒ scoped_evidence 如实报 listing fingerprint mismatch
# 并拒绝给这些任务判分。任何自带会话库/日志的 CLI 都会踩同一个坑，所以给出显式的外置目录。
# 命令模板里出现这些词根的 flag，其下一个参数按凭据处理：manifest 只记 flag 名。
SECRET_FLAGS = ("key", "token", "secret", "password", "auth", "bearer", "credential", "api")


def redact_argv(argv):
    out = []
    pending = False
    for item in argv:
        text = str(item)
        if pending:
            out.append("<REDACTED>")
            pending = False
            continue
        low = text.lower()
        if "=" in text and any(flag in low.split("=", 1)[0] for flag in SECRET_FLAGS):
            out.append(text.split("=", 1)[0] + "=<REDACTED>")
            continue
        out.append(text)
        if low.startswith("-") and any(flag in low for flag in SECRET_FLAGS):
            pending = True
    return out


def redact_config(config):
    # 模板级脱敏：safe_config 只按键名掩码，argv 列表里的 --token SECRET 会原样外泄。
    out = safe_config(config)
    for field in ("send_argv", "reset_argv", "memory_argv", "send_first_turn_argv_extra"):
        value = out.get(field)
        if not isinstance(value, list):
            continue
        if value and all(isinstance(x, str) for x in value):
            out[field] = redact_argv(value)          # 单条 argv：flag 与紧随其后的值成对处理
        else:
            out[field] = [redact_argv(row) if isinstance(row, list) else redact_argv([row])
                          for row in value]         # reset_argv 是多条命令的列表
    return out


def _substitute(value, mapping: dict):
    """递归替换模板里的 {placeholder}；未知占位符直接报错（不静默传给 CLI）。"""
    if isinstance(value, list):
        return [_substitute(v, mapping) for v in value]
    if not isinstance(value, str):
        return value
    out = value
    for key in PLACEHOLDERS:
        if "{" + key + "}" in out:
            out = out.replace("{" + key + "}", str(mapping[key]))
    if "{" in out or "}" in out:
        import re
        unknown = sorted(set(re.findall(r"\{([^{}]+)\}", out)))
        if unknown:
            raise AdapterError(f"unknown template placeholder(s): {unknown}; allowed: {list(PLACEHOLDERS)}")
    return out


def _extract(data, dotted: str | None):
    """按仓内共享的字段路径实现取值（与证据 payload 路径同一份语义）。取不到返回 None。"""
    if not dotted:
        return None
    try:
        return P.resolve(data, dotted)
    except P.PathError:
        return None


class CliTemplateAdapter(AgentAdapter):
    """配置驱动的 CLI 适配器：命令模板 + 字段路径，无 shell、无猜测。"""

    name = "cli"
    agent_kind = "cli-template"
    # model_name/temperature 由配置声明（不是探测值）：multirun 的登记门会拿 manifest 里声明的
    # model 与逐任务**观测到的** model 比对（实测 'observed model mismatch'），所以接第三方
    # 智能体时必须在 agents.yaml 里声明它上报的模型名，声明不了就别登记进对比计划。
    model_name = None
    temperature = None

    def __init__(self, config: dict | None = None):
        super().__init__(config)
        cfg = self.config
        raw_cli = cfg.get("cli", "")
        # cli 可以是单个可执行名，也可以是解释器前缀列表（如 [python3, agent.py]）：
        # 某些 CLI 本身就是脚本，硬要求字符串会逼出 str(list) 这种废 argv。
        def _tok(x, first=False):
            # {python} = 当前解释器：让同一份 agents.yaml 在 openKylin（python3）与
            # Windows 开发机（python）都能跑，而不是把某个平台的可执行名焊死在配置里。
            text = os.path.expanduser(str(x))
            if text == "{python}":
                return sys.executable
            if first:
                return text
            # 脚本类参数按**仓库根**解析：子进程的 cwd 是任务工作区，相对路径在那里不存在
            # （实测 agent_error: can't open file .../work/<task>/engine/tests/fake_cli_agent.py）。
            candidate = Path(text)
            if not candidate.is_absolute() and (ROOT / text).is_file():
                return str((ROOT / text).resolve())
            return text
        if isinstance(raw_cli, str):
            self.cli_prefix = [_tok(raw_cli, first=True)] if raw_cli.strip() else []
        elif isinstance(raw_cli, list) and raw_cli and all(str(x).strip() for x in raw_cli):
            self.cli_prefix = [_tok(x, first=i == 0) for i, x in enumerate(raw_cli)]
        else:
            self.cli_prefix = []
        if not self.cli_prefix:
            raise AdapterError("cli adapter needs config['cli'] (executable path, name, or argv prefix list)")
        self.cli = self.cli_prefix[0]
        self.send_argv = cfg.get("send_argv")
        if not isinstance(self.send_argv, list) or not self.send_argv:
            raise AdapterError("cli adapter needs a non-empty config['send_argv'] argv list")
        self.send_stdin = bool(cfg.get("send_stdin", True))
        # 关闭 stdin 时对话文本会进 argv：同机任何用户都能从 `ps` 读到，且过长会 E2BIG。
        # 传输方式必须在构造期定下来并留在 config 里，让 manifest/登记的指纹能区分两种模板。
        self.argv_text_max_bytes = int(cfg.get("argv_text_max_bytes", 4096))
        cfg["text_transport"] = "stdin" if self.send_stdin else "argv"
        self.send_first_extra = cfg.get("send_first_turn_argv_extra") or []
        self.reply_from = cfg.get("reply_from")
        self.reply_raw = bool(cfg.get("reply_raw", False))
        self.session_id_from = cfg.get("session_id_from")
        self.model_from = cfg.get("model_from")
        self.usage_from = cfg.get("usage_from")
        self.reset_argv = cfg.get("reset_argv") or []
        self.memory_argv = cfg.get("memory_argv") or []
        self.memory_format = str(cfg.get("memory_format", "json"))
        self.memory_entries_from = cfg.get("memory_entries_from")
        self.memory_key_field = str(cfg.get("memory_key_field", "key"))
        self.memory_value_field = str(cfg.get("memory_value_field", "value"))
        self.memory_time_field = cfg.get("memory_time_field")
        self.memory_dir = os.path.expanduser(str(cfg.get("memory_dir", ""))) or None
        self.memory_files = list(cfg.get("memory_files") or [])
        caps = cfg.get("capabilities") or {}
        self._caps = caps
        if cfg.get("model_name") is not None:
            self.model_name = str(cfg["model_name"])
        if cfg.get("temperature") is not None:
            self.temperature = float(cfg["temperature"])
        self.turn_timeout = float(cfg.get("turn_timeout_seconds", 180))
        self.probe_timeout = float(cfg.get("probe_timeout_seconds", 120))
        self.supports_time_anchor = bool(caps.get("supports_time_anchor", False))
        # probe_memory(phase) 的基类签名不带 TaskContext（openclaw/hermes 的记忆面在固定
        # home 目录下），但声明式模板常用 {workspace}。这里记住最近一次任务工作目录；
        # 没记住就明确报错——绝不静默回落到进程 cwd（那会读到别的任务甚至空记忆面）。
        self._workspace: Path | None = None
        self._task_id: str = ""
        if self.memory_format not in ("json", "files", "markdown"):
            raise AdapterError(f"unsupported memory_format {self.memory_format!r}")
        if self.memory_format in ("files", "markdown") and not (self.memory_dir and self.memory_files):
            raise AdapterError("file-based memory needs config['memory_dir'] and ['memory_files']")
        if self.memory_format in ("json",) and not self.memory_argv:
            raise AdapterError("json memory needs config['memory_argv']")

    # ---- 能力与身份 ----

    def capabilities(self) -> Capabilities:
        c = self._caps
        return Capabilities(
            dialogue=bool(c.get("dialogue", True)),
            memory=bool(c.get("memory", False)),
            file=bool(c.get("file", False)),
            tool=bool(c.get("tool", False)),
            notes=str(c.get("notes", "declarative CLI adapter; capabilities declared, not probed")),
            supports_time_anchor=self.supports_time_anchor,
        )

    def cli_path(self) -> str | None:
        # 基类 health() 对返回做 which()/exists()，所以只能给**单个可执行文件**；
        # 把 "python3 script.py" 整串返回会让预检必然失败（实测 env_error：预检失败）。
        return self.cli_prefix[0] if self.cli_prefix else None

    def _resolve_script(self, path: str) -> Path | None:
        for cand in (Path(os.path.expanduser(path)), ROOT / path):
            if cand.is_file():
                return cand
        return None

    def health(self) -> dict:
        """解释器前缀形态（cli: [python3, agent.py]）时，脚本存在性必须单独检查。"""
        report = super().health()
        if len(self.cli_prefix) > 1:
            script = self.cli_prefix[1]
            found = self._resolve_script(script)
            report["checks"].append({"name": "cli_script", "ok": found is not None,
                                     "detail": str(found) if found else script})
            report["ok"] = all(c["ok"] for c in report["checks"])
        return report

    def manifest_extra(self) -> dict:
        """只暴露脱敏后的模板：让「这次到底怎么调用智能体的」可复核，又不带凭据。"""
        return {"cli_template": redact_config(self.config)}

    def _state_dir(self, workspace: Path, task_id: str) -> Path:
        path = workspace.parent / "_agent_state" / task_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _mapping(self, ctx: TaskContext, session: str = "", turn: int = 0,
                 session_key: str = "", phase: str = "") -> dict:
        workspace = str(Path(ctx.workspace).resolve())
        self._workspace = Path(workspace)
        self._task_id = ctx.task_id
        return {"session_key": session_key, "run_id": ctx.run_id, "task_id": ctx.task_id,
                "workspace": workspace, "session": session, "turn": turn, "phase": phase,
                "state_dir": str(self._state_dir(Path(workspace), ctx.task_id))}

    def _exec(self, argv: list, *, cwd: str, stdin: str | None, timeout: float, label: str):
        start = time.monotonic()
        try:
            proc = subprocess.run([str(x) for x in argv], cwd=cwd, input=stdin,
                                  capture_output=True, text=True, encoding="utf-8",
                                  errors="replace", timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            raise AdapterTimeout(f"{label} 超时（{timeout}s）") from exc
        except FileNotFoundError as exc:
            raise AdapterError(f"{label} 无法启动：{argv[:2]}（{exc}）") from exc
        return proc, int((time.monotonic() - start) * 1000)

    # ---- 生命周期 ----

    def reset(self, ctx: TaskContext) -> list[dict]:
        actions: list[dict] = []
        mapping = self._mapping(ctx)
        for template in self.reset_argv:
            body = template if isinstance(template, list) else [template]
            argv = [*self.cli_prefix, *_substitute(body, mapping)]
            proc, ms = self._exec(argv, cwd=mapping["workspace"], stdin=None,
                                  timeout=self.probe_timeout, label="reset")
            actions.append({"cmd": " ".join(argv), "returncode": proc.returncode,
                            "ok": proc.returncode == 0, "duration_ms": ms,
                            "stdout": (proc.stdout or "")[:2000], "stderr": (proc.stderr or "")[:2000]})
            if proc.returncode != 0:
                raise AdapterError(f"reset 退出码 {proc.returncode}：{(proc.stderr or proc.stdout)[:400]}")
        if not self.reset_argv:
            actions.append({"cmd": "<no reset_argv declared>", "returncode": 0, "ok": False,
                            "duration_ms": 0,
                            "note": "该智能体未声明清理命令：基线未重置，记忆面可能承接上一任务"})
        return actions

    # ---- 单轮调用 ----

    def send(self, ctx: TaskContext, session: str, turn_index: int, text: str,
             session_first_turn: bool, timeout: float | None = None) -> TurnResult:
        session_key = self.session_key(ctx.run_id, ctx.task_id, session)
        mapping = self._mapping(ctx, session=session, turn=turn_index, session_key=session_key)
        argv = [*self.cli_prefix, *_substitute(self.send_argv, mapping)]
        if session_first_turn and self.send_first_extra:
            argv += [str(_substitute(x, mapping)) for x in self.send_first_extra]
        stdin = text if self.send_stdin else None
        if not self.send_stdin:
            nbytes = len(text.encode("utf-8"))
            if nbytes > self.argv_text_max_bytes:
                raise AdapterError(
                    "send_stdin=false 时对话文本走 argv，本次 %d 字节超过上限 %d（argv 会长得触发 "
                    "E2BIG，并且同机用户能从 ps 读到题目原文）。请改用 send_stdin: true；"
                    "确有需要再调 argv_text_max_bytes。" % (nbytes, self.argv_text_max_bytes))
            argv.append(text)          # 只有显式关闭 stdin 时才把文本放进 argv
        cwd = str(_substitute(self.config.get("cwd", "{workspace}"), mapping))
        proc, ms = self._exec(argv, cwd=cwd, stdin=stdin,
                              timeout=timeout or self.turn_timeout, label="send")
        if proc.returncode != 0:
            raise AdapterError(f"cli send 退出码 {proc.returncode}：{(proc.stderr or proc.stdout or '')[:400]}")
        stdout = proc.stdout or ""
        if self.reply_raw:
            reply, envelope = stdout, None
        else:
            try:
                envelope = json.loads(stdout)
            except ValueError as exc:
                raise AdapterError(f"cli send 输出不是 JSON（声明了 reply_from）：{stdout[:200]}") from exc
            reply = _extract(envelope, self.reply_from)
            if reply is None:
                raise AdapterError(f"reply_from {self.reply_from!r} 在该输出里取不到值："
                                   f"{stdout[:200]}")
        return TurnResult(
            reply_text=str(reply),
            stdout=stdout,
            stderr=proc.stderr or "",
            exit_code=proc.returncode,
            duration_ms=ms,
            session_id=(str(_extract(envelope, self.session_id_from))
                        if envelope is not None and _extract(envelope, self.session_id_from) is not None
                        else session_key),
            model=(_extract(envelope, self.model_from) if envelope is not None else None),
            # 抽不到就是 None：显式不冒充观测值（Hermes 的 usage 缺口是同一条规矩）
            usage=(_extract(envelope, self.usage_from) if envelope is not None else None),
        )

    # ---- 记忆快照 ----

    def probe_memory(self, phase: str, *, for_task_id: str | None = None) -> MemorySnapshot:
        # 跨任务串证据守卫：记忆面按任务目录存放（{workspace}/{state_dir}），而探针签名不带
        # TaskContext，所以只能靠"绑定时记下的 task_id"与"调用方声明的 for_task_id"对撞。
        # 不匹配就说明探针落在了别的任务的记忆面上——那种证据会被当成有效记忆静默参与评分。
        if for_task_id and self._task_id and for_task_id != self._task_id:
            raise AdapterError(
                "probe_memory(%s) 声明属于任务 %r，但适配器当前绑定的是 %r：拒绝读取别的任务的记忆面"
                % (phase, for_task_id, self._task_id))
        if for_task_id and self._task_id:
            pass                                  # 绑定一致，继续
        elif for_task_id and not self._task_id:
            raise AdapterError("probe_memory(%s) 在 reset(ctx) 之前被调用（任务 %r 尚未绑定工作区）"
                               % (phase, for_task_id))
        if self.memory_format in ("files", "markdown"):
            return self._probe_files(phase)
        if self._workspace is None:
            raise AdapterError("memory template needs a task workspace: run reset(ctx) first, "
                               "or declare memory_dir/memory_files for a file-based memory face")
        workspace = str(self._workspace)
        task_id = self._task_id or self._workspace.name
        # 与 _mapping() 保持同一组占位符：漏一个键就会让 memory_argv 里的 {state_dir}
        # 抛 KeyError（实测 3 个连续 env_error 触发熔断停批）。
        mapping = {"session_key": "", "run_id": "-", "task_id": task_id, "workspace": workspace,
                   "state_dir": str(self._state_dir(self._workspace, task_id)),
                   "session": "", "turn": 0, "phase": phase}
        argv = [*self.cli_prefix, *_substitute(self.memory_argv, mapping)]
        proc, _ms = self._exec(argv, cwd=workspace, stdin=None,
                               timeout=self.probe_timeout, label="memory export")
        started, completed = now_iso(), now_iso()
        if proc.returncode != 0:
            return MemorySnapshot(phase=phase, taken_at=completed, entries=[],
                                  coverage={"files_scanned": 0, "entries_scanned": 0,
                                            "bytes_scanned": 0, "sources": [],
                                            "error": f"exit {proc.returncode}"}, observation=None)
        try:
            data = json.loads(proc.stdout or "")
        except ValueError as exc:
            raise AdapterError(f"memory export 不是 JSON：{(proc.stdout or '')[:200]}") from exc
        rows = _extract(data, self.memory_entries_from)
        if rows is None:
            rows = []
        if not isinstance(rows, list):
            raise AdapterError("memory_entries_from 必须指向一个数组")
        entries = []
        for i, row in enumerate(rows):
            if not isinstance(row, dict):
                raise AdapterError(f"memory entry {i} is not an object")
            key = row.get(self.memory_key_field)
            value = row.get(self.memory_value_field)
            if not isinstance(key, str) or not isinstance(value, str):
                raise AdapterError(f"memory entry {i} lacks string {self.memory_key_field!r}/"
                                   f"{self.memory_value_field!r}")
            stamp = _extract(row, self.memory_time_field) if self.memory_time_field else None
            entries.append(MemoryEntry(key=key, value=value,
                                       written_at=str(stamp) if stamp else None,
                                       written_at_source="cli_export_field" if stamp else None,
                                       source="cli_export"))
        raw = (proc.stdout or "").encode("utf-8")
        return MemorySnapshot(phase=phase, taken_at=completed, entries=entries,
                              coverage={"files_scanned": 0, "entries_scanned": len(entries),
                                        "bytes_scanned": len(raw), "sources": ["cli_export"]},
                              observation=None)

    def _probe_files(self, phase: str) -> MemorySnapshot:
        from engine.memory_capture import capture_files
        from engine.observation import file_scope
        started = now_iso()
        capture = capture_files(Path(self.memory_dir), self.memory_files)
        entries, sources, total = [], [], 0
        for doc in capture["documents"]:
            text = doc["content"].replace("\r\n", "\n").replace("\r", "\n")
            total += len(text.encode("utf-8"))
            sources.append(doc["path"])
            stamp = time.strftime("%Y-%m-%dT%H:%M:%S%z",
                                  time.localtime(doc["source_mtime_ns"] / 1e9))
            entries.extend(MemoryEntry(key=f"{doc['path']}#{i}", value=line, written_at=stamp,
                                       written_at_source="file_mtime", source=doc["path"])
                           for i, line in enumerate(l for l in text.splitlines() if l.strip()))
        completed = now_iso()
        scope = file_scope(Path(self.memory_dir), self.memory_files, "cli-template",
                           started, completed, full_capture=True)
        return MemorySnapshot(phase=phase, taken_at=completed, entries=entries,
                              coverage={"files_scanned": len(capture["documents"]),
                                        "entries_scanned": len(entries), "bytes_scanned": total,
                                        "sources": sources}, observation=scope,
                              full_capture=capture)


    def file_root(self, ctx: TaskContext) -> Path:
        return Path(ctx.workspace).resolve()


def make(config: dict | None = None) -> CliTemplateAdapter:
    return CliTemplateAdapter(config)
