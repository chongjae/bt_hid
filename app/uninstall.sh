#!/bin/bash
# Remove BT HID Relay.  Run: sudo ./uninstall.sh
set -uo pipefail
[ "$(id -u)" = 0 ] || { echo "sudo ./uninstall.sh 로 실행해 주세요"; exit 1; }
USER_HOME=$(getent passwd "${SUDO_USER:-root}" | cut -d: -f6)
systemctl disable --now bthidd 2>/dev/null
rm -f /etc/systemd/system/bthidd.service /usr/local/bin/bthid-tray /usr/local/bin/bthid-doctor "$USER_HOME/.config/autostart/bthid-tray.desktop"
rm -f /run/systemd/system/bluetooth.service.d/zz-bthid-pair.conf
DROPIN=/etc/systemd/system/bluetooth.service.d/hid.conf
if [ -f "$DROPIN.bthid.bak" ]; then mv "$DROPIN.bthid.bak" "$DROPIN"   # pre-install state
else rm -f "$DROPIN"; fi                                              # restores BlueZ input plugin
rm -rf /usr/local/lib/bthid
[ -f /etc/bluetooth/main.conf.bthid.bak ] && mv /etc/bluetooth/main.conf.bthid.bak /etc/bluetooth/main.conf
systemctl daemon-reload
systemctl restart bluetooth
pkill -f bthid_tray.py 2>/dev/null
echo "삭제 완료 (/etc/bthid/config.json 은 남겨 뒀어요)."
