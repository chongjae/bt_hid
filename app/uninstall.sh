#!/bin/bash
# Remove BT HID Relay.  Run: sudo ./uninstall.sh
set -uo pipefail
[ "$(id -u)" = 0 ] || { echo "sudo ./uninstall.sh 로 실행해 주세요"; exit 1; }
USER_HOME=$(getent passwd "${SUDO_USER:-root}" | cut -d: -f6)
systemctl disable --now bthidd 2>/dev/null
rm -f /etc/systemd/system/bthidd.service /usr/local/bin/bthid-tray /usr/local/bin/bthid-doctor "$USER_HOME/.config/autostart/bthid-tray.desktop"
rm -f /run/systemd/system/bluetooth.service.d/zz-bthid-pair.conf
# 설치 전 상태로 복원: .bthid.bak = 원래 파일, .bthid.none = 원래 없었음
restore() {  # $1 = file
  if [ -f "$1.bthid.bak" ]; then mv "$1.bthid.bak" "$1"
  elif [ -f "$1.bthid.none" ]; then rm -f "$1" "$1.bthid.none"
  elif [ "$(head -1 "$1" 2>/dev/null)" = "# bthid-managed" ]; then rm -f "$1"
  fi
}
restore /etc/systemd/system/bluetooth.service.d/hid.conf      # BlueZ input 플러그인 복원
rm -rf /usr/local/lib/bthid
restore /etc/bluetooth/main.conf
systemctl daemon-reload
systemctl restart bluetooth
pkill -f bthid_tray.py 2>/dev/null
echo "삭제 완료 (/etc/bthid/config.json 은 남겨 뒀어요)."
