#!/bin/sh
L=/mnt/take
T() { echo "$(date +%s) $1" >> $L/timeline7.log; }
echo "TAKE7-START $(date)" > $L/timeline7.log
rm -f $L/DONE7

export DISPLAY=:0
xset s off -dpms 2>/dev/null || true

while [ ! -f $L/GO7 ]; do sleep 1; done
sleep 2

rm -f /tmp/scene3b_rid /tmp/scene3b_done

T S3B_START
setsid mate-terminal --title=DemoM --geometry=56x26+0+0 -e "sh -c 'sh /home/okb/demo_driver/scene3b.sh 2>&1 | tee /mnt/take/scene3b-take7.log'" >/dev/null 2>&1 < /dev/null &
sleep 2
wmctrl -a DemoM 2>/dev/null; sleep 1; wmctrl -a DemoM 2>/dev/null

setsid mate-terminal --title=DemoP --geometry=76x26+520+0 -e "sh /mnt/take/scene3prog.sh" >/dev/null 2>&1 < /dev/null &
sleep 2
wmctrl -a DemoP 2>/dev/null; sleep 1; wmctrl -a DemoP 2>/dev/null

i=0
while [ ! -f /tmp/scene3b_done ] && [ $i -lt 360 ]; do sleep 5; i=$((i+1)); done
sleep 12
T S3B_END
wmctrl -c DemoP 2>/dev/null
wmctrl -c DemoM 2>/dev/null
sleep 1

T DONE7
touch $L/DONE7
