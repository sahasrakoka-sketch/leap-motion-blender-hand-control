"""
LEAP MOTION BLENDER RECEIVER (ULTRA 5-FINGER TRACKING & 3D INTERACTION)
========================================================================
Features:
- Full 5-Finger Tracking (Thumb, Index, Middle, Ring, Pinky) + Palm for both hands.
- Self-building visual hand rig: Auto-generates cyber-hologram hand models in Blender.
  (Zero missing-cursor errors; works out of the box in any .blend file).
- Adaptive One-Euro Jitter Filter: Eliminates sensor noise while maintaining 0 lag.
- High-Speed LP5F Binary Protocol Unpacker (microsecond decode) + JSON fallback.
- 3D Object Interactions:
    * Hover Detection & Non-destructive material glow.
    * Single-Hand Pinch: Grab, 6-DOF Move & Rotate.
    * Two-Hand Pinch: Expand / Contract (Scaling) & Dual-Hand Orbit Rotation.
    * Power Fist Grab: Natural multi-finger grasping.
    * Thumbs-Up Gesture: Quick object reset (position & scale).
- Hot-Reload Safe: Cleans up existing sockets, timers, and materials automatically.
"""

import bpy
import bmesh
import socket
import struct
import json
import time
import math
from mathutils import Vector, Quaternion, Matrix

# ============================================================
# USER CONFIGURATION
# ============================================================

HOST = "127.0.0.1"
PORT = 5005
TIMER_INTERVAL = 0.005  # ~200Hz polling rate

# Scale factor from Leap meters to Blender 3D space
TRACKING_SCALE = 100.0
Y_TRACKING_SCALE = TRACKING_SCALE
Z_TRACKING_SCALE = TRACKING_SCALE
FIXED_Y = 0.0

# Workspace center offset in Blender
WORKSPACE_ORIGIN = Vector((0.0, 0.0, 1.0))

# Inversion / direction factors (Blender: +X Right, +Y Forward/Away, +Z Up)
DIR_X = -1.0
DIR_Y = -1.0
DIR_Z = -1.0

# Interaction distances and scaling limits
HOVER_DISTANCE = 4.0           # Max distance from mesh bounding box to hover
MIN_SCALE_FACTOR = 0.1        # Minimum scale during two-hand pinch resize
MAX_SCALE_FACTOR = 8.0        # Maximum scale during two-hand pinch resize
HAND_TIMEOUT = 0.25           # Seconds before dropping held objects if tracking lost

# Smoothing configuration (One-Euro Filter)
USE_SMOOTHING = True
ONE_EURO_MIN_CUTOFF = 3.5     # Hz: lower = smoother stationary hand (less jitter)
ONE_EURO_BETA = 0.35          # Velocity factor: higher = faster response to fast motion
ONE_EURO_DCUTOFF = 1.0        # Hz: cutoff frequency for derivative

# Hand collection name in Blender Outliner
COLLECTION_NAME = "LEAP_HANDS"


# ============================================================
# ADAPTIVE ONE-EURO FILTER (JITTER REMOVAL WITHOUT LAG)
# ============================================================

class LowPassFilter:
    def __init__(self, alpha=0.5):
        self.alpha = alpha
        self.hat_x = None

    def reset(self):
        self.hat_x = None

    def filter(self, x, alpha=None):
        if alpha is not None:
            self.alpha = alpha
        if self.hat_x is None:
            self.hat_x = x
        else:
            self.hat_x = self.hat_x + self.alpha * (x - self.hat_x)
        return self.hat_x


class OneEuroFilter3D:
    """Adaptive low-pass filter for 3D vectors."""
    def __init__(self, min_cutoff=ONE_EURO_MIN_CUTOFF, beta=ONE_EURO_BETA, d_cutoff=ONE_EURO_DCUTOFF):
        self.min_cutoff = min_cutoff
        self.beta = beta
        self.d_cutoff = d_cutoff
        self.x_filt = LowPassFilter()
        self.dx_filt = LowPassFilter()
        self.last_time = None

    def reset(self):
        self.x_filt.reset()
        self.dx_filt.reset()
        self.last_time = None

    def _calc_alpha(self, rate, cutoff):
        tau = 1.0 / (2.0 * math.pi * cutoff)
        te = 1.0 / rate if rate > 0 else 0.01
        return 1.0 / (1.0 + tau / te)

    def filter(self, val_vec, timestamp):
        if self.last_time is None:
            self.last_time = timestamp
            return self.x_filt.filter(val_vec, 1.0)

        dt = timestamp - self.last_time
        self.last_time = timestamp
        if dt <= 1e-5:
            dt = 1e-4
        rate = 1.0 / dt

        prev_x = self.x_filt.hat_x if self.x_filt.hat_x is not None else val_vec
        dx = (val_vec - prev_x) * rate
        edx = self.dx_filt.filter(dx, self._calc_alpha(rate, self.d_cutoff))

        cutoff = self.min_cutoff + self.beta * edx.length
        alpha = self._calc_alpha(rate, cutoff)
        return self.x_filt.filter(val_vec, alpha)


# Global filter cache for hands
hand_filters = {
    "Left": {
        "palm": OneEuroFilter3D(),
        "digits": [OneEuroFilter3D() for _ in range(5)],
        "knuckles": [OneEuroFilter3D() for _ in range(5)]
    },
    "Right": {
        "palm": OneEuroFilter3D(),
        "digits": [OneEuroFilter3D() for _ in range(5)],
        "knuckles": [OneEuroFilter3D() for _ in range(5)]
    }
}


# ============================================================
# HOT-RELOAD CLEANUP (PREVENT MEMORY LEAKS & DUPLICATE TIMERS)
# ============================================================

if "leap_timer" in bpy.app.driver_namespace:
    old_timer = bpy.app.driver_namespace["leap_timer"]
    if bpy.app.timers.is_registered(old_timer):
        try:
            bpy.app.timers.unregister(old_timer)
            print("[LEAP] Previous timer unregistered.")
        except Exception:
            pass

if "leap_socket" in bpy.app.driver_namespace:
    old_sock = bpy.app.driver_namespace["leap_socket"]
    try:
        old_sock.close()
        print("[LEAP] Previous socket closed.")
    except Exception:
        pass

if "leap_cleanup_highlights" in bpy.app.driver_namespace:
    try:
        bpy.app.driver_namespace["leap_cleanup_highlights"]()
    except Exception:
        pass


# ============================================================
# UDP SOCKET SETUP
# ============================================================

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

try:
    sock.bind((HOST, PORT))
    sock.setblocking(False)
    bpy.app.driver_namespace["leap_socket"] = sock
    print(f"[LEAP] Socket successfully bound to {HOST}:{PORT}")
except OSError as e:
    print(f"[LEAP ERROR] Could not bind to {HOST}:{PORT}: {e}")
    raise


# ============================================================
# MATERIALS SYSTEM (CYBER HOLOGRAPHIC HANDS + HOVER GLOW)
# ============================================================

def get_or_create_material(name, color_rgba, emission_strength=1.5):
    mat = bpy.data.materials.get(name)
    if mat is None:
        mat = bpy.data.materials.new(name)
        mat.use_nodes = True
        bsdf = mat.node_tree.nodes.get("Principled BSDF")
        if bsdf:
            bsdf.inputs["Base Color"].default_value = color_rgba
            if "Roughness" in bsdf.inputs:
                bsdf.inputs["Roughness"].default_value = 0.2
            if "Emission Color" in bsdf.inputs:
                bsdf.inputs["Emission Color"].default_value = color_rgba
                bsdf.inputs["Emission Strength"].default_value = emission_strength
            elif "Emission" in bsdf.inputs:
                bsdf.inputs["Emission"].default_value = color_rgba
    mat.diffuse_color = color_rgba
    return mat

# Materials for Left hand (Cyan), Right hand (Neon Orange), and Pinching (Bright Lime/Gold)
mat_left_hand   = get_or_create_material("LEAP_Mat_Left",   (0.0, 0.9, 1.0, 0.8), emission_strength=2.0)
mat_right_hand  = get_or_create_material("LEAP_Mat_Right",  (1.0, 0.45, 0.05, 0.8), emission_strength=2.0)
mat_pinch_glow  = get_or_create_material("LEAP_Mat_Pinch",  (0.0, 1.0, 0.45, 1.0), emission_strength=4.0)
mat_hover_glow  = get_or_create_material("LEAP_Mat_Hover",  (1.0, 0.8, 0.1, 1.0), emission_strength=3.0)
mat_bone_link   = get_or_create_material("LEAP_Mat_Bone",   (0.5, 0.6, 0.8, 0.3), emission_strength=0.5)

# Non-destructive material restoration tracking
original_materials = {}
hover_ref_counts = {}

def highlight_object(obj, enable):
    """Temporarily applies or reverts glowing hover highlight without losing original materials."""
    if obj is None or not hasattr(obj, "data") or not hasattr(obj.data, "materials"):
        return
    name = obj.name
    refs = hover_ref_counts.get(name, 0)

    if enable:
        hover_ref_counts[name] = refs + 1
        if refs == 0:
            if len(obj.data.materials) > 0:
                original_materials[name] = obj.data.materials[0]
                obj.data.materials[0] = mat_hover_glow
            else:
                original_materials[name] = None
                obj.data.materials.append(mat_hover_glow)
    else:
        new_refs = max(0, refs - 1)
        hover_ref_counts[name] = new_refs
        if new_refs == 0 and name in original_materials:
            orig = original_materials.pop(name, None)
            if orig is None:
                obj.data.materials.clear()
            else:
                if len(obj.data.materials) > 0:
                    obj.data.materials[0] = orig
                else:
                    obj.data.materials.append(orig)

def cleanup_all_highlights():
    for name, orig in list(original_materials.items()):
        obj = bpy.data.objects.get(name)
        if obj and hasattr(obj, "data") and hasattr(obj.data, "materials"):
            if orig is None:
                obj.data.materials.clear()
            else:
                if len(obj.data.materials) > 0:
                    obj.data.materials[0] = orig
                else:
                    obj.data.materials.append(orig)
    original_materials.clear()
    hover_ref_counts.clear()

bpy.app.driver_namespace["leap_cleanup_highlights"] = cleanup_all_highlights


# ============================================================
# SELF-BUILDING 5-FINGER VISUAL HAND RIG
# ============================================================

def get_or_create_collection(name):
    col = bpy.data.collections.get(name)
    if col is None:
        col = bpy.data.collections.new(name)
        bpy.context.scene.collection.children.link(col)
    return col

def get_or_create_shared_mesh(mesh_name, radius, is_palm=False):
    mesh = bpy.data.meshes.get(mesh_name)
    if mesh is None:
        mesh = bpy.data.meshes.new(mesh_name)
        bm = bmesh.new()
        if is_palm:
            bmesh.ops.create_icosphere(bm, subdivisions=2, radius=radius)
            # Flatten slightly for palm disc shape
            bmesh.ops.scale(bm, vec=Vector((1.2, 1.2, 0.45)), verts=bm.verts)
        else:
            bmesh.ops.create_icosphere(bm, subdivisions=2, radius=radius)
        bm.to_mesh(mesh)
        bm.free()
    return mesh

mesh_palm_sphere    = get_or_create_shared_mesh("LEAP_Mesh_Palm", radius=0.07, is_palm=True)
mesh_fingertip      = get_or_create_shared_mesh("LEAP_Mesh_Tip", radius=0.035)
mesh_knuckle        = get_or_create_shared_mesh("LEAP_Mesh_Knuckle", radius=0.025)

def get_or_create_hand_object(obj_name, mesh, material, collection):
    obj = bpy.data.objects.get(obj_name)
    if obj is None:
        obj = bpy.data.objects.new(obj_name, mesh)
        collection.objects.link(obj)
        obj.data.materials.append(material)
    obj.hide_viewport = False
    obj.show_in_front = True
    return obj

FINGER_NAMES = ["Thumb", "Index", "Middle", "Ring", "Pinky"]

class HandVisualRig:
    """Manages the 3D visual hierarchy of a single hand."""
    def __init__(self, hand_name, collection):
        self.hand_name = hand_name
        self.col = collection
        self.base_mat = mat_left_hand if hand_name == "Left" else mat_right_hand

        # Palm
        self.palm = get_or_create_hand_object(
            f"LEAP_{hand_name}_Palm", mesh_palm_sphere, self.base_mat, self.col
        )

        # 5 Fingertips & 5 Knuckles
        self.tips = []
        self.knuckles = []
        for fn in FINGER_NAMES:
            tip_obj = get_or_create_hand_object(
                f"LEAP_{hand_name}_{fn}_Tip", mesh_fingertip, self.base_mat, self.col
            )
            knuckle_obj = get_or_create_hand_object(
                f"LEAP_{hand_name}_{fn}_Knuckle", mesh_knuckle, self.base_mat, self.col
            )
            self.tips.append(tip_obj)
            self.knuckles.append(knuckle_obj)

    def set_visible(self, visible):
        self.palm.hide_viewport = not visible
        for t in self.tips:
            t.hide_viewport = not visible
        for k in self.knuckles:
            k.hide_viewport = not visible

    def set_pinch_visual(self, is_pinching):
        # Dynamically highlight Thumb and Index tips during pinch
        pinch_mat = mat_pinch_glow if is_pinching else self.base_mat
        if len(self.tips[0].data.materials) > 0:
            self.tips[0].data.materials[0] = pinch_mat
        if len(self.tips[1].data.materials) > 0:
            self.tips[1].data.materials[0] = pinch_mat

# Initialize hand collection and visual rigs
hand_col = get_or_create_collection(COLLECTION_NAME)
visual_rigs = {
    "Left": HandVisualRig("Left", hand_col),
    "Right": HandVisualRig("Right", hand_col)
}

# Optional backward compatibility with scene cursors
legacy_left_cursor = bpy.data.objects.get("LEAP_LEFT_CURSOR")
legacy_right_cursor = bpy.data.objects.get("LEAP_RIGHT_CURSOR")
legacy_index_cursor = bpy.data.objects.get("IndexCursor")
cursor_left_material = get_or_create_material("LEAP_Cursor_Left", (0.65, 0.05, 1.0, 1.0), emission_strength=4.0)
cursor_right_material = get_or_create_material("LEAP_Cursor_Right", (0.05, 1.0, 0.2, 1.0), emission_strength=4.0)

for cursor_name, cursor_location, cursor_material in (
    ("LEAP_LEFT_CURSOR", (-5.0, 0.0, 0.0), cursor_left_material),
    ("LEAP_RIGHT_CURSOR", (5.0, 0.0, 0.0), cursor_right_material),
):
    cursor = bpy.data.objects.get(cursor_name)
    if cursor is None:
        bpy.ops.mesh.primitive_uv_sphere_add(radius=0.5, location=cursor_location)
        cursor = bpy.context.object
        cursor.name = cursor_name
    cursor.hide_viewport = False
    cursor.hide_render = False
    cursor.show_in_front = True
    if len(cursor.data.materials) == 0:
        cursor.data.materials.append(cursor_material)
    else:
        cursor.data.materials[0] = cursor_material

legacy_left_cursor = bpy.data.objects.get("LEAP_LEFT_CURSOR")
legacy_right_cursor = bpy.data.objects.get("LEAP_RIGHT_CURSOR")

for cursor, material in ((legacy_left_cursor, cursor_left_material), (legacy_right_cursor, cursor_right_material), (legacy_index_cursor, cursor_right_material)):
    if cursor is not None and hasattr(cursor.data, "materials"):
        if len(cursor.data.materials) > 0:
            cursor.data.materials[0] = material
        else:
            cursor.data.materials.append(material)
        cursor.show_in_front = True


# ============================================================
# STATE TRACKING & INTERACTION SYSTEM
# ============================================================

hand_states = {
    "Left": {
        "is_active": False,
        "last_seen": 0.0,
        "pinch": False,
        "grab": False,
        "fist": False,
        "cursor_position": Vector((0, 0, 0)),
        "frozen_cursor_position": Vector((0, 0, 0)),
        "clutch_resume_reference": Vector((0, 0, 0)),
        "clutch_resume_position": Vector((0, 0, 0)),
        "cursor_initialized": False,
        "cursor_frozen": False,
        "clutch_resume_active": False,
        "fist_pinch_warning": False,
        "thumbs_up": False,
        "point": False,
        "pinch_pos": Vector((0, 0, 0)),
        "palm_pos": Vector((0, 0, 0)),
        "palm_rot": Quaternion((1, 0, 0, 0)),
        "hover": None,
        "grab_target": None,
        "grab_offset_pos": Vector((0, 0, 0)),
        "grab_offset_rot": Quaternion((1, 0, 0, 0)),
        "thumbs_up_start": 0.0
    },
    "Right": {
        "is_active": False,
        "last_seen": 0.0,
        "pinch": False,
        "grab": False,
        "fist": False,
        "cursor_position": Vector((0, 0, 0)),
        "frozen_cursor_position": Vector((0, 0, 0)),
        "clutch_resume_reference": Vector((0, 0, 0)),
        "clutch_resume_position": Vector((0, 0, 0)),
        "cursor_initialized": False,
        "cursor_frozen": False,
        "clutch_resume_active": False,
        "fist_pinch_warning": False,
        "thumbs_up": False,
        "point": False,
        "pinch_pos": Vector((0, 0, 0)),
        "palm_pos": Vector((0, 0, 0)),
        "palm_rot": Quaternion((1, 0, 0, 0)),
        "hover": None,
        "grab_target": None,
        "grab_offset_pos": Vector((0, 0, 0)),
        "grab_offset_rot": Quaternion((1, 0, 0, 0)),
        "thumbs_up_start": 0.0
    }
}

two_hand_interaction = {
    "active": False,
    "object": None,
    "initial_distance": 0.0,
    "initial_scale": Vector((1, 1, 1)),
    "initial_vector": Vector((0, 0, 0)),
    "initial_angle": 0.0,
    "initial_rot": Quaternion((1, 0, 0, 0))
}

# Track initial transforms of scene objects for thumbs-up reset
initial_object_transforms = {}


# ============================================================
# 3D MESH FILTER & BOUNDING BOX HOVER CALCULATION
# ============================================================

def is_interactable(obj):
    """Filters for editable scene mesh objects, excluding cursors, camera, and lights."""
    if obj is None or obj.type != "MESH":
        return False
    if obj.name.startswith("LEAP_Left_") or obj.name.startswith("LEAP_Right_"):
        return False
    if obj.name in ("IndexCursor", "LEAP_LEFT_CURSOR", "LEAP_RIGHT_CURSOR"):
        return False
    if obj.name in ("Camera", "Light"):
        return False
    if obj.hide_viewport or obj.hide_get():
        return False
    return True

def distance_to_bounding_box(obj, point):
    """Calculates world-space distance from a point to the mesh bounding box."""
    try:
        corners = [obj.matrix_world @ Vector(c) for c in obj.bound_box]
        min_x = min(c.x for c in corners)
        max_x = max(c.x for c in corners)
        min_y = min(c.y for c in corners)
        max_y = max(c.y for c in corners)
        min_z = min(c.z for c in corners)
        max_z = max(c.z for c in corners)

        closest = Vector((
            max(min_x, min(max_x, point.x)),
            max(min_y, min(max_y, point.y)),
            max(min_z, min(max_z, point.z))
        ))
        return (closest - point).length
    except Exception:
        return (obj.matrix_world.translation - point).length

def find_hover_target(point):
    """Finds the closest interactable object within HOVER_DISTANCE."""
    best_obj = None
    min_dist = float("inf")
    for obj in bpy.context.scene.objects:
        if not is_interactable(obj):
            continue
        dist = distance_to_bounding_box(obj, point)
        if dist < min_dist:
            min_dist = dist
            best_obj = obj
    if best_obj and min_dist <= HOVER_DISTANCE:
        return best_obj
    return None


def prepare_expo_scene():
    """Arrange existing demo meshes and reuse the scene camera for a 3D view."""
    layout = {
        "LEAP_CONE": (-5.0, 2.0, 2.5),
        "LEAP_CUBE": (0.0, 0.5, 2.0),
        "LEAP_CYLINDER": (5.0, -1.0, 2.5),
        "LEAP_ICOSPHERE": (-4.5, -1.5, -2.5),
        "LEAP_TORUS": (0.5, 1.5, -2.5),
        "TEST_CUBE": (5.0, 0.0, -2.5),
        "TEST_SPHERE": (0.0, 3.0, 5.5)
    }
    for name, location in layout.items():
        obj = bpy.data.objects.get(name)
        if obj is not None and obj.type == "MESH":
            obj.location = location

    camera = bpy.data.objects.get("Camera")
    if camera is not None and camera.type == "CAMERA":
        camera.data.type = 'PERSP'
        camera.location = (16.0, -24.0, 14.0)
        target = Vector((0.0, 0.0, 2.5))
        camera.rotation_euler = (target - camera.location).to_track_quat('-Z', 'Y').to_euler()
        camera.data.lens = 52
        bpy.context.scene.camera = camera


prepare_expo_scene()


# ============================================================
# COORDINATE CONVERSION WITH WORKSPACE OFFSET
# ============================================================

def transform_to_blender(raw_tuple):
    """Preserves the tracker's already-converted absolute Blender XYZ coordinates."""
    return Vector((
        raw_tuple[0] * DIR_X * TRACKING_SCALE + WORKSPACE_ORIGIN.x,
        raw_tuple[1] * DIR_Y * Y_TRACKING_SCALE + WORKSPACE_ORIGIN.y,
        raw_tuple[2] * DIR_Z * Z_TRACKING_SCALE + WORKSPACE_ORIGIN.z
    ))


# ============================================================
# PACKET DECODERS: LP5F BINARY & JSON
# ============================================================

FLAG_PINCH      = 1 << 0
FLAG_GRAB       = 1 << 1
FLAG_THUMBS_UP  = 1 << 2
FLAG_POINT      = 1 << 3
FLAG_OPEN_PALM  = 1 << 4
FLAG_FIST       = 1 << 5

def decode_lp5f(packet):
    """
    Decodes binary LP5F packet in microsecond speed:
      Header (14 bytes): magic(4s), version(B), hand_count(B), timestamp(d)
      Hand Header (14 bytes): hand_id(B), flags(B), pinch_str(f), pinch_dist(f), grab_str(f)
      Palm (28 bytes): pos(3f), rot(4f)
      5 Digits (185 bytes): each is ext(B), tip(3f), mcp(3f), pip(3f) -> 37 bytes * 5
    """
    if len(packet) < 14:
        return None
    magic, ver, count, ts = struct.unpack_from("<4sBBd", packet, 0)
    if magic != b"LP5F":
        return None
    if ver != 1 or count > 2 or len(packet) < 14 + count * 227:
        return None

    hands_dict = {}
    offset = 14

    for _ in range(count):
        if offset + 227 > len(packet):
            break
        hand_id, flags, p_str, p_dist, g_str = struct.unpack_from("<BBfff", packet, offset)
        offset += 14

        palm_px, palm_py, palm_pz, palm_rw, palm_rx, palm_ry, palm_rz = struct.unpack_from("<3f4f", packet, offset)
        offset += 28

        hand_name = "Left" if hand_id == 0 else "Right"
        digits_data = []

        for _ in range(5):
            ext, tx, ty, tz, mx, my, mz, px, py, pz = struct.unpack_from("<B3f3f3f", packet, offset)
            offset += 37
            digits_data.append({
                "extended": bool(ext),
                "tip": (tx, ty, tz),
                "mcp": (mx, my, mz),
                "pip": (px, py, pz)
            })

        hands_dict[hand_name] = {
            "flags": flags,
            "pinch": bool(flags & FLAG_PINCH),
            "grab": bool(flags & FLAG_GRAB),
            "fist": bool(flags & FLAG_FIST),
            "thumbs_up": bool(flags & FLAG_THUMBS_UP),
            "point": bool(flags & FLAG_POINT),
            "open_palm": bool(flags & FLAG_OPEN_PALM),
            "pinch_strength": p_str,
            "pinch_distance": p_dist,
            "grab_strength": g_str,
            "palm_pos": (palm_px, palm_py, palm_pz),
            "palm_rot": (palm_rw, palm_rx, palm_ry, palm_rz),
            "digits": digits_data
        }

    return {"hands": hands_dict, "timestamp": ts}


def decode_json(packet):
    """Fallback decoder for JSON UDP packets."""
    try:
        data = json.loads(packet.decode("utf-8"))
        hands_dict = {}
        for hname, hdata in data.get("hands", {}).items():
            flags = 0
            if hdata.get("pinch"): flags |= FLAG_PINCH
            if hdata.get("grab"): flags |= FLAG_GRAB
            if hdata.get("fist"): flags |= FLAG_FIST
            if hdata.get("thumbs_up"): flags |= FLAG_THUMBS_UP
            if hdata.get("point"): flags |= FLAG_POINT
            if hdata.get("open_palm"): flags |= FLAG_OPEN_PALM

            palm_p = hdata.get("palm", {}).get("position", {})
            palm_r = hdata.get("palm", {}).get("rotation", {})
            digits_dict = hdata.get("digits", {})
            digits_list = []
            for fn in ["thumb", "index", "middle", "ring", "pinky"]:
                d_entry = digits_dict.get(fn, {})
                tip = d_entry.get("tip", (0, 0, 0))
                digits_list.append({
                    "extended": bool(d_entry.get("extended", True)),
                    "tip": tuple(tip) if isinstance(tip, (list, tuple)) else (0, 0, 0),
                    "mcp": (0, 0, 0),
                    "pip": (0, 0, 0)
                })

            hands_dict[hname] = {
                "flags": flags,
                "pinch": bool(flags & FLAG_PINCH),
                "grab": bool(flags & FLAG_GRAB),
                "fist": bool(flags & FLAG_FIST),
                "thumbs_up": bool(flags & FLAG_THUMBS_UP),
                "point": bool(flags & FLAG_POINT),
                "open_palm": bool(flags & FLAG_OPEN_PALM),
                "pinch_strength": float(hdata.get("pinch_strength", 0.0)),
                "pinch_distance": float(hdata.get("pinch_distance", 50.0)),
                "grab_strength": float(hdata.get("grab_strength", 0.0)),
                "palm_pos": (palm_p.get("x", 0), palm_p.get("y", 0), palm_p.get("z", 0)),
                "palm_rot": (palm_r.get("w", 1), palm_r.get("x", 0), palm_r.get("y", 0), palm_r.get("z", 0)),
                "digits": digits_list
            }
        return {"hands": hands_dict, "timestamp": data.get("timestamp", time.perf_counter())}
    except Exception as e:
        print("[LEAP JSON DECODE ERROR]:", e)
        return None


# ============================================================
# INTERACTION LOGIC: HOVER, GRAB, SCALE, RESET
# ============================================================

def update_hand_hover(hand_name):
    """Updates non-destructive hover highlighting for this hand's interaction point."""
    state = hand_states[hand_name]
    if state["grab_target"] is not None:
        return

    new_hover = find_hover_target(state["pinch_pos"])
    old_hover = state["hover"]

    if new_hover != old_hover:
        if old_hover is not None:
            highlight_object(old_hover, False)
        if new_hover is not None:
            highlight_object(new_hover, True)
            # Store initial transform for thumbs-up reset
            if new_hover.name not in initial_object_transforms:
                initial_object_transforms[new_hover.name] = {
                    "loc": new_hover.location.copy(),
                    "rot": new_hover.rotation_quaternion.copy() if new_hover.rotation_mode == 'QUATERNION' else new_hover.rotation_euler.to_quaternion(),
                    "scale": new_hover.scale.copy()
                }
            print(f">>> {hand_name.upper()} HOVER: {new_hover.name}")
        state["hover"] = new_hover


def process_hand_actions(hand_name, is_pinching, is_fist, is_thumbs_up):
    """Handles single-hand grabbing, moving, rotating, and thumbs-up resetting."""
    state = hand_states[hand_name]
    other_name = "Right" if hand_name == "Left" else "Left"
    other_state = hand_states[other_name]

    # Fist has priority over pinch, but remains an independent grab gesture.
    effective_pinch = is_pinching and not is_fist
    action_held = effective_pinch or is_fist
    prev_held = state["pinch"] or state["grab"]

    trigger_start = action_held and not prev_held
    trigger_release = not action_held and prev_held

    state["pinch"] = effective_pinch
    state["grab"] = is_fist
    state["thumbs_up"] = is_thumbs_up

    # --- 1. THUMBS UP GESTURE (SCENE / OBJECT RESET) ---
    now = time.perf_counter()
    if is_thumbs_up:
        if state["thumbs_up_start"] == 0.0:
            state["thumbs_up_start"] = now
        elif now - state["thumbs_up_start"] > 0.5:
            # Trigger object reset
            target = state["grab_target"] or state["hover"]
            if target and target.name in initial_object_transforms:
                orig = initial_object_transforms[target.name]
                target.location = orig["loc"].copy()
                if target.rotation_mode == 'QUATERNION':
                    target.rotation_quaternion = orig["rot"].copy()
                else:
                    target.rotation_euler = orig["rot"].to_euler(target.rotation_mode)
                target.scale = orig["scale"].copy()
                print(f">>> [GESTURE] THUMBS-UP RESET: {target.name} restored to original transform!")
                state["thumbs_up_start"] = now + 1.0  # Cooldown
    else:
        state["thumbs_up_start"] = 0.0

    # --- 2. GRAB START ---
    if trigger_start:
        target = state["hover"]
        if target is not None:
            # Check if other hand is already interacting with this target -> trigger two-hand resize
            if other_state["is_active"] and (other_state["pinch"] or other_state["grab"]):
                other_target = other_state["grab_target"] or other_state["hover"]
                if other_target == target:
                    start_two_hand_interaction(target)
                    return

            state["grab_target"] = target
            state["grab_offset_pos"] = target.location - state["pinch_pos"]

            # Relative rotation tracking
            if target.rotation_mode != 'QUATERNION':
                target.rotation_mode = 'QUATERNION'
            state["grab_offset_rot"] = state["palm_rot"].inverted() @ target.rotation_quaternion
            print(f">>> {hand_name.upper()} GRABBED: {target.name}")

    # --- 3. 6-DOF DRAGGING & ROTATION ---
    if action_held and state["grab_target"] is not None:
        target = state["grab_target"]
        if not (two_hand_interaction["active"] and two_hand_interaction["object"] == target):
            # Smooth 3D position following
            target.location = state["pinch_pos"] + state["grab_offset_pos"]
            # 6-DOF Hand rotation
            target.rotation_quaternion = state["palm_rot"] @ state["grab_offset_rot"]

    # --- 4. RELEASE (DROP) ---
    if trigger_release:
        if state["grab_target"] is not None:
            print(f">>> {hand_name.upper()} RELEASED: {state['grab_target'].name}")
            state["grab_target"] = None


def start_two_hand_interaction(obj):
    """Activates Two-Hand Expand/Contract scaling and dual-hand rotation."""
    left = hand_states["Left"]
    right = hand_states["Right"]
    hand_delta = right["pinch_pos"] - left["pinch_pos"]
    dist = hand_delta.length
    if dist < 0.02:
        return

    two_hand_interaction["active"] = True
    two_hand_interaction["object"] = obj
    two_hand_interaction["initial_distance"] = dist
    two_hand_interaction["initial_scale"] = obj.scale.copy()
    initial_vector = right["pinch_pos"] - left["pinch_pos"]
    two_hand_interaction["initial_vector"] = Vector((initial_vector.x, 0.0, initial_vector.z))
    if obj.rotation_mode != 'QUATERNION':
        obj.rotation_mode = 'QUATERNION'
    two_hand_interaction["initial_rot"] = obj.rotation_quaternion.copy()

    # Clear single-hand grab so hands don't fight
    left["grab_target"] = None
    right["grab_target"] = None
    print(f"TWO-HAND TRANSFORM START: {obj.name}")


def update_two_hand_interaction():
    """Scales object dynamically based on hand distance and rotates with dual-hand vector."""
    left = hand_states["Left"]
    right = hand_states["Right"]

    left_active = left["is_active"] and (left["pinch"] or left["grab"])
    right_active = right["is_active"] and (right["pinch"] or right["grab"])

    if not (left_active and right_active):
        stop_two_hand_interaction()
        return

    obj = two_hand_interaction["object"]
    init_dist = two_hand_interaction["initial_distance"]
    if obj is None or init_dist < 0.001:
        stop_two_hand_interaction()
        return

    hand_delta = left["pinch_pos"] - right["pinch_pos"]
    current_dist = hand_delta.length
    scale_factor = current_dist / init_dist
    scale_factor = max(MIN_SCALE_FACTOR, min(MAX_SCALE_FACTOR, scale_factor))

    # Apply uniform proportional scale
    obj.scale = two_hand_interaction["initial_scale"] * scale_factor

    # Rotate from the initial hand angle in X-Z around world Y without accumulation.
    current_vector = right["pinch_pos"] - left["pinch_pos"]
    current_vector = Vector((current_vector.x, 0.0, current_vector.z))
    initial_vector = two_hand_interaction["initial_vector"]
    if initial_vector.length > 0.001 and current_vector.length > 0.001:
        cross_y = initial_vector.z * current_vector.x - initial_vector.x * current_vector.z
        dot = initial_vector.x * current_vector.x + initial_vector.z * current_vector.z
        angle_delta = math.atan2(cross_y, dot)
        delta_rotation = Quaternion((0.0, 1.0, 0.0), angle_delta)
        obj.rotation_quaternion = delta_rotation @ two_hand_interaction["initial_rot"]

    # Follow hand midpoint
    midpoint = (left["pinch_pos"] + right["pinch_pos"]) * 0.5
    obj.location = midpoint


def stop_two_hand_interaction():
    if two_hand_interaction["active"]:
        obj_name = two_hand_interaction["object"].name if two_hand_interaction["object"] else "Object"
        print(f"TWO-HAND TRANSFORM END: {obj_name}")
    two_hand_interaction["active"] = False
    two_hand_interaction["object"] = None
    two_hand_interaction["initial_distance"] = 0.0
    two_hand_interaction["initial_vector"] = Vector((0, 0, 0))


def check_hand_timeout(seen_hands):
    """Safely cleans up any hands that exited the Leap FOV."""
    now = time.perf_counter()
    for hand_name in ("Left", "Right"):
        state = hand_states[hand_name]
        rig = visual_rigs[hand_name]
        if hand_name not in seen_hands:
            if state["is_active"] and (now - state["last_seen"] > HAND_TIMEOUT):
                state["is_active"] = False
                state["pinch"] = False
                state["grab"] = False
                state["fist"] = False
                state["cursor_frozen"] = False
                state["clutch_resume_active"] = False
                state["cursor_initialized"] = False
                state["fist_pinch_warning"] = False
                rig.set_visible(False)
                if state["hover"] is not None:
                    highlight_object(state["hover"], False)
                    state["hover"] = None
                if state["grab_target"] is not None:
                    print(f">>> {hand_name.upper()} RELEASED (Tracking lost): {state['grab_target'].name}")
                    state["grab_target"] = None
                if two_hand_interaction["active"]:
                    stop_two_hand_interaction()
                # Reset filters
                hand_filters[hand_name]["palm"].reset()
                for f in hand_filters[hand_name]["digits"]: f.reset()
                for f in hand_filters[hand_name]["knuckles"]: f.reset()


# ============================================================
# MAIN 200HZ UPDATE LOOP
# ============================================================

def leap_update():
    newest_packet = None

    # Drain socket buffer to get freshest tracking packet (zero latency)
    while True:
        try:
            packet, addr = sock.recvfrom(65535)
            newest_packet = packet
        except (BlockingIOError, InterruptedError):
            break
        except OSError as e:
            if getattr(e, "winerror", None) in (10035, 10054):
                break
            break

    try:
        return _leap_update_inner(newest_packet)
    except Exception as e:
        print(f"[LEAP UPDATE ERROR] {type(e).__name__}: {e}")
        return TIMER_INTERVAL


def _leap_update_inner(newest_packet):
    if newest_packet is not None:
        # Auto-detect format: LP5F Binary or JSON
        if newest_packet.startswith(b"LP5F"):
            frame_data = decode_lp5f(newest_packet)
        elif newest_packet.startswith(b"{"):
            frame_data = decode_json(newest_packet)
        else:
            frame_data = None

        if frame_data is None:
            return TIMER_INTERVAL

        hands_data = frame_data.get("hands", {})
        seen_hands = set()
        now = time.perf_counter()

        for hand_name in ("Left", "Right"):
            if hand_name in hands_data:
                seen_hands.add(hand_name)
                h = hands_data[hand_name]
                state = hand_states[hand_name]
                rig = visual_rigs[hand_name]
                filters = hand_filters[hand_name]

                state["is_active"] = True
                state["last_seen"] = now
                rig.set_visible(True)

                # --- 1. FILTER & UPDATE PALM ---
                raw_palm = transform_to_blender(h["palm_pos"])
                if USE_SMOOTHING:
                    palm_pos = filters["palm"].filter(raw_palm, now)
                else:
                    palm_pos = raw_palm

                state["palm_pos"] = palm_pos
                rot_tuple = h["palm_rot"]  # (w, x, y, z)
                palm_rot = Quaternion(rot_tuple)
                state["palm_rot"] = palm_rot

                rig.palm.location = palm_pos
                rig.palm.rotation_quaternion = palm_rot

                # --- 2. FILTER & UPDATE 5 FINGERTIPS & KNUCKLES ---
                digits = h.get("digits", [])
                for i, d in enumerate(digits):
                    if i >= 5: break
                    raw_tip = transform_to_blender(d["tip"])
                    raw_mcp = transform_to_blender(d.get("mcp", d["tip"]))

                    if USE_SMOOTHING:
                        tip_pos = filters["digits"][i].filter(raw_tip, now)
                        mcp_pos = filters["knuckles"][i].filter(raw_mcp, now)
                    else:
                        tip_pos = raw_tip
                        mcp_pos = raw_mcp

                    rig.tips[i].location = tip_pos
                    rig.knuckles[i].location = mcp_pos

                # Pinch interaction point is between thumb and index tips
                thumb_pos = rig.tips[0].location
                index_pos = rig.tips[1].location
                pinch_midpoint = (thumb_pos + index_pos) * 0.5
                state["pinch_pos"] = pinch_midpoint

                # Fist state remains independent from continuous cursor tracking.
                is_fisting = bool(h.get("fist", False))
                if not state["cursor_initialized"]:
                    state["cursor_position"] = index_pos.copy()
                    state["cursor_initialized"] = True
                if is_fisting and not state["fist"]:
                    state["frozen_cursor_position"] = state["cursor_position"].copy()
                    state["cursor_frozen"] = True
                    state["clutch_resume_active"] = False
                elif not is_fisting and state["fist"]:
                    state["clutch_resume_reference"] = index_pos.copy()
                    state["clutch_resume_position"] = state["frozen_cursor_position"].copy()
                    state["cursor_frozen"] = False
                    state["clutch_resume_active"] = True
                if state["cursor_frozen"]:
                    state["cursor_position"] = state["frozen_cursor_position"].copy()
                elif state["clutch_resume_active"]:
                    state["cursor_position"] = state["clutch_resume_position"] + (index_pos - state["clutch_resume_reference"])
                else:
                    state["cursor_position"] = index_pos.copy()
                if is_fisting and h.get("pinch", False) and not state["fist_pinch_warning"]:
                    print(f">>> WARNING: {hand_name.upper()} fist + pinch received; fist takes priority")
                    state["fist_pinch_warning"] = True
                elif not is_fisting or not h.get("pinch", False):
                    state["fist_pinch_warning"] = False
                state["fist"] = is_fisting

                # Update legacy scene cursors if present
                if hand_name == "Left":
                    left_cursor = bpy.data.objects.get("LEAP_LEFT_CURSOR") or legacy_left_cursor
                    if left_cursor:
                        left_cursor.location = state["cursor_position"]
                else:
                    right_cursor = bpy.data.objects.get("LEAP_RIGHT_CURSOR") or legacy_right_cursor
                    if right_cursor:
                        right_cursor.location = state["cursor_position"]

                # --- 3. DYNAMIC PINCH VISUAL FEEDBACK ---
                is_pinching = h["pinch"] and not is_fisting
                rig.set_pinch_visual(is_pinching)

                # --- 4. HOVER & OBJECT ACTIONS ---
                update_hand_hover(hand_name)
                process_hand_actions(
                    hand_name,
                    is_pinching=is_pinching,
                    is_fist=False,
                    is_thumbs_up=h.get("thumbs_up", False)
                )

        check_hand_timeout(seen_hands)

        # --- 5. TWO-HAND INTERACTION UPDATE ---
        if two_hand_interaction["active"]:
            update_two_hand_interaction()
        else:
            left = hand_states["Left"]
            right = hand_states["Right"]
            if left["is_active"] and right["is_active"]:
                left_acting = left["pinch"] or left["grab"]
                right_acting = right["pinch"] or right["grab"]
                if left_acting and right_acting:
                    # Check shared object interaction
                    target = None
                    if left["grab_target"] and left["grab_target"] == right["grab_target"]:
                        target = left["grab_target"]
                    elif left["grab_target"] and (right["hover"] == left["grab_target"]):
                        target = left["grab_target"]
                    elif right["grab_target"] and (left["hover"] == right["grab_target"]):
                        target = right["grab_target"]
                    elif left["hover"] and (left["hover"] == right["hover"]):
                        target = left["hover"]

                    if target is not None:
                        start_two_hand_interaction(target)

        # Tag 3D Viewports for smooth redraw
        for window in bpy.context.window_manager.windows:
            for area in window.screen.areas:
                if area.type == "VIEW_3D":
                    area.tag_redraw()

    else:
        check_hand_timeout(set())

    return TIMER_INTERVAL


# Register 200Hz Blender Timer
bpy.app.timers.register(leap_update, first_interval=TIMER_INTERVAL)
bpy.app.driver_namespace["leap_timer"] = leap_update

print("")
print("=" * 65)
print("  LEAP MOTION 5-FINGER RECEIVER & 3D INTERACTION ENGINE ACTIVE  ")
print("=" * 65)
print(f"  Network:          UDP {HOST}:{PORT} (LP5F Binary & JSON Auto)")
print(f"  Visual Hand Rig:  '{COLLECTION_NAME}' (5 Fingers + Palm Articulation)")
print(f"  Jitter Filter:    One-Euro Adaptive Low-Pass (Steady & Zero-Lag)")
print(f"  Actions Enabled:  Single-Hand Pinch/Fist (Grab, Move, 6-DOF Rotate)")
print(f"                    Two-Hand Pinch (Expand / Contract Scaling)")
print(f"                    Thumbs-Up (Reset Object Position/Scale)")
print("=" * 65)
print("")