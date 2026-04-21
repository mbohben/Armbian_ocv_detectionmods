#!/bin/bash

mkdir -p ~/printer_ai/timelapse

ffmpeg -y -framerate 30 \
-pattern_type glob -i '/home/pi/printer_ai/buffer/*.jpg' \
-c:v libx264 -preset veryfast -crf 23 \
/home/pi/printer_ai/timelapse/$(date +%s).mp4
