# legacy — 개발 중 실험 스크립트 (참고용)

정식 프로그램은 `../app/`이에요. 여기 파일은 설치되지 않아요.

| 파일 | 내용 |
|---|---|
| `bt_hid.py` | 수동 실행형 릴레이 프로토타입. 동작 확인을 마친 마지막 버전이에요 (`sudo ./start.sh`로 실행) |
| `bt_hid.py.bak` | 6월 버전 (`hci1` 하드코딩 버그 있음) |
| `hid.xml` / `hid.xml.bak` | 고친 SDP 레코드 / 속성 번호가 한 칸씩 밀린 원래 레코드 (폰이 연결을 끊던 원인) |
| `register_sdp*.py`, `test_connect.py`, `diag.sh` | 6월에 SDP 등록과 L2CAP 연결을 실험한 스크립트 |

공개 저장소라서 MAC 주소는 예시 값(`AA:BB:CC:...`)으로 바꿔 뒀어요. 직접 실행하려면 `bt_hid.py`의 `PHONE_MAC`을 실제 폰 주소로 고쳐야 해요.
