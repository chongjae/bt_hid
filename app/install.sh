#!/bin/bash
# Install BT HID Relay.  Run: sudo ./install.sh
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "sudo ./install.sh 로 실행해 주세요"; exit 1; }
USER_NAME="${SUDO_USER:?sudo로 실행해야 해요 (SUDO_USER 필요)}"
USER_HOME=$(getent passwd "$USER_NAME" | cut -d: -f6)
USER_UID=$(id -u "$USER_NAME")
SRC=$(cd "$(dirname "$0")" && pwd)
LIB=/usr/local/lib/bthid

echo "[0/7] 필요한 패키지 확인/설치"
command -v apt-get >/dev/null || { echo "apt-get이 없어요 (Ubuntu/Debian 계열만 지원)"; exit 1; }
DEPS="python3 python3-gi python3-dbus python3-evdev gir1.2-glib-2.0 gir1.2-gtk-3.0 gir1.2-ayatanaappindicator3-0.1 bluez"
if command -v gnome-shell >/dev/null; then DEPS="$DEPS gnome-shell-extension-appindicator"; fi
MISSING=$(for p in $DEPS; do dpkg-query -W -f='${Status}' "$p" 2>/dev/null | grep -q 'ok installed' || echo "$p"; done | xargs)
if [ -n "$MISSING" ]; then
  echo "  설치: $MISSING"
  apt-get update -qq
  apt-get install -y $MISSING
fi

echo "[1/7] 설치 전 진단 (시스템 파이썬 /usr/bin/python3 기준, conda/venv 무관)"
if ! /usr/bin/python3 "$SRC/bthid_doctor.py" --pre; then
  echo; echo "위 FAIL 항목을 먼저 해결해 주세요. 설치를 중단해요."; exit 1
fi

echo "[2/7] 이전 실험용 프로세스 정리"
pkill -x bt-agent 2>/dev/null || true
pkill -f 'bt_hid/(legacy/)?bt_hid\.py' 2>/dev/null || true
pkill -f 'bt_hid/(legacy/)?start\.sh' 2>/dev/null || true

echo "[3/7] 파일 설치 → $LIB (root 소유)"
install -d -m 755 "$LIB"
install -m 644 "$SRC/bthidd.py" "$SRC/hid.xml" "$LIB/"
install -m 755 "$SRC/bthid_tray.py" "$SRC/bthid_doctor.py" "$LIB/"
install -m 644 "$SRC/bthidd.service" /etc/systemd/system/bthidd.service
ln -sf "$LIB/bthid_tray.py" /usr/local/bin/bthid-tray
ln -sf "$LIB/bthid_doctor.py" /usr/local/bin/bthid-doctor

echo "[4/7] 설정 파일"
install -d -m 755 /etc/bthid
if [ ! -f /etc/bthid/config.json ]; then
  printf '{\n  "user": "%s",\n  "target": null,\n  "devices": [],\n  "hotkey": [97, 100]\n}\n' "$USER_NAME" > /etc/bthid/config.json
fi

echo "[5/7] bluetoothd: 키보드 장치 클래스 + input 플러그인 비활성화"
BTD=$(for p in /usr/libexec/bluetooth/bluetoothd /usr/lib/bluetooth/bluetoothd /usr/sbin/bluetoothd; do [ -x "$p" ] && echo "$p" && break; done || true)
[ -n "$BTD" ] || { echo "bluetoothd 실행 파일을 못 찾았어요 (bluez 설치 확인)"; exit 1; }
MAIN=/etc/bluetooth/main.conf
install -d /etc/bluetooth
[ -f "$MAIN" ] || printf '[General]\n' > "$MAIN"
[ -f "$MAIN.bthid.bak" ] || cp "$MAIN" "$MAIN.bthid.bak"
grep -q '^\[General\]' "$MAIN" || sed -i '1i [General]' "$MAIN"
if grep -qE '^\s*Class\s*=' "$MAIN"; then
  sed -i -E 's/^\s*Class\s*=.*/Class = 0x002540/' "$MAIN"
else
  sed -i '/^\[General\]/a Class = 0x002540' "$MAIN"
fi
install -d /etc/systemd/system/bluetooth.service.d
DROPIN=/etc/systemd/system/bluetooth.service.d/hid.conf
if [ -f "$DROPIN" ] && [ ! -f "$DROPIN.bthid.bak" ]; then cp "$DROPIN" "$DROPIN.bthid.bak"; fi
printf '[Service]\nExecStart=\nExecStart=%s -C --noplugin=input\n' "$BTD" > "$DROPIN"

echo "[6/7] 서비스 시작"
systemctl daemon-reload
systemctl restart bluetooth
systemctl enable bthidd
systemctl restart bthidd

echo "[7/7] 트레이 자동 시작 등록"
GRP=$(id -gn "$USER_NAME")
install -d -o "$USER_NAME" -g "$GRP" "$USER_HOME/.config" "$USER_HOME/.config/autostart"
install -m 644 -o "$USER_NAME" -g "$GRP" "$SRC/bthid-tray.desktop" "$USER_HOME/.config/autostart/"
if command -v gnome-extensions >/dev/null && [ -S "/run/user/$USER_UID/bus" ]; then
  sudo -u "$USER_NAME" DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$USER_UID/bus" \
    gnome-extensions enable ubuntu-appindicators@ubuntu.com 2>/dev/null || true
fi

echo
sleep 2
echo "── 설치 후 진단 ──"
/usr/bin/python3 "$LIB/bthid_doctor.py" || true
echo
echo "설치 완료. 트레이는 다음 로그인부터 자동으로 떠요."
echo "지금 바로 띄우려면 (sudo 없이):  bthid-tray &"
echo "문제가 있으면 (sudo 없이):       bthid-doctor"
echo "로그 보기:                       journalctl -u bthidd -f"
