#!/usr/bin/env python3
"""Register HID SDP record using libbluetooth ctypes API. Must run as root."""
import ctypes, struct, sys, subprocess

lib = ctypes.CDLL("libbluetooth.so.3")

class bdaddr_t(ctypes.Structure):
    _fields_ = [("b", ctypes.c_uint8 * 6)]

def str2ba(s):
    ba = bdaddr_t()
    for i, v in enumerate(reversed([int(x,16) for x in s.split(":")])):
        ba.b[i] = v
    return ba

BDADDR_ANY   = str2ba("00:00:00:00:00:00")
BDADDR_LOCAL = str2ba("FF:FF:FF:00:00:00")  # BlueZ local SDP daemon

lib.sdp_connect.restype  = ctypes.c_void_p
lib.sdp_connect.argtypes = [ctypes.POINTER(bdaddr_t), ctypes.POINTER(bdaddr_t), ctypes.c_uint32]
lib.sdp_record_alloc.restype  = ctypes.c_void_p
lib.sdp_record_alloc.argtypes = []
lib.sdp_record_register.restype  = ctypes.c_int
lib.sdp_record_register.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint8]
lib.sdp_record_free.restype  = None
lib.sdp_record_free.argtypes = [ctypes.c_void_p]
lib.sdp_close.restype  = ctypes.c_int
lib.sdp_close.argtypes = [ctypes.c_void_p]

# --- High-level attribute builders ---
lib.sdp_list_append.restype  = ctypes.c_void_p
lib.sdp_list_append.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
lib.sdp_list_free.restype  = None
lib.sdp_list_free.argtypes = [ctypes.c_void_p, ctypes.c_void_p]

lib.sdp_set_service_id.restype  = None
lib.sdp_set_service_id.argtypes = [ctypes.c_void_p, ctypes.c_uint8 * 16]  # uuid_t

# sdp_uuid16_create(uuid_t*, uint16)
lib.sdp_uuid16_create.restype  = ctypes.c_void_p
lib.sdp_uuid16_create.argtypes = [ctypes.c_void_p, ctypes.c_uint16]

lib.sdp_set_service_classes.restype  = ctypes.c_int
lib.sdp_set_service_classes.argtypes = [ctypes.c_void_p, ctypes.c_void_p]

lib.sdp_set_browse_groups.restype  = ctypes.c_int
lib.sdp_set_browse_groups.argtypes = [ctypes.c_void_p, ctypes.c_void_p]

lib.sdp_set_access_protos.restype  = ctypes.c_int
lib.sdp_set_access_protos.argtypes = [ctypes.c_void_p, ctypes.c_void_p]

lib.sdp_set_add_access_protos.restype  = ctypes.c_int
lib.sdp_set_add_access_protos.argtypes = [ctypes.c_void_p, ctypes.c_void_p]

lib.sdp_set_profile_descs.restype  = ctypes.c_int
lib.sdp_set_profile_descs.argtypes = [ctypes.c_void_p, ctypes.c_void_p]

lib.sdp_set_info_attr.restype  = None
lib.sdp_set_info_attr.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p]

lib.sdp_attr_add_new.restype  = ctypes.c_int
lib.sdp_attr_add_new.argtypes = [ctypes.c_void_p, ctypes.c_uint16, ctypes.c_uint8, ctypes.c_void_p]

# uuid_t is 128 bits = 16 bytes
class uuid_t(ctypes.Structure):
    _fields_ = [("type", ctypes.c_uint8), ("value", ctypes.c_uint8 * 15)]

SDP_UUID16 = 0x16  # type for 16-bit UUID
SDP_UINT8  = 0x08
SDP_UINT16 = 0x09
SDP_BOOL   = 0x28
SDP_TEXT   = 0x25
SDP_SEQ8   = 0x35
SDP_SEQ16  = 0x36
SDP_ALT8   = 0x3D

def mk_uuid16(val):
    u = uuid_t()
    u.type = SDP_UUID16
    struct.pack_into(">H", u.value, 0, val)
    return ctypes.addressof(u), u  # return address and keep alive

SDP_RETRY_IF_BUSY = 0x01

# Connect to local SDP daemon
src = BDADDR_ANY
dst = BDADDR_LOCAL

session = lib.sdp_connect(ctypes.byref(src), ctypes.byref(dst), SDP_RETRY_IF_BUSY)
if not session:
    import ctypes as ct
    err = ctypes.get_errno()
    print(f"sdp_connect failed (errno={err})")
    sys.exit(1)
print(f"SDP session: 0x{session:x}")

# List available symbols for building the record
result = subprocess.run(['nm', '-D', '/usr/lib/x86_64-linux-gnu/libbluetooth.so.3'],
                       capture_output=True, text=True)
print("\nAvailable sdp builder functions:")
for line in result.stdout.split('\n'):
    if any(k in line for k in ['sdp_set', 'sdp_uuid', 'sdp_attr', 'sdp_data']):
        name = line.split()[-1] if line.split() else ''
        print(f"  {name}")
