#!/bin/bash
# Install BT HID Relay.  Run: sudo ./install.sh
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "sudo ./install.sh 로 실행해 주세요"; exit 1; }
USER_NAME="${SUDO_USER:?sudo로 실행해야 해요 (SUDO_USER 필요)}"
USER_HOME=$(getent passwd "$USER_NAME" | cut -d: -f6)
SRC=$(cd "$(dirname "$0")" && pwd)
LIB=/usr/local/lib/bthid

echo "[0/6] 필요한 패키지 설치"
DEPS="python3-gi python3-dbus python3-evdev gir1.2-gtk-3.0 gir1.2-ayatanaappindicator3-0.1 gnome-shell-extension-appindicator bluez"
MISSING=$(for p in $DEPS; do dpkg-query -W -f='${Status}' "$p" 2>/dev/null | grep -q 'ok installed' || echo "$p"; done)
if [ -n "$MISSING" ]; then
  echo "  설치: $MISSING"
  apt-get update -qq && apt-get install -y $MISSING
fi
# 시스템 파이썬(/usr/bin/python3)으로 모듈이 실제로 import되는지 확인 (conda/venv 파이썬은 쓰지 않아요)
/usr/bin/python3 - <<'PY' || { echo "필수 파이썬 모듈 확인 실패 — 위 오류를 확인해 주세요"; exit 1; }
import gi, dbus, evdev
gi.require_version("Gtk", "3.0"); gi.require_version("AyatanaAppIndicator3", "0.1")
from gi.repository import Gtk, GLib, AyatanaAppIndicator3
PY
# 트레이 아이콘 표시용 GNOME 확장 켜기 (사용자 세션에서)
sudo -u "$USER_NAME" DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$(id -u "$USER_NAME")/bus" \
  gnome-extensions enable ubuntu-appindicators@ubuntu.com 2>/dev/null || true

echo "[1/6] 이전 실험용 프로세스 정리"
pkill -x bt-agent 2>/dev/null || true
pkill -f 'bt_hid/(legacy/)?bt_hid\.py' 2>/dev/null || true
pkill -f 'bt_hid/(legacy/)?start\.sh' 2>/dev/null || true

echo "[2/6] 파일 설치 → $LIB (root 소유)"
install -d -m 755 "$LIB"
install -m 644 "$SRC/bthidd.py" "$SRC/bthid_tray.py" "$SRC/hid.xml" "$LIB/"
install -m 644 "$SRC/bthidd.service" /etc/systemd/system/bthidd.service

echo "[3/6] 설정 파일"
install -d -m 755 /etc/bthid
if [ ! -f /etc/bthid/config.json ]; then
  printf '{\n  "user": "%s",\n  "target": null,\n  "devices": [],\n  "hotkey": [97, 100]\n}\n' "$USER_NAME" > /etc/bthid/config.json
fi

echo "[4/6] bluetoothd: 키보드 장치 클래스 + input 플러그인 비활성화"
MAIN=/etc/bluetooth/main.conf
cp -n "$MAIN" "$MAIN.bthid.bak"
if grep -qE '^\s*Class\s*=' "$MAIN"; then
  sed -i -E 's/^\s*Class\s*=.*/Class = 0x002540/' "$MAIN"
else
  sed -i '/^\[General\]/a Class = 0x002540' "$MAIN"
fi
install -d /etc/systemd/system/bluetooth.service.d
DROPIN=/etc/systemd/system/bluetooth.service.d/hid.conf
[ -f "$DROPIN" ] && cp -n "$DROPIN" "$DROPIN.bthid.bak"
printf '[Service]\nExecStart=\nExecStart=/usr/libexec/bluetooth/bluetoothd -C --noplugin=input\n' > "$DROPIN"

echo "[5/6] 서비스 시작"
systemctl daemon-reload
systemctl restart bluetooth
systemctl enable --now bthidd
systemctl restart bthidd

echo "[6/6] 트레이 자동 시작 등록"
install -d -o "$USER_NAME" -g "$(id -gn "$USER_NAME")" "$USER_HOME/.config/autostart"
install -m 644 -o "$USER_NAME" -g "$(id -gn "$USER_NAME")" "$SRC/bthid-tray.desktop" "$USER_HOME/.config/autostart/"
ln -sf "$LIB/bthid_tray.py" /usr/local/bin/bthid-tray
chmod 755 "$LIB/bthid_tray.py"

echo
echo "설치 완료. 트레이는 다음 로그인부터 자동으로 떠요."
echo "지금 바로 띄우려면 (sudo 없이):  bthid-tray &"
echo "로그 보기:  journalctl -u bthidd -f"
