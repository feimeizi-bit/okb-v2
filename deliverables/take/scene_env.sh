#!/bin/sh
export PATH="$HOME/.local/bin:$PATH"
echo "== 运行环境：openKylin =="
grep PRETTY_NAME /etc/os-release
uname -srm
echo "主机: $(hostname)   用户: $(whoami)"
echo
echo "== 长期记忆评测依赖 =="
echo -n "embed-server : "; systemctl is-active embed-server
echo -n "openclaw     : "; openclaw --version | head -1
echo -n "hermes       : "; hermes --version | head -1
echo
echo "—— 以上为录像当时的真实环境信息 ——"
sleep 600
