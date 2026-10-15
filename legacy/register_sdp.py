#!/usr/bin/env python3
"""
Register HID SDP record via libbluetooth ctypes.
Must run as root.
"""
import ctypes, ctypes.util, struct, sys

lib = ctypes.CDLL("libbluetooth.so.3")

# --- bdaddr_t ---
class bdaddr_t(ctypes.Structure):
    _fields_ = [("b", ctypes.c_uint8 * 6)]

def str2ba(addr_str):
    ba = bdaddr_t()
    parts = [int(x, 16) for x in addr_str.split(":")]
    for i, v in enumerate(reversed(parts)):
        ba.b[i] = v
    return ba

BDADDR_ANY = bdaddr_t()

# --- HID descriptor: keyboard (ReportID 1) + mouse (ReportID 2) ---
KBD = bytes([
    0x05,0x01, 0x09,0x06, 0xA1,0x01, 0x85,0x01,
    0x05,0x07, 0x19,0xE0, 0x29,0xE7,
    0x15,0x00, 0x25,0x01, 0x75,0x01, 0x95,0x08, 0x81,0x02,
    0x95,0x01, 0x75,0x08, 0x81,0x01,
    0x95,0x06, 0x75,0x08, 0x15,0x00, 0x25,0x65,
    0x05,0x07, 0x19,0x00, 0x29,0x65, 0x81,0x00,
    0xC0,
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

def encode_sdp_element(type_desc, value_bytes):
    """Encode a raw SDP data element."""
    size_index = len(value_bytes)
    if size_index == 1:
        header = bytes([type_desc | 0x00])
    elif size_index == 2:
        header = bytes([type_desc | 0x01])
    elif size_index == 4:
        header = bytes([type_desc | 0x02])
    elif size_index <= 255:
        header = bytes([type_desc | 0x05, size_index])
    elif size_index <= 65535:
        header = bytes([type_desc | 0x06]) + struct.pack(">H", size_index)
    else:
        header = bytes([type_desc | 0x07]) + struct.pack(">I", size_index)
    return header + value_bytes

def uuid16(u): return bytes([0x19]) + struct.pack(">H", u)  # type=1(UUID), size=2
def uint8(v):  return bytes([0x08, v])
def uint16(v): return bytes([0x09]) + struct.pack(">H", v)
def uint32(v): return bytes([0x0A]) + struct.pack(">I", v)
def boolean(v): return bytes([0x28, 0x01 if v else 0x00])
def text(s):
    b = s.encode()
    return bytes([0x25, len(b)]) + b  # text, 1-byte length
def sequence(*items):
    body = b"".join(items)
    if len(body) <= 255:
        return bytes([0x35, len(body)]) + body
    return bytes([0x36]) + struct.pack(">H", len(body)) + body
def descriptor_seq(desc_bytes):
    # HIDDescriptorList: sequence containing one entry: [type=0x22, data=descriptor]
    inner = uint8(0x22) + bytes([0x25, len(desc_bytes)]) + desc_bytes
    return sequence(sequence(inner))

# Build attribute list
attrs = b""
def attr(id_, elem):
    return struct.pack(">BH", 0x09, id_) + elem  # attr_id tag + uint16

attrs += attr(0x0001, sequence(uuid16(0x1124)))  # ServiceClassIDList: HID
attrs += attr(0x0004, sequence(                  # ProtocolDescriptorList
    sequence(uuid16(0x0100), uint16(0x0011)),    #   L2CAP PSM=17
    sequence(uuid16(0x0011))                     #   HIDP
))
attrs += attr(0x0005, sequence(uuid16(0x1002)))  # BrowseGroupList
attrs += attr(0x000D, sequence(sequence(         # AdditionalProtocolDescriptorList
    sequence(uuid16(0x0100), uint16(0x0013)),    #   L2CAP PSM=19
    sequence(uuid16(0x0011))                     #   HIDP
)))
attrs += attr(0x0009, sequence(                  # BluetoothProfileDescriptorList
    sequence(uuid16(0x1124), uint16(0x0101))     #   HID v1.1
))
attrs += attr(0x0100, text("BT HID Relay"))
attrs += attr(0x0200, uint16(0x0111))            # HIDParserVersion
attrs += attr(0x0201, uint8(0xC0))               # HIDDeviceSubclass: kbd+mouse
attrs += attr(0x0202, uint8(0x21))               # HIDCountryCode: US
attrs += attr(0x0203, boolean(True))             # HIDVirtualCable
attrs += attr(0x0204, boolean(True))             # HIDReconnectInitiate
attrs += attr(0x0205, descriptor_seq(HID_DESC))  # HIDDescriptorList
attrs += attr(0x0206, sequence(sequence(uint16(0x0409), uint16(0x0100))))  # LANGIDBaseList
attrs += attr(0x0209, boolean(False))            # HIDBatteryPower
attrs += attr(0x020A, boolean(False))            # HIDRemoteWake
attrs += attr(0x020B, uint16(0x0100))            # HIDProfileVersion
attrs += attr(0x020C, uint16(0x0C80))            # HIDSupervisionTimeout: 3200
attrs += attr(0x020D, boolean(False))            # HIDNormallyConnectable
attrs += attr(0x020E, boolean(True))             # HIDBootDevice

# Wrap in outer sequence (SDP attribute list DES)
attr_list = sequence(attrs)

# --- Send to BlueZ SDP daemon via Unix socket ---
import socket as sock_mod

HANDLE_NONE = 0xFFFFFFFF
sdp_sock = sock_mod.socket(sock_mod.AF_UNIX, sock_mod.SOCK_STREAM)
sdp_sock.connect("/var/run/sdp")

# BlueZ SDP daemon protocol for local registration:
# PDU: [uint8 cmd=0x04] [uint32 handle=0xFFFFFFFF] [attr_list_bytes]
# cmd 0x04 = SDP_SVC_ADD_REQ (internal BlueZ)
pdu = bytes([0x04]) + struct.pack(">I", HANDLE_NONE) + attr_list
sdp_sock.send(pdu)

resp = sdp_sock.recv(64)
print(f"SDP response ({len(resp)} bytes): {resp.hex()}")
if len(resp) >= 5 and resp[0] == 0x05:  # SDP_SVC_ADD_RSP
    handle = struct.unpack(">I", resp[1:5])[0]
    print(f"SUCCESS: HID SDP record registered, handle=0x{handle:08x}")
else:
    print(f"Response bytes: {list(resp)}")
    # Try interpreting as error
    print("Unexpected response - check manually with: sudo sdptool browse local")

sdp_sock.close()
