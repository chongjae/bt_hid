#!/bin/bash
# Run as: sudo /home/chongjae/bt_hid/start.sh
pkill -9 -f bt_hid.py 2>/dev/null
pkill -9 btmon 2>/dev/null
sleep 1
rfkill unblock bluetooth
bluetoothctl power on >/dev/null
bluetoothctl pairable on >/dev/null
rm -f /tmp/bthid.log
rm -f /tmp/bt.snoop; btmon -w /tmp/bt.snoop >/dev/null 2>&1 &   # binary packet trace (text mode crashes in 5.72)
BTMON=$!
trap 'kill $BTMON 2>/dev/null; chmod 644 /tmp/bt.snoop' EXIT
python3 -u /home/chongjae/bt_hid/legacy/bt_hid.py 2>&1 | tee /tmp/bthid.log
