#!/bin/sh
# 第 5 幕 · 六维对比雷达图（浏览器打开）
cd /home/okb/okb
echo "打开诊断报告与六维雷达……"
xdg-open score/final-report-r12/index.html &
sleep 2
xdg-open score/final-report-r12/radar-six-dim.svg &
