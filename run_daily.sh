#!/bin/zsh
# Wrapper invoked by launchd. Runs the daily NSE scan and logs output.
cd /Users/nithin/fyers-connect || exit 1
export PYTHONWARNINGS=ignore
mkdir -p logs
echo "===== $(date) =====" >> logs/daily_scan.log
/usr/bin/python3 daily_scan.py >> logs/daily_scan.log 2>&1
echo "exit: $? at $(date)" >> logs/daily_scan.log
