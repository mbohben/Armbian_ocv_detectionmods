#!/bin/bash

# Buffer: keep 2 days
find ~/printer_ai/buffer -type f -mtime +2 -delete

# Events: keep 7 days
find ~/printer_ai/events -type f -mtime +7 -delete

# Timelapse: keep 14 days
find ~/printer_ai/timelapse -type f -mtime +14 -delete

# Logs: keep 7 days
find ~/printer_ai/logs -type f -mtime +7 -delete
