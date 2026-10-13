#!/usr/bin/env bash
# okb .deb 构建（交付 d）——dpkg-deb 直构：无 debhelper、无网络、幂等
#
# 用法：bash deploy/build-deb.sh [版本]     产物：dist/okb_<版本>_all.deb
#   · 版本取值来源：命令行第 1 参数 > pyproject.toml [project].version（tomllib 读取）
#   · 安装：sudo dpkg -i dist/okb_0.1.0_all.deb（依赖 python3 / python3-yaml / python3-jsonschema）
#   · 构成：/opt/okb 全仓数据面（okb tools engine samples schemas deploy + pyproject/README）
#           /usr/bin/okb shim = cd /opt/okb && python3 -m okb "$@"
#   · deploy/ 必须入包：交付 d 要求「支持导入不同智能体配置进行批量对比评测」，
#     智能体清单 deploy/agents.yaml 与 VM 执行单 deploy/RUN_ON_OPENKYLIN.md 只有随包安装才可用
set -euo pipefail
cd "$(dirname "$0")/.."

VERSION="${1:-}"
if [ -z "$VERSION" ]; then
    VERSION="$(python3 - <<'PY'
import tomllib
print(tomllib.load(open("pyproject.toml", "rb"))["project"]["version"])
PY
)"
fi

stage="$(mktemp -d)"
trap 'rm -rf "$stage"' EXIT
# mktemp -d 默认 0700：不纠正的话包根条目是 drwx------，安装态普通用户读不到 /opt/okb
chmod 0755 "$stage"

mkdir -p "$stage/DEBIAN" "$stage/opt/okb" "$stage/usr/bin"
for d in okb tools engine samples schemas deploy; do
    cp -r "$d" "$stage/opt/okb/"
done
cp pyproject.toml README.md "$stage/opt/okb/"
# .pyc 属构建机产物，不入包
find "$stage/opt/okb" -name '__pycache__' -type d -prune -exec rm -rf {} +
# Windows/drvfs 检出会把所有文件带成 0755；安装态统一归位（目录 755 / 数据 644 / 脚本 755）
find "$stage/opt/okb" -type d -exec chmod 0755 {} +
find "$stage/opt/okb" -type f -exec chmod 0644 {} +
find "$stage/opt/okb" -type f \( -name '*.sh' -o -name '*.py' \) -exec chmod 0755 {} +

# control/postinst 在 Windows 工作副本里可能带 CRLF：`#!/bin/sh\\r` 会让 dpkg 在
# 安装后配置阶段报「unable to execute ... post-installation script: No such file or
# directory」（内核按 shebang 找 /bin/sh\\r 这个解释器）。入包前一律去 CR。
# 打包确定性：dpkg-deb 会把文件 mtime 写进 tar，默认两次构建产物 sha 不同。
# 统一压到固定 epoch（可用 SOURCE_DATE_EPOCH 覆盖），这样 .deb 本身也可复算比对。
EPOCH="${SOURCE_DATE_EPOCH:-1767225600}"   # 2026-01-01T00:00:00Z
find "$stage" -exec touch -d "@${EPOCH}" {} + 2>/dev/null

sed "s/@@VERSION@@/${VERSION}/" deploy/deb/control | tr -d '\r' > "$stage/DEBIAN/control"
tr -d '\r' < deploy/deb/postinst > "$stage/DEBIAN/postinst"
chmod 0755 "$stage/DEBIAN/postinst"

cat > "$stage/usr/bin/okb" <<'SHIM'
#!/bin/sh
# okb CLI shim：benchmark 工具链以 /opt/okb 为工作目录（samples/ schemas/ deploy/ 相对可达）
cd /opt/okb && exec python3 -m okb "$@"
SHIM
chmod 0755 "$stage/usr/bin/okb"

# 上一步之后又写了 control/postinst/shim，再压一次时间戳保证完整确定
find "$stage" -exec touch -d "@${EPOCH}" {} + 2>/dev/null

mkdir -p dist
deb="dist/okb_${VERSION}_all.deb"
rm -f "$deb"          # 幂等重建：dpkg-deb 不覆盖既有产物会直接失败
dpkg-deb --build --root-owner-group "$stage" "$deb" >/dev/null
# 成员清单旁证：.deb 用 zstd 压缩（Ubuntu 24.04 起 dpkg 默认），Python 侧不一定能解，
# 所以在"有 dpkg-deb 的构建现场"就把成员列表落成旁证，供文档事实生成器读取。
dpkg-deb -c "$deb" | awk '{print $6}' | grep -v '/$' | sort > "${deb%.deb}.members.txt"
# 内容指纹：对"包内每个文件的路径+内容哈希"求摘要。容器 sha256 绑定构建实例（mtime/gzip 头），
# 换一台机器重建就对不上，无法当作文档引用值；内容指纹在同样的树下逐字节相同，才是可跨机核对的量。
( x=$(mktemp -d); dpkg-deb -x "$deb" "$x"
  ( cd "$x" && find . -type f | sort | while read -r f; do
      printf '%s:%s\n' "${f#./}" "$(sha256sum "$f" | cut -d' ' -f1)"
    done ) | sha256sum | cut -d' ' -f1 > "${deb%.deb}.content.sha256"
  rm -rf "$x" )
echo "   content: $(cat "${deb%.deb}.content.sha256")（跨机可比）"
echo "   members: $(wc -l <"${deb%.deb}.members.txt") 个文件 → ${deb%.deb}.members.txt"
echo "✅ 构建完成：$deb"
echo "   size: $(wc -c <"$deb") bytes   sha256: $(sha256sum "$deb" | cut -d' ' -f1)"
dpkg-deb -I "$deb" | sed -n '1,14p'
