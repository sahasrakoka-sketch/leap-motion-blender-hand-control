import bpy
from mathutils import Vector
from bpy_extras import view3d_utils


NAMESPACE_KEY = "expo_controls_state"
HANDLER_KEY = "expo_controls_handler"
OPERATOR_ID = "expo_controls.mouse_handler"

OBJECT_NAMES = (
    "EXPO_Cube",
    "EXPO_Sphere",
    "EXPO_Cone",
    "EXPO_Cylinder",
    "EXPO_Torus",
    "EXPO_Icosphere",
)
BUTTON_NAMES = {
    "RESET": "EXPO_UI_BUTTON_RESET",
    "DEMO": "EXPO_UI_BUTTON_DEMO",
    "HELP": "EXPO_UI_BUTTON_HELP",
    "HANDS": "EXPO_UI_BUTTON_HANDS",
}
BUTTON_TEXT_NAMES = {
    "RESET": "EXPO_UI_BUTTON_TEXT_RESET",
    "DEMO": "EXPO_UI_BUTTON_TEXT_DEMO",
    "HELP": "EXPO_UI_BUTTON_TEXT_HELP",
    "HANDS": "EXPO_UI_BUTTON_TEXT_HANDS",
}
HELP_OBJECT_PREFIXES = (
    "EXPO_UI_GUIDE_",
    "EXPO_UI_AXIS_",
)


def get_state():
    state = bpy.app.driver_namespace.get(NAMESPACE_KEY)
    if state is None:
        state = {
            "original_transforms": {},
            "demo_active": False,
            "demo_time": 0.0,
            "demo_expected": {},
            "help_visible": True,
            "hands_visible": True,
            "status_online": None,
            "last_feedback": None,
        }
        bpy.app.driver_namespace[NAMESPACE_KEY] = state
    return state


def capture_original_transforms(state):
    if state["original_transforms"]:
        return
    for name in OBJECT_NAMES:
        obj = bpy.data.objects.get(name)
        if obj is not None:
            state["original_transforms"][name] = {
                "location": obj.location.copy(),
                "rotation_mode": obj.rotation_mode,
                "rotation": obj.rotation_quaternion.copy() if obj.rotation_mode == "QUATERNION" else obj.rotation_euler.copy(),
                "scale": obj.scale.copy(),
            }


def restore_original_transforms(state):
    for name, transform in state["original_transforms"].items():
        obj = bpy.data.objects.get(name)
        if obj is None:
            continue
        obj.location = transform["location"].copy()
        obj.rotation_mode = transform["rotation_mode"]
        if transform["rotation_mode"] == "QUATERNION":
            obj.rotation_quaternion = transform["rotation"].copy()
        else:
            obj.rotation_euler = transform["rotation"].copy()
        obj.scale = transform["scale"].copy()


def set_object_visibility(prefixes, visible):
    for obj in bpy.data.objects:
        if any(obj.name.startswith(prefix) for prefix in prefixes):
            obj.hide_viewport = not visible
            obj.hide_render = not visible


def get_or_create_material(name, color):
    material = bpy.data.materials.get(name)
    if material is None:
        material = bpy.data.materials.new(name)
        material.use_nodes = True
    material.diffuse_color = color
    principled = material.node_tree.nodes.get("Principled BSDF")
    if principled is not None:
        principled.inputs["Base Color"].default_value = color
        principled.inputs["Roughness"].default_value = 0.45
        principled.inputs["Metallic"].default_value = 0.0
        if "Specular IOR Level" in principled.inputs:
            principled.inputs["Specular IOR Level"].default_value = 0.25
    return material


def set_button_text(label, text):
    obj = bpy.data.objects.get(BUTTON_TEXT_NAMES[label])
    if obj is not None and obj.type == "FONT":
        obj.data.body = text


def set_button_feedback(label, active):
    obj = bpy.data.objects.get(BUTTON_TEXT_NAMES[label])
    if obj is None or obj.type != "FONT":
        return
    base_material = {
        "RESET": "EXPO_UI_PEACH",
        "DEMO": "EXPO_UI_LAVENDER",
        "HELP": "EXPO_UI_BLUE",
        "HANDS": "EXPO_UI_MINT",
    }[label]
    active_material = get_or_create_material("EXPO_UI_ACTIVE", (0.96, 0.92, 0.72, 1.0))
    material = active_material if active else bpy.data.materials.get(base_material)
    if material is not None:
        if len(obj.data.materials) == 0:
            obj.data.materials.append(material)
        else:
            obj.data.materials[0] = material


def update_tracking_status(state):
    online = (
        "leap_timer" in bpy.app.driver_namespace and
        "leap_socket" in bpy.app.driver_namespace and
        bpy.app.driver_namespace.get("leap_socket") is not None
    )
    if online == state["status_online"]:
        return
    state["status_online"] = online
    label = bpy.data.objects.get("EXPO_UI_STATUS_LABEL")
    if label is not None and label.type == "FONT":
        label.data.body = "TRACKING  ACTIVE" if online else "TRACKING  OFFLINE"
        material_name = "EXPO_UI_MINT" if online else "EXPO_UI_PEACH"
        material = bpy.data.materials.get(material_name)
        if material is not None:
            if len(label.data.materials) == 0:
                label.data.materials.append(material)
            else:
                label.data.materials[0] = material


def apply_reset(state):
    stop_demo(state, restore=False)
    restore_original_transforms(state)
    set_button_feedback("RESET", True)
    state["last_feedback"] = "RESET"


def toggle_help(state):
    state["help_visible"] = not state["help_visible"]
    set_object_visibility(HELP_OBJECT_PREFIXES, state["help_visible"])
    set_button_text("HELP", "HELP ON" if state["help_visible"] else "HELP")
    set_button_feedback("HELP", state["help_visible"])


def toggle_hands(state):
    state["hands_visible"] = not state["hands_visible"]
    collection = bpy.data.collections.get("LEAP_HANDS")
    if collection is not None:
        for obj in collection.objects:
            obj.hide_viewport = not state["hands_visible"]
    set_button_text("HANDS", "HANDS ON" if state["hands_visible"] else "HANDS OFF")
    set_button_feedback("HANDS", not state["hands_visible"])


def transform_matches_expected(obj, expected):
    if obj is None:
        return True
    return (
        (obj.location - expected["location"]).length < 0.02 and
        (obj.scale - expected["scale"]).length < 0.02
    )


def start_demo(state):
    if state["demo_active"]:
        stop_demo(state, restore=True)
        return
    capture_original_transforms(state)
    state["demo_active"] = True
    state["demo_time"] = 0.0
    state["demo_expected"] = {}
    set_button_text("DEMO", "DEMO STOP")
    set_button_feedback("DEMO", True)


def stop_demo(state, restore=True):
    if not state["demo_active"]:
        if restore:
            restore_original_transforms(state)
        return
    state["demo_active"] = False
    state["demo_time"] = 0.0
    state["demo_expected"] = {}
    if restore:
        restore_original_transforms(state)
    set_button_text("DEMO", "DEMO")
    set_button_feedback("DEMO", False)


def update_demo(state, delta_time):
    if not state["demo_active"]:
        return
    phase_time = state["demo_time"]
    duration = 14.0
    if phase_time >= duration:
        stop_demo(state, restore=True)
        return

    # Abort if live interaction changed an object since the previous demo frame.
    for name, expected in state["demo_expected"].items():
        if not transform_matches_expected(bpy.data.objects.get(name), expected):
            stop_demo(state, restore=True)
            return

    for name, original in state["original_transforms"].items():
        obj = bpy.data.objects.get(name)
        if obj is None:
            continue
        phase = phase_time / duration
        row = OBJECT_NAMES.index(name) % 3
        col = OBJECT_NAMES.index(name) // 3
        x_wave = 1.4 * __import__("math").sin(phase * 6.283185 + row)
        z_wave = 1.0 * __import__("math").sin(phase * 6.283185 + col * 1.4)
        y_wave = 0.75 * __import__("math").sin(phase * 6.283185 + 1.5)
        obj.location = original["location"] + Vector((x_wave, y_wave, z_wave))
        obj.rotation_mode = "QUATERNION"
        obj.rotation_quaternion = original["rotation"].to_quaternion() if original["rotation_mode"] != "QUATERNION" else original["rotation"]
        obj.rotation_quaternion.rotate_axis("Y", phase * 6.283185)
        scale = 1.0 + 0.18 * (0.5 + 0.5 * __import__("math").sin(phase * 6.283185))
        obj.scale = original["scale"] * scale
        state["demo_expected"][name] = {
            "location": obj.location.copy(),
            "scale": obj.scale.copy(),
        }
    state["demo_time"] += delta_time


def raycast_button(context, event):
    region = context.region
    region_3d = context.space_data.region_3d
    coord = (event.mouse_region_x, event.mouse_region_y)
    origin = view3d_utils.region_2d_to_origin_3d(region, region_3d, coord)
    direction = view3d_utils.region_2d_to_vector_3d(region, region_3d, coord)
    depsgraph = context.evaluated_depsgraph_get()
    hit, location, normal, index, obj, matrix = context.scene.ray_cast(depsgraph, origin, direction)
    if hit and obj is not None:
        for label, name in BUTTON_NAMES.items():
            if obj.name == name:
                return label
    return None


class EXPO_CONTROLS_OT_handler(bpy.types.Operator):
    bl_idname = OPERATOR_ID
    bl_label = "Expo Controls"

    _timer = None
    _last_time = 0.0

    def modal(self, context, event):
        state = get_state()
        if event.type == "TIMER":
            now = __import__("time").perf_counter()
            delta_time = min(0.1, now - self._last_time) if self._last_time else 0.016
            self._last_time = now
            update_tracking_status(state)
            update_demo(state, delta_time)
            return {"PASS_THROUGH"}
        if event.type == "LEFTMOUSE" and event.value == "PRESS":
            label = raycast_button(context, event)
            if label == "RESET":
                apply_reset(state)
            elif label == "DEMO":
                start_demo(state)
            elif label == "HELP":
                toggle_help(state)
            elif label == "HANDS":
                toggle_hands(state)
            return {"PASS_THROUGH"}
        if event.type in {"ESC"}:
            stop_demo(state, restore=True)
            return {"CANCELLED"}
        return {"PASS_THROUGH"}

    def invoke(self, context, event):
        if context.area is None or context.area.type != "VIEW_3D":
            return {"CANCELLED"}
        self._last_time = __import__("time").perf_counter()
        self._timer = context.window_manager.event_timer_add(0.05, window=context.window)
        context.window_manager.modal_handler_add(self)
        bpy.app.driver_namespace[HANDLER_KEY] = self
        return {"RUNNING_MODAL"}

    def cancel(self, context):
        if self._timer is not None:
            context.window_manager.event_timer_remove(self._timer)
            self._timer = None
        if bpy.app.driver_namespace.get(HANDLER_KEY) is self:
            del bpy.app.driver_namespace[HANDLER_KEY]


def register_operator():
    if not hasattr(bpy.types, "EXPO_CONTROLS_OT_handler"):
        bpy.utils.register_class(EXPO_CONTROLS_OT_handler)

    previous = bpy.app.driver_namespace.get(HANDLER_KEY)
    if previous is not None and hasattr(previous, "cancel"):
        try:
            previous.cancel(bpy.context)
        except Exception:
            pass

    if bpy.context.area is not None and bpy.context.area.type == "VIEW_3D":
        bpy.ops.expo_controls.mouse_handler("INVOKE_DEFAULT")


def main():
    state = get_state()
    capture_original_transforms(state)
    update_tracking_status(state)
    register_operator()
    print("EXPO CONTROLS SETUP COMPLETE")
    print("RESET: enabled")
    print("DEMO: enabled")
    print("HELP: enabled")
    print("HANDS: enabled")
    print("TRACKING STATUS: enabled")
    print("UDP sockets added: 0")
    print("Tracking systems added: 0")
    print("Cursor systems added: 0")
    print("Gradients added: 0")


if __name__ == "__main__":
    main()
