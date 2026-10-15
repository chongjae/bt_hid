# BT HID Relay

> 전체 문서는 [../README.md](../README.md), [../docs/USAGE.md](../docs/USAGE.md)에 있어요.

PC 키보드·마우스 입력을 블루투스로 폰(S25 Edge 등 Android)에 전달하는 프로그램이에요.
PC가 블루투스 키보드·마우스처럼 동작하고, 폰은 "텍스트 입력"(입력 장치)으로만 연결돼요.

## 구성

| 파일 | 역할 |
|---|---|
| `bthidd.py` | 데몬. root로 systemd에서 실행돼요. HID 연결, 입력 전달, 페어링 에이전트를 맡고, 제어 소켓은 `/run/bthid/ctl.sock`이에요. |
| `bthid_tray.py` | 우측 상단 트레이 아이콘과 설정 창이에요. 사용자 권한으로 실행돼요. |
| `hid.xml` | 폰에 알려 주는 키보드·마우스 장치 정보(SDP 레코드)예요. |
| `install.sh` / `uninstall.sh` | 설치와 제거 스크립트예요. |
| `/etc/bthid/config.json` | 설정 파일이에요. 대상 폰, 이 앱으로 페어링한 기기 목록, 단축키가 들어 있어요. |

## 설치와 제거

```bash
cd ~/bt_hid/app
sudo ./install.sh        # 설치
bthid-tray &             # 트레이 바로 실행 (다음 로그인부터는 자동으로 떠요)
sudo ./uninstall.sh      # 제거
```

## 사용법

- **전달 켜기/끄기:** 오른쪽 Ctrl(한자) + 오른쪽 Alt(한/영)를 눌러요. 단축키는 설정 창에서 바꿀 수 있어요.
  - 전달이 켜진 동안 PC 화면에는 입력이 가지 않아요.
  - 폰 연결이 끊기면 전달이 자동으로 꺼지고 PC 입력이 돌아와요.
- **한/영 전환:** 전달 중에 한/영 키만 눌렀다 떼면 폰에 Shift+Space가 가요.
- **신규 장비 페어링:** 약 2분간 PC가 검색 가능한 상태가 돼요. 폰에서 이 PC를 골라 페어링하면 돼요.
  - 이 동안 PC 블루투스 오디오(이어폰 등)가 잠시 끊겨요. 블루투스 서비스를 오디오 기능 없이 다시 띄우기 때문이에요.
  - 그래서 폰은 이 PC를 통화·오디오 없는 키보드로 기억해요.
  - 페어링한 폰은 PC 쪽에서 "신뢰하지 않음"으로 둬요. 그래서 폰이 나중에 오디오 연결을 시도해도 PC가 거절해요.
- **기존 장비 페어링 삭제:** 이 앱으로 페어링한 기기만 목록에 나와요. 이어폰 같은 다른 기기는 표시되지 않아요.

## 주의

- 설치하면 bluetoothd의 `input` 플러그인이 꺼져요. 이 PC에 블루투스 키보드·마우스를 직접 연결하는 기능을 쓸 수 없게 돼요. 2.4G 수신기나 USB 장치는 영향이 없어요. `uninstall.sh`로 제거하면 원래대로 돌아와요.
- 로그는 `journalctl -u bthidd -f`로 볼 수 있어요.

## 테스트

```bash
python3 -m unittest discover -s tests
python3 -m coverage run --include='bthidd.py,bthid_tray.py,bthid_doctor.py' -m unittest discover -s tests && python3 -m coverage report
```

## 진단

```bash
bthid-doctor          # 설치 후 전체 진단
/usr/bin/python3 bthid_doctor.py --pre   # 설치 전
```
