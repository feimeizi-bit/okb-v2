"""okb 引擎 · 工作区与 fixture 管理（I-10 / I-14 / CONTRACT §1 §6）

职责：
- 每个 task 一个独立 workspace；`reset()` 恢复到干净基线（I-10）
- 按样本 `fixture[]` 布置初始文件，占位符在此**物化**为真实值（Agent 侧）
- 路径安全：拒绝绝对路径 / `..` / 反斜杠 / NUL / 首尾空白，并用 realpath 容器校验兜底（CONTRACT §6）
- 快照与 diff：跑后 workspace 文件清单（路径 + sha256 + mtime）是规则评分器校验
  `file_content` 锚点的事实基础（B 复核 v0.3 §0 明确要求保留）
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from engine.redact import PiiPipeline, materialize

_DRIVE_RE = re.compile(r"^[A-Za-z]:")
_CHUNK = 1024 * 1024


class PathSafetyError(ValueError):
    """fixture / anchors 路径不安全（CONTRACT §6）。"""


def validate_relpath(rel: str) -> str:
    """校验并归一化一个相对 workspace 根的路径。"""
    if not isinstance(rel, str) or not rel:
        raise PathSafetyError(f"路径为空：{rel!r}")
    if rel != rel.strip():
        raise PathSafetyError(f"路径首尾有空白：{rel!r}")
    if rel.startswith(("/", "\\")) or _DRIVE_RE.match(rel):
        raise PathSafetyError(f"禁止绝对路径：{rel!r}")
    if "\\" in rel:
        raise PathSafetyError(f"禁止反斜杠：{rel!r}")
    if "\x00" in rel:
        raise PathSafetyError(f"路径含 NUL：{rel!r}")
    parts = rel.split("/")
    for part in parts:
        if part in ("", ".", ".."):
            raise PathSafetyError(f"路径含非法段 {part!r}：{rel!r}")
        if part != part.strip():
            raise PathSafetyError(f"路径段首尾有空白：{rel!r}")
    return "/".join(parts)


def resolve_in_root(root: Path, rel: str) -> Path:
    """realpath 容器校验：解析后的路径必须落在 root 内（正则挡不住的都在这挡）。"""
    clean = validate_relpath(rel)
    root_real = Path(os.path.realpath(root))
    target = Path(os.path.realpath(root_real / clean))
    if target != root_real and root_real not in target.parents:
        raise PathSafetyError(f"路径越出 workspace：{rel!r}")
    return target


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            chunk = fh.read(_CHUNK)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


@dataclass(frozen=True)
class FileInfo:
    path: str
    sha256: str
    size: int
    mtime_ns: int

    def as_dict(self) -> dict:
        return {"path": self.path, "sha256": self.sha256, "size": self.size, "mtime_ns": self.mtime_ns}


@dataclass(frozen=True)
class Change:
    op: str  # create | modify | delete
    path: str
    before: FileInfo | None
    after: FileInfo | None


class Workspace:
    def __init__(self, root: str | Path, ignore_top: set[str] | None = None):
        self.root = Path(root)
        self.ignore_top = set(ignore_top or ())

    def _iter_files(self):
        """遍历文件，跳过忽略名单里的顶层项（如 .git / 记忆面）。"""
        if not self.root.exists():
            return
        for path in sorted(self.root.rglob("*")):
            if not path.is_file():
                continue
            rel = path.relative_to(self.root).as_posix()
            if self.ignore_top and rel.split("/", 1)[0] in self.ignore_top:
                continue
            yield rel, path

    # ---- 生命周期 ----

    def reset(self, keep: set[str] | None = None) -> None:
        """task 级清理（I-10）。

        keep=None：整目录清空重建（默认，task 独立工作区）。
        keep=集合：仅清理集合外的顶层条目（Agent 自有工作区必须保留系统文件）。
        """
        if keep is None:
            if self.root.exists():
                shutil.rmtree(self.root)
            self.root.mkdir(parents=True, exist_ok=True)
            return
        self.root.mkdir(parents=True, exist_ok=True)
        for entry in self.root.iterdir():
            if entry.name in keep:
                continue
            if entry.is_dir():
                shutil.rmtree(entry)
            else:
                entry.unlink()

    def cleanup(self, keep: set[str] | None = None) -> None:
        """运行结束清理。

        keep 模式（Agent 自有工作区）**不删除根目录**——它是长期存在的环境；
        任务产物由下一次 reset 清理。默认模式删除整个 task 工作区。
        """
        if keep is not None:
            return
        if self.root.exists():
            shutil.rmtree(self.root, ignore_errors=True)

    # ---- fixture ----

    def materialize(self, fixture: list[dict], pii: PiiPipeline | None = None) -> list[str]:
        """布置 fixture；返回创建的相对路径列表（含创建父目录）。"""
        created: list[str] = []
        for item in fixture or []:
            rel = validate_relpath(str(item.get("path", "")))
            target = resolve_in_root(self.root, rel)
            content = str(item.get("content", ""))
            if pii is not None:
                content = materialize(content, pii.entries)
            target.parent.mkdir(parents=True, exist_ok=True)
            # 显式 LF：禁止平台默认换行把 fixture 字节变成 CRLF（F1 审计；跨 Windows/VM 同策略）
            target.write_text(content, encoding="utf-8", newline="\n")
            created.append(rel)
        return created

    # ---- 快照 / diff ----

    def snapshot(self) -> dict[str, FileInfo]:
        result: dict[str, FileInfo] = {}
        for rel, path in self._iter_files():
            st = path.stat()
            result[rel] = FileInfo(
                path=rel,
                sha256=sha256_file(path),
                size=st.st_size,
                mtime_ns=st.st_mtime_ns,
            )
        return result

    def diff(self, before: dict[str, FileInfo], after: dict[str, FileInfo]) -> list[Change]:
        changes: list[Change] = []
        for rel, info in sorted(after.items()):
            old = before.get(rel)
            if old is None:
                changes.append(Change("create", rel, None, info))
            elif old.sha256 != info.sha256:
                changes.append(Change("modify", rel, old, info))
        for rel, info in sorted(before.items()):
            if rel not in after:
                changes.append(Change("delete", rel, info, None))
        return changes

    def listing(self) -> list[dict]:
        """跑后 workspace 文件清单（B 复核 v0.3 §0 要求保留）。"""
        return [info.as_dict() for info in self.snapshot().values()]

    # ---- 读取 ----

    def read_bytes(self, rel: str) -> bytes | None:
        target = resolve_in_root(self.root, rel)
        if not target.is_file():
            return None
        return target.read_bytes()

    def read_text(self, rel: str, max_bytes: int = 4 * 1024 * 1024) -> str | None:
        """文本读取；二进制或超限返回 None（调用方按截断规则处理）。"""
        raw = self.read_bytes(rel)
        if raw is None or len(raw) > max_bytes:
            return None
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            return None
