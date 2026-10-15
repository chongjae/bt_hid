#!/bin/bash
# Run as: sudo /home/chongjae/bt_hid/diag.sh
# Checks if phone makes ACL + L2CAP connection to PSM 17

PHONE_MAC="AA:BB:CC:11:22:33"

pkill -9 -f bt_hid.py 2>/dev/null
pkill -9 l2test 2>/dev/null
sleep 1

echo "=== Starting L2CAP server on PSM 17 ==="
l2test -r -P 17 -i hci0 &
L2_PID=$!
echo "l2test PID: $L2_PID"

echo ""
echo "=== NOW: toggle 텍스트 입력 on S25 Edge ==="
echo "Monitoring for 20 seconds..."
echo ""

for i in $(seq 1 20); do
  printf "[%2ds] " $i
  CONS=$(hcitool con 2>/dev/null | grep -v "^Connections:")
  if echo "$CONS" | grep -qi "$PHONE_MAC"; then
    echo "*** ACL CONNECTION TO PHONE DETECTED ***"
    echo "$CONS"
  elif [ -n "$CONS" ]; then
    echo "ACL (other): $CONS"
  else
    echo "no ACL"
  fi
  sleep 1
done

kill $L2_PID 2>/dev/null
echo ""
echo "=== l2test output (any connections?) ==="
wait $L2_PID 2>/dev/null
