# 사용 설명서

## 0. 요구 사항

- **OS:** Ubuntu 22.04 / 24.04
  - 24.04는 실기기로 확인했어요.
  - 22.04는 깨끗한 컨테이너에서 패키지 설치와 모듈 import까지만 확인했어요.
  - 블루투스 어댑터가 있어야 해요.
- **시스템 패키지:** `install.sh`가 없는 걸 확인해서 apt로 자동 설치해요.
  - `python3 python3-gi python3-dbus python3-evdev gir1.2-glib-2.0 gir1.2-gtk-3.0 gir1.2-ayatanaappindicator3-0.1 bluez`
  - GNOME이면 `gnome-shell-extension-appindicator`도 설치해요.
- **PC마다 다른 경로는 자동으로 찾아요.**
  - `bluetoothd` 위치: 24.04는 `/usr/libexec/bluetooth/`, 22.04는 `/usr/lib/bluetooth/`
  - 블루투스가 꺼져 있으면(rfkill) 페어링을 시작할 때 자동으로 켜요.
- **자가 진단:** `bthid-doctor`를 sudo 없이 실행하면 아래 항목을 한 번에 점검하고 해결 방법을 알려 줘요.
  - OS, 파이썬 모듈, apt 패키지
  - 어댑터, rfkill, bluetoothd 버전·옵션, systemd 버전, 오디오 스택
  - 설치 파일, 서비스, 제어 소켓 권한
  - GNOME 트레이 확장, 트레이 실행 여부
  - 설치 전에 검사만 하려면 `/usr/bin/python3 app/bthid_doctor.py --pre`를 실행해요.
- 프로그램은 항상 **시스템 파이썬 `/usr/bin/python3`**으로 실행돼요. conda나 venv 파이썬에는 `gi`(PyGObject)가 없어서 `ModuleNotFoundError: No module named 'gi'`가 나요. `pip install`로 해결하지 말고 위 apt 패키지를 쓰세요.

## 1. 설치

```bash
sudo ~/bt_hid/app/install.sh
bthid-tray &        # 지금 바로 트레이 띄우기 (sudo 없이)
```

`install.sh`가 하는 일은 이래요.

0. 필요한 패키지를 확인하고, 없으면 apt로 설치해요. 그다음 `bthid-doctor --pre`로 점검하고, FAIL이 있으면 설치를 멈춰요.
1. 예전 실험용 프로세스를 정리해요. `bt-agent`, `legacy/bt_hid.py`, `start.sh`가 대상이에요.
2. 프로그램을 `/usr/local/lib/bthid/`에 복사해요. root 소유로 둬서 일반 사용자가 고칠 수 없어요.
3. 설정 파일 `/etc/bthid/config.json`을 만들어요. 이미 있으면 건드리지 않아요.
4. 블루투스 설정 두 가지를 바꿔요.
   - `/etc/bluetooth/main.conf`에 `Class = 0x002540`을 넣어요. 이 PC가 키보드·마우스로 보이게 하는 설정이에요.
   - `bluetooth.service`가 `-C --noplugin=input` 옵션으로 실행되게 해요.
   - 원래 파일은 `.bthid.bak`으로 백업해요.
5. `bthidd` 서비스를 켜고, 부팅할 때 자동으로 시작되게 등록해요.
6. 트레이가 로그인할 때 자동으로 뜨게 `~/.config/autostart/`에 등록해요.

> **주의:** 설치하면 이 PC에 블루투스 키보드·마우스를 *직접* 연결하는 기능이 꺼져요(`--noplugin=input`). 2.4G 수신기나 USB 장치는 영향이 없어요.

## 2. 제거

```bash
sudo ~/bt_hid/app/uninstall.sh
```

서비스, 프로그램 파일, 자동 시작 항목을 지워요. 블루투스 설정은 백업해 둔 원래 상태로 되돌려요. `/etc/bthid/config.json`은 남겨 둬요.

## 3. 트레이와 설정 창

우측 상단 아이콘 모양으로 상태를 알 수 있어요.

| 아이콘 | 뜻 |
|---|---|
| 블루투스 꺼짐 모양 | 폰과 연결 안 됨 |
| 블루투스 켜짐 모양 | 폰과 연결됨, 입력 전달은 꺼짐 |
| 키보드 모양 | 입력 전달 중 (PC 입력 차단) |
| 오류 모양 | 데몬(`bthidd`)이 꺼져 있음 |

메뉴 항목은 세 가지예요.
- **설정 열기:** 설정 창을 열어요.
- **입력 전달 끄기:** 비상용이에요.
- **트레이 종료:** 트레이만 닫아요.

설정 창에 보이는 정보예요.
- **상태:** 상태, HID 연결, 입력 전달
- **기기 정보:** 기기 이름, MAC 주소, 블루투스 링크
- **현재 단축키**

### 신규 장비 페어링
1. 버튼을 누르고 확인을 눌러요. PC가 **2분 동안 검색 가능한 상태**가 돼요.
   - 이 동안 **PC 블루투스 오디오(Shokz 등)가 잠시 끊겨요.** 블루투스 서비스를 오디오 기능 없이 다시 띄우기 때문이에요.
2. 폰: 설정 → 블루투스 → 이 PC(`<PC 이름>`)를 눌러 페어링해요. 숫자 확인 창이 뜨면 확인을 눌러요.
3. 페어링이 끝나면 약 8초 뒤에 블루투스가 원래 상태로 돌아와요. 그다음 PC가 폰에 키보드 연결을 자동으로 걸어요.
4. 폰에서 이 PC 설정을 열었을 때 **"텍스트 입력"만 켜져 있으면 정상이에요.**

페어링하는 동안 버튼이 "페어링 취소"로 바뀌어요. 2분 안에 페어링하지 않으면 자동으로 취소돼요.

### 기존 장비 페어링 삭제
이 앱으로 페어링한 기기만 목록에 나와요. 이어폰 같은 다른 블루투스 기기는 표시되지 않아요. 삭제한 뒤에는 **폰에서도 이 PC를 "등록 해제"해 주세요.**

### relay 단축키 설정
1. 창이 뜨면 새 조합을 **동시에 눌렀다 떼고** "저장"을 눌러요.
   - 2~4개 키 조합이어야 해요.
   - Ctrl·Alt·Shift·Win 중 하나는 꼭 넣어야 해요.
2. 다시 고르려면 "다시 입력"을 눌러요.

단축키로 쓴 키는 폰으로 전달되지 않아요. 창이 열려 있는 동안은 기존 단축키가 동작하지 않아요.

## 4. 입력 전달

| 동작 | 키 |
|---|---|
| 전달 켜기/끄기 | **오른쪽 Ctrl(한자) + 오른쪽 Alt(한/영)** (기본값, 바꿀 수 있어요) |
| 폰 한/영 전환 | 전달 중 **한/영 키만** 눌렀다 떼기 → 폰에 Shift+Space가 가요 |

- **켜는 시점:** 단축키를 **뗄 때** 켜져요. 눌린 키가 PC에 남는 걸 막으려고 그렇게 했어요.
- **켜진 동안:** 키보드·마우스 입력이 **PC 화면에는 가지 않아요.**
- **자동으로 꺼지는 경우:** 폰 연결이 끊기거나, 전송이 1초 넘게 멈추거나, 데몬이 죽으면 전달이 꺼지고 **PC 입력이 바로 돌아와요.**

## 5. 문제 해결

| 증상 | 확인할 것 |
|---|---|
| 무엇이든 이상할 때 | **먼저 `bthid-doctor` 실행.** FAIL 항목에 해결 방법이 함께 나와요 |
| `No module named 'gi'` | conda/venv 파이썬으로 실행한 경우예요. `/usr/bin/python3 /usr/local/lib/bthid/bthid_tray.py`로 실행하거나 `sudo ./install.sh`를 다시 실행해요 (패키지 자동 설치) |
| 트레이에 "데몬 미실행" | `systemctl status bthidd`, 다시 켜려면 `sudo systemctl restart bthidd` |
| 폰 연결이 안 됨 | 폰 블루투스가 켜져 있는지, 폰 설정에서 이 PC의 "텍스트 입력"이 켜져 있는지 |
| 폰에서 "텍스트 입력"이 켜졌다 바로 꺼짐 | 폰과 PC 양쪽에서 페어링을 지우고 "신규 장비 페어링"을 다시 해요 |
| 한/영 전환이 안 됨 | 폰 키보드의 언어 전환 단축키를 확인해요. 바꾸려면 `bthidd.py`의 `LANG_SWITCH`를 수정해요 (Ctrl+Space는 `(0x01, 0x2C)`) |
| PC 입력이 안 돌아옴 (비상) | 다른 PC에서 SSH로 접속해 `sudo systemctl stop bthidd`를 실행해요. 프로세스가 끝나면 입력 차단이 자동으로 풀려요 |

**로그 보기:**
```bash
journalctl -u bthidd -f
```
페어링, 연결, 오디오 거절(`AuthorizeService ... REJECT`), 전달 켜기/끄기가 모두 기록돼요.

**폰 쪽 로그** (필요할 때만): 폰에서 무선 디버깅을 켜고 `adb connect <IP>:<포트>` 후 아래를 실행해요.
```bash
adb logcat --pid=$(adb shell pidof com.android.bluetooth) | grep -i hh
```

## 6. 개발과 테스트

```bash
cd ~/bt_hid/app
DISPLAY=:0 python3 -m unittest discover -s tests          # 테스트 80개
DISPLAY=:0 python3 -m coverage run --include='bthidd.py,bthid_tray.py,bthid_doctor.py' -m unittest discover -s tests && python3 -m coverage report
```

코드를 고친 뒤에는 `sudo ./install.sh`를 다시 실행해야 반영돼요. 설정 파일은 그대로 남아요.
