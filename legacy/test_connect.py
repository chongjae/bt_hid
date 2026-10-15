#!/usr/bin/env python3
"""
Test if phone is listening on PSM 17/19 (phone = HID host server, PC = HID device client).
Run WHILE '텍스트 입력' is toggled ON on S25 Edge.
"""
import socket, sys, time

PHONE_MAC = "AA:BB:CC:11:22:33"

for psm in [0x11, 0x13]:
    print(f"Trying to connect to phone PSM 0x{psm:02x}...", end=" ", flush=True)
    try:
        s = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_SEQPACKET, socket.BTPROTO_L2CAP)
        s.settimeout(5)
        s.connect((PHONE_MAC, psm))
        print(f"CONNECTED! Phone is listening on PSM 0x{psm:02x}")
        data = s.recv(64)
        print(f"  received: {data.hex() if data else '(empty)'}")
        s.close()
    except Exception as e:
        print(f"failed: {e}")
