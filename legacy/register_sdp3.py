#!/usr/bin/env python3
"""Register HID SDP record using sdp_device_record_register_binary. Must run as root."""
import ctypes, ctypes.util, struct, sys, os

# Set errno accessible
libc = ctypes.CDLL("libc.so.6", use_errno=True)
lib  = ctypes.CDLL("libbluetooth.so.3", use_errno=True)

class bdaddr_t(ctypes.Structure):
    _fields_ = [("b", ctypes.c_uint8 * 6)]

def str2ba(s):
    ba = bdaddr_t()
    for i, v in enumerate(reversed([int(x,16) for x in s.split(":")])):
        ba.b[i] = v
    return ba

BDADDR_ANY   = str2ba("00:00:00:00:00:00")
BDADDR_LOCAL = str2ba("FF:FF:FF:00:00:00")

lib.sdp_connect.restype  = ctypes.c_void_p
lib.sdp_connect.argtypes = [ctypes.POINTER(bdaddr_t), ctypes.POINTER(bdaddr_t), ctypes.c_uint32]

lib.sdp_close.restype  = ctypes.c_int
lib.sdp_close.argtypes = [ctypes.c_void_p]

lib.sdp_device_record_register_binary.restype  = ctypes.c_int
lib.sdp_device_record_register_binary.argtypes = [
    ctypes.c_void_p,           # session
    ctypes.POINTER(bdaddr_t),  # device bdaddr (use BDADDR_ANY for default)
    ctypes.c_char_p,           # data (serialized record bytes)
    ctypes.c_uint32,           # size
    ctypes.c_uint8,            # flags
    ctypes.POINTER(ctypes.c_uint32),  # handle out
]

lib.sdp_record_register.restype  = ctypes.c_int
lib.sdp_record_register.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint8]

SDP_RETRY_IF_BUSY = 0x01
SDP_RECORD_PERSIST = 0x01

# --- Build serialized SDP record (DES of attribute-value pairs) ---
def sdp_uint8(v):   return bytes([0x08, v])
def sdp_uint16(v):  return bytes([0x09]) + struct.pack(">H", v)
def sdp_uint32(v):  return bytes([0x0A]) + struct.pack(">I", v)
def sdp_bool(v):    return bytes([0x28, 0x01 if v else 0x00])
def sdp_uuid16(v):  return bytes([0x19]) + struct.pack(">H", v)
def sdp_text(s):
    b = s.encode() if isinstance(s, str) else s
    if len(b) <= 255:
        return bytes([0x25, len(b)]) + b
    return bytes([0x26]) + struct.pack(">H", len(b)) + b
def sdp_seq(items):
    body = b"".join(items)
    if len(body) <= 255:
        return bytes([0x35, len(body)]) + body
    return bytes([0x36]) + struct.pack(">H", len(body)) + body
def sdp_attr(attr_id, elem):
    return bytes([0x09]) + struct.pack(">H", attr_id) + elem

KBD = bytes([
    0x05,0x01, 0x09,0x06, 0xA1,0x01, 0x85,0x01,
    0x05,0x07, 0x19,0xE0, 0x29,0xE7,
    0x15,0x00, 0x25,0x01, 0x75,0x01, 0x95,0x08, 0x81,0x02,
    0x95,0x01, 0x75,0x08, 0x81,0x01,
    0x95,0x06, 0x75,0x08, 0x15,0x00, 0x25,0x65,
    0x05,0x07, 0x19,0x00, 0x29,0x65, 0x81,0x00, 0xC0,
])
MOUSE = bytes([
    0x05,0x01, 0x09,0x02, 0xA1,0x01, 0x85,0x02,
    0x09,0x01, 0xA1,0x00,
    0x05,0x09, 0x19,0x01, 0x29,0x03,
    0x15,0x00, 0x25,0x01, 0x95,0x03, 0x75,0x01, 0x81,0x02,
    0x95,0x01, 0x75,0x05, 0x81,0x01,
    0x05,0x01, 0x09,0x30, 0x09,0x31, 0x09,0x38,
    0x15,0x81, 0x25,0x7F, 0x75,0x08, 0x95,0x03, 0x81,0x06,
    0xC0, 0xC0,
])
HID_DESC = KBD + MOUSE

attrs = b""
attrs += sdp_attr(0x0001, sdp_seq([sdp_uuid16(0x1124)]))  # ServiceClassIDList
attrs += sdp_attr(0x0004, sdp_seq([                        # ProtocolDescriptorList
    sdp_seq([sdp_uuid16(0x0100), sdp_uint16(0x0011)]),     #   L2CAP PSM=17
    sdp_seq([sdp_uuid16(0x0011)])                           #   HIDP
]))
attrs += sdp_attr(0x0005, sdp_seq([sdp_uuid16(0x1002)]))   # BrowseGroupList
attrs += sdp_attr(0x000D, sdp_seq([sdp_seq([               # AdditionalProtocolDescriptorList
    sdp_seq([sdp_uuid16(0x0100), sdp_uint16(0x0013)]),     #   L2CAP PSM=19 (interrupt)
    sdp_seq([sdp_uuid16(0x0011)])                           #   HIDP
])]))
attrs += sdp_attr(0x0009, sdp_seq([                        # BluetoothProfileDescriptorList
    sdp_seq([sdp_uuid16(0x1124), sdp_uint16(0x0101)])      #   HID v1.1
]))
attrs += sdp_attr(0x0100, sdp_text("BT HID Relay"))
attrs += sdp_attr(0x0200, sdp_uint16(0x0111))              # HIDParserVersion
attrs += sdp_attr(0x0201, sdp_uint8(0xC0))                 # HIDDeviceSubclass: kbd+mouse
attrs += sdp_attr(0x0202, sdp_uint8(0x21))                 # HIDCountryCode: US
attrs += sdp_attr(0x0203, sdp_bool(True))                  # HIDVirtualCable
attrs += sdp_attr(0x0204, sdp_bool(True))                  # HIDReconnectInitiate
attrs += sdp_attr(0x0205, sdp_seq([sdp_seq([              # HIDDescriptorList
    sdp_uint8(0x22), sdp_text(HID_DESC)
])]))
attrs += sdp_attr(0x0206, sdp_seq([sdp_seq([              # HIDLANGIDBaseList
    sdp_uint16(0x0409), sdp_uint16(0x0100)
])]))
attrs += sdp_attr(0x0209, sdp_bool(False))                 # HIDBatteryPower
attrs += sdp_attr(0x020A, sdp_bool(False))                 # HIDRemoteWake
attrs += sdp_attr(0x020B, sdp_uint16(0x0100))              # HIDProfileVersion
attrs += sdp_attr(0x020C, sdp_uint16(0x0C80))              # HIDSupervisionTimeout
attrs += sdp_attr(0x020D, sdp_bool(False))                 # HIDNormallyConnectable
attrs += sdp_attr(0x020E, sdp_bool(True))                  # HIDBootDevice

# Wrap in outer DES
record_bytes = sdp_seq([attrs])
print(f"Record size: {len(record_bytes)} bytes")

# Connect to local SDP daemon
src = BDADDR_ANY
dst = BDADDR_LOCAL

session = lib.sdp_connect(ctypes.byref(src), ctypes.byref(dst), SDP_RETRY_IF_BUSY)
if not session:
    err = ctypes.get_errno()
    print(f"sdp_connect failed: errno={err} ({os.strerror(err)})")
    sys.exit(1)
print(f"SDP session connected: 0x{session:x}")

# Register record using binary API
handle = ctypes.c_uint32(0)
result = lib.sdp_device_record_register_binary(
    session,
    ctypes.byref(BDADDR_ANY),
    record_bytes,
    ctypes.c_uint32(len(record_bytes)),
    ctypes.c_uint8(SDP_RECORD_PERSIST),
    ctypes.byref(handle)
)

if result < 0:
    err = ctypes.get_errno()
    print(f"sdp_device_record_register_binary failed: result={result}, errno={err} ({os.strerror(err)})")
else:
    print(f"SUCCESS: HID record registered, handle=0x{handle.value:08x}")
    print("Verify: sudo sdptool browse local | grep -A2 HID")

lib.sdp_close(session)
