#!/bin/bash
cd /home/pi/printer_ai
source venv/bin/activate
nice -n 10 python main.py >> logs/main.log 2>&1
