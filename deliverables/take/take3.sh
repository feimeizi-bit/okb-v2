#!/bin/sh
# 补拍第 3 幕专用（不重录其他幕）
L=/mnt/take
T() { echo "$(date +%s) $1" >> $L/timeline3.log; }
echo "TAKE3-START $(date)" > $L/timeline3.log
rm -f $L/DONE3

export DISPLAY=:0
xset s off -dpms 2>/dev/null || true

# 等待宿主开录信号
while [ ! -f $L/GO3 ]; do sleep 1; done
sleep 2

# 清掉上一轮 out-root（否则 okb run 真实模式拒绝混入）
rm -rf /home/okb/okb/build/demo_live
rm -f /tmp/scene3_done

T SCENE3ONLY_START
setsid mate-terminal --title=DemoM --geometry=130x28+0+0 -e "sh -c 'sh /home/okb/demo_driver/scene3.sh 2>&1 | tee /mnt/take/scene3-take3.log'" >/dev/null 2>&1 < /dev/null &
sleep 2
wmctrl -a DemoM 2>/dev/null; sleep 1; wmctrl -a DemoM 2>/dev/null

i=0
while [ ! -f /tmp/scene3_done ] && [ $i -lt 360 ]; do sleep 5; i=$((i+1)); done
sleep 14
T SCENE3ONLY_END
wmctrl -c DemoM 2>/dev/null; sleep 1

T DONE3
touch $L/DONE3
