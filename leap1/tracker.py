"""
LEAP MOTION 5-FINGER TRACKER (OPTIMIZED TRANSMISSION & FULL KINEMATICS)
========================================================================
- Tracks all 5 fingers (Thumb, Index, Middle, Ring, Pinky) + Palm for both hands.
- Captures full skeletal joints (Tip, PIP joint, Knuckle/MCP) and extension state.
- Computes precision pinch, power grab/fist, thumbs-up, point, and open-palm gestures.
- Uses high-speed binary protocol (LP5F) for ultra-low latency (<250 bytes/frame)
  with JSON support option.
- Target: UDP 127.0.0.1:5005
"""

import leap
import math
import socket
import struct
import json
import time

# ============================================================
# CONFIGURATION
# ============================================================

UDP_IP = "127.0.0.1"
UDP_PORT = 5005

# Protocol mode: "BINARY" (ultra-fast LP5F, ~350 bytes) or "JSON"
TRANSMISSION_PROTOCOL = "BINARY"

# Pinch detection thresholds (in mm)
PINCH_START_DIST = 38.0
PINCH_RELEASE_DIST = 52.0
PINCH_STRENGTH_START = 0.75
PINCH_STRENGTH_RELEASE = 0.35
FIST_START = 0.80
FIST_RELEASE = 0.60

# Power grab (fist) threshold
GRAB_STRENGTH_THRESH = 0.75

# Scale from Leap mm to meters for 3D engine
COORD_SCALE = 1.0 / 1000.0


# ============================================================
# MATH & CONVERSION HELPERS
# ============================================================

def dist_3d(a, b):
    """Euclidean distance in mm between two Leap Vector objects."""
    return math.sqrt(
        (a.x - b.x) ** 2 +
        (a.y - b.y) ** 2 +
        (a.z - b.z) ** 2
    )


def read_hand_strength(hand, name):
    """Read SDK gesture strength while tolerating SDK field differences."""
    value = getattr(hand, name, 0.0)
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


def convert_point_to_blender(v):
    """
    Converts Leap Motion coordinate (mm, Y-up) to Blender coordinate (m, Z-up):
      Blender X =  Leap X / 1000
      Blender Y = -Leap Z / 1000  (pushing away from user = +Y in Blender)
      Blender Z =  Leap Y / 1000  (height = +Z in Blender)
    """
    return (
        v.x * COORD_SCALE,
        -v.z * COORD_SCALE,
        v.y * COORD_SCALE
    )


def convert_quat_to_blender(q):
    """
    Converts Leap orientation quaternion (w, x, y, z) to Blender coordinate frame.
    """
    return (
        q.w,
        q.x,
        -q.z,
        q.y
    )


# ============================================================
# BINARY PROTOCOL PACKER (LP5F)
# ============================================================

MAGIC = b"LP5F"
PROTOCOL_VERSION = 1

# Flag bitmasks
FLAG_PINCH      = 1 << 0
FLAG_GRAB       = 1 << 1
FLAG_THUMBS_UP  = 1 << 2
FLAG_POINT      = 1 << 3
FLAG_OPEN_PALM  = 1 << 4
FLAG_FIST       = 1 << 5

def pack_hand_binary(hand_id_int, flags, pinch_str, pinch_dist, grab_str,
                     palm_pos, palm_rot, digits_data):
    """
    Packs a single hand's data into binary format:
      Hand header: hand_id (B), flags (B), pinch_str (f), pinch_dist (f), grab_str (f) -> 14 bytes
      Palm: pos (3f), rot (4f) -> 28 bytes
      5 Digits: each has is_ext (B), tip (3f), mcp (3f), pip (3f) -> 5 * 37 = 185 bytes
      Total per hand: 227 bytes
    """
    buf = bytearray()
    # Hand header
    buf.extend(struct.pack("<BBfff", hand_id_int, flags, pinch_str, pinch_dist, grab_str))
    # Palm position & orientation
    buf.extend(struct.pack("<3f4f", *palm_pos, *palm_rot))
    # 5 Digits: Thumb, Index, Middle, Ring, Pinky
    for d in digits_data:
        is_ext = 1 if d["extended"] else 0
        buf.extend(struct.pack("<B3f3f3f",
                               is_ext,
                               *d["tip"],
                               *d["mcp"],
                               *d["pip"]))
    return buf


def create_packet(hands_payload, timestamp):
    """
    Creates complete binary or JSON UDP packet.
    """
    if TRANSMISSION_PROTOCOL == "BINARY":
        # Header: magic(4s), version(B), hand_count(B), timestamp(d) -> 14 bytes
        header = struct.pack("<4sBBd", MAGIC, PROTOCOL_VERSION, len(hands_payload), timestamp)
        packet = bytearray(header)
        for h in hands_payload:
            packet.extend(h["binary_blob"])
        return bytes(packet)
    else:
        # JSON fallback
        out = {
            "protocol": "LP5F_JSON",
            "version": PROTOCOL_VERSION,
            "timestamp": timestamp,
            "hands": {}
        }
        for h in hands_payload:
            name = "Left" if h["hand_id"] == 0 else "Right"
            out["hands"][name] = {
                "pinch": bool(h["flags"] & FLAG_PINCH),
                "grab": bool(h["flags"] & FLAG_GRAB),
                "fist": bool(h["flags"] & FLAG_FIST),
                "thumbs_up": bool(h["flags"] & FLAG_THUMBS_UP),
                "point": bool(h["flags"] & FLAG_POINT),
                "open_palm": bool(h["flags"] & FLAG_OPEN_PALM),
                "pinch_strength": h["pinch_str"],
                "pinch_distance": h["pinch_dist"],
                "grab_strength": h["grab_str"],
                "palm": {
                    "position": {"x": h["palm_pos"][0], "y": h["palm_pos"][1], "z": h["palm_pos"][2]},
                    "rotation": {"w": h["palm_rot"][0], "x": h["palm_rot"][1], "y": h["palm_rot"][2], "z": h["palm_rot"][3]}
                },
                "index": {"x": h["digits"][1]["tip"][0], "y": h["digits"][1]["tip"][1], "z": h["digits"][1]["tip"][2]},
                "thumb": {"x": h["digits"][0]["tip"][0], "y": h["digits"][0]["tip"][1], "z": h["digits"][0]["tip"][2]},
                "digits": {
                    "thumb":  {"tip": h["digits"][0]["tip"], "extended": h["digits"][0]["extended"]},
                    "index":  {"tip": h["digits"][1]["tip"], "extended": h["digits"][1]["extended"]},
                    "middle": {"tip": h["digits"][2]["tip"], "extended": h["digits"][2]["extended"]},
                    "ring":   {"tip": h["digits"][3]["tip"], "extended": h["digits"][3]["extended"]},
                    "pinky":  {"tip": h["digits"][4]["tip"], "extended": h["digits"][4]["extended"]},
                }
            }
        return json.dumps(out).encode("utf-8")


# ============================================================
# LISTENER IMPLEMENTATION
# ============================================================

class UltraLeapListener(leap.Listener):

    def __init__(self, sock):
        super().__init__()
        self.sock = sock

        self.pinching = {
            "Left": False,
            "Right": False
        }

        self.fisting = {
            "Left": False,
            "Right": False
        }

        self.hand_seen = {
            "Left": False,
            "Right": False
        }

        self.packet_count = 0
        self.last_stat_time = time.perf_counter()
        self.stat_packets = 0

    def on_tracking_event(self, event):
        now = time.perf_counter()
        hands_payload = []
        seen_names = set()

        for hand in event.hands:
            # Determine Hand Identity
            hand_type_str = str(hand.type)
            if "Left" in hand_type_str:
                hand_name = "Left"
                hand_id_int = 0
            elif "Right" in hand_type_str:
                hand_name = "Right"
                hand_id_int = 1
            else:
                continue

            seen_names.add(hand_name)

            if not self.hand_seen[hand_name]:
                self.hand_seen[hand_name] = True
                print(f">>> {hand_name.upper()} HAND DETECTED")

            # --------------------------------------------------------
            # 1. PALM TRACKING & POSE
            # --------------------------------------------------------
            palm_pos = convert_point_to_blender(hand.palm.position)
            palm_rot = convert_quat_to_blender(hand.palm.orientation)

            # --------------------------------------------------------
            # 2. ALL 5 DIGITS SKELETON
            # --------------------------------------------------------
            digits_list = [
                hand.thumb,
                hand.index,
                hand.middle,
                hand.ring,
                hand.pinky
            ]

            digits_data = []
            for digit in digits_list:
                tip = convert_point_to_blender(digit.distal.next_joint)
                pip = convert_point_to_blender(digit.proximal.next_joint)
                mcp = convert_point_to_blender(digit.metacarpal.next_joint)
                extended = bool(digit.is_extended)

                digits_data.append({
                    "tip": tip,
                    "pip": pip,
                    "mcp": mcp,
                    "extended": extended
                })

            # --------------------------------------------------------
            # 3. GESTURE RECOGNITION & PINCH HYSTERESIS
            # --------------------------------------------------------
            thumb_tip = hand.thumb.distal.next_joint
            index_tip = hand.index.distal.next_joint
            pinch_distance = dist_3d(thumb_tip, index_tip)

            # Read SDK strengths if available (fallback to 0)
            pinch_strength = read_hand_strength(hand, "pinch_strength")
            grab_strength = read_hand_strength(hand, "grab_strength")

            # Robust Pinch Hysteresis
            is_pinching = self.pinching[hand_name]
            if not is_pinching:
                if pinch_distance <= PINCH_START_DIST or pinch_strength >= PINCH_STRENGTH_START:
                    is_pinching = True
                    self.pinching[hand_name] = True
                    print(f">>> {hand_name} PINCH START ({pinch_distance:.1f} mm | str: {pinch_strength:.2f})")
            else:
                if pinch_distance >= PINCH_RELEASE_DIST and pinch_strength <= PINCH_STRENGTH_RELEASE:
                    is_pinching = False
                    self.pinching[hand_name] = False
                    print(f">>> {hand_name} PINCH RELEASE ({pinch_distance:.1f} mm | str: {pinch_strength:.2f})")

            # Power Grab (Fist)
            is_fist = (grab_strength >= GRAB_STRENGTH_THRESH) and not digits_data[1]["extended"] and not digits_data[2]["extended"]

            # Independent fist clutch state with hysteresis
            is_fisting = self.fisting[hand_name]
            if not is_fisting and grab_strength >= FIST_START:
                is_fisting = True
                self.fisting[hand_name] = True
            elif is_fisting and grab_strength <= FIST_RELEASE:
                is_fisting = False
                self.fisting[hand_name] = False

            # Thumbs Up (Thumb extended up, all other fingers curled)
            is_thumbs_up = (
                digits_data[0]["extended"] and
                not digits_data[1]["extended"] and
                not digits_data[2]["extended"] and
                not digits_data[3]["extended"] and
                not digits_data[4]["extended"] and
                (hand.thumb.distal.next_joint.y > hand.palm.position.y + 25.0)
            )

            # Pointing Gesture (Index extended, middle/ring/pinky curled)
            is_point = (
                digits_data[1]["extended"] and
                not digits_data[2]["extended"] and
                not digits_data[3]["extended"] and
                not digits_data[4]["extended"]
            )

            # Open Palm (All extended, relaxed)
            is_open_palm = all(d["extended"] for d in digits_data) and grab_strength < 0.2

            # Build Flags Bitfield
            flags = 0
            if is_pinching:  flags |= FLAG_PINCH
            if is_fist:      flags |= FLAG_GRAB
            if is_thumbs_up: flags |= FLAG_THUMBS_UP
            if is_point:     flags |= FLAG_POINT
            if is_open_palm: flags |= FLAG_OPEN_PALM
            if is_fisting:   flags |= FLAG_FIST

            # Pack Hand Binary Blob
            binary_blob = pack_hand_binary(
                hand_id_int,
                flags,
                pinch_strength,
                pinch_distance,
                grab_strength,
                palm_pos,
                palm_rot,
                digits_data
            )

            hands_payload.append({
                "hand_id": hand_id_int,
                "flags": flags,
                "pinch_str": pinch_strength,
                "pinch_dist": pinch_distance,
                "grab_str": grab_strength,
                "palm_pos": palm_pos,
                "palm_rot": palm_rot,
                "digits": digits_data,
                "binary_blob": binary_blob
            })

        for hand_name in ("Left", "Right"):
            if hand_name not in seen_names:
                self.pinching[hand_name] = False
                self.fisting[hand_name] = False

        # --------------------------------------------------------
        # 4. SEND HIGH-SPEED UDP PACKET
        # --------------------------------------------------------
        if hands_payload:
            packet = create_packet(hands_payload, now)
            try:
                self.sock.sendto(packet, (UDP_IP, UDP_PORT))
            except Exception as e:
                print(f"[UDP SEND ERROR]: {e}")

        # Diagnostics & FPS report
        self.packet_count += 1
        self.stat_packets += 1
        dt = now - self.last_stat_time
        if dt >= 2.0:
            fps = self.stat_packets / dt
            hands_str = ", ".join(f"{'Left' if h['hand_id']==0 else 'Right'}{'(PINCH)' if h['flags'] & FLAG_PINCH else ''}{'(FIST)' if h['flags'] & FLAG_GRAB else ''}" for h in hands_payload) or "No hands"
            print(f"[LEAP 5-FINGER] Rate: {fps:.1f} packets/sec | Mode: {TRANSMISSION_PROTOCOL} | Hands: [{hands_str}]")
            self.last_stat_time = now
            self.stat_packets = 0


# ============================================================
# MAIN ENTRY POINT
# ============================================================

def main():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    connection = leap.Connection()
    listener = UltraLeapListener(sock)
    connection.add_listener(listener)

    print("=" * 60)
    print("LEAP MOTION ULTRA 5-FINGER TRACKER (FULL SKELETON + GESTURES)")
    print("=" * 60)
    print(f"Target UDP:     {UDP_IP}:{UDP_PORT}")
    print(f"Protocol:       {TRANSMISSION_PROTOCOL} (LP5F Binary High-Speed)")
    print("Tracking:       All 5 Digits (Thumb, Index, Middle, Ring, Pinky)")
    print("                + Palm Pose & Knuckle/PIP Joints")
    print("Gestures:       Pinch (Grab/Move/Scale), Power Fist, Thumbs-Up, Point")
    print("=" * 60)
    print("Connecting to Ultraleap Tracking Service...")

    with connection.open():
        connection.set_tracking_mode(leap.TrackingMode.Desktop)
        print("Ultraleap Connection Active! Move your hands over the sensor...")
        print("Press Ctrl+C to terminate.")

        try:
            while True:
                time.sleep(0.001)
        except KeyboardInterrupt:
            print("\nTracker stopped by user.")
        finally:
            sock.close()


if __name__ == "__main__":
    main()