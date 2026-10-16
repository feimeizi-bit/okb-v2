#!/bin/sh
# 总导演（在 VM 内本地运行，从共享文件夹启动；不需要 SSH）
# 时间线写入 /mnt/take/timeline.log；结束时 touch /mnt/take/DONE
L=/mnt/take
mkdir -p $L 2>/dev/null
T() { echo "$(date +%s) $1" >> $L/timeline.log; }
echo "TAKE-START $(date)" > $L/timeline.log
rm -f $L/DONE

export DISPLAY=:0
xset s off -dpms 2>/dev/null || true

# 把自己（控制台终端）最小化，避免出镜
self=$(xdotool search --name "okb@okb-pc" 2>/dev/null | head -1)
[ -n "$self" ] && xdotool windowminimize "$self" 2>/dev/null
sleep 1

spawn() {
  title=$1; geom=$2; script=$3
  setsid mate-terminal --title=$title --geometry=$geom -e "sh /home/okb/demo_driver/$script" >/dev/null 2>&1 < /dev/null &
  sleep 2
  wmctrl -a $title 2>/dev/null
  sleep 1
  wmctrl -a $title 2>/dev/null
}
closew() { wmctrl -c "$1" 2>/dev/null; sleep 1; }

for t in DemoE RehM RehM6 RehA RehB DemoM DemoA DemoB DemoMain DemoTest; do wmctrl -c $t 2>/dev/null; done
sleep 1

# GO 等待开录信号
while [ ! -f $L/GO ]; do sleep 1; done
sleep 2

T SCENE_ENV_START
setsid mate-terminal --title=DemoE --geometry=104x26+120+70 -e "sh /mnt/take/scene_env.sh" >/dev/null 2>&1 < /dev/null &
sleep 2
wmctrl -a DemoE 2>/dev/null; sleep 1; wmctrl -a DemoE 2>/dev/null
sleep 20
T SCENE_ENV_END
closew DemoE; sleep 1

T SCENE0_START
spawn DemoA 60x26+0+0 scene0a.sh
spawn DemoB 60x26+640+0 scene0b.sh
sleep 33
T SCENE0_END
closew DemoA; closew DemoB; sleep 1

T SCENE1_START
spawn DemoM 130x28+0+0 scene1.sh
sleep 24
T SCENE1_END
closew DemoM; sleep 1

T SCENE2_START
spawn DemoM 130x28+0+0 scene2.sh
sleep 24
T SCENE2_END
closew DemoM; sleep 1

T SCENE3_START
rm -f /tmp/scene3_done
spawn DemoM 130x28+0+0 scene3.sh
i=0
while [ ! -f /tmp/scene3_done ] && [ $i -lt 360 ]; do sleep 5; i=$((i+1)); done
sleep 14
T SCENE3_END
closew DemoM; sleep 1

T SCENE35_START
spawn DemoM 130x28+0+0 scene35.sh
sleep 16
T SCENE35_END
closew DemoM; sleep 1

T SCENE4_START
spawn DemoM 130x28+0+0 scene4.sh
sleep 24
T SCENE4_END
closew DemoM; sleep 1

T SCENE5_SKIPPED


T SCENE6_START
spawn DemoM 130x28+0+0 scene6.sh
sleep 20
T SCENE6_END
closew DemoM

T DONE
touch $L/DONE
