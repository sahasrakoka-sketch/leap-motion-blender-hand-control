import bpy
from mathutils import Vector


UI_COLLECTION_NAME = "EXPO_UI"
CAMERA_NAME = "EXPO_CAMERA"

COLORS = {
    "background": (0.025, 0.035, 0.05, 1.0),
    "lavender": (0.68, 0.54, 0.92, 1.0),
    "mint": (0.48, 0.86, 0.70, 1.0),
    "peach": (0.96, 0.66, 0.48, 1.0),
    "blue": (0.48, 0.70, 0.92, 1.0),
    "warm_white": (0.94, 0.92, 0.88, 1.0),
    "muted": (0.62, 0.66, 0.72, 1.0),
    "charcoal": (0.07, 0.09, 0.13, 1.0),
}


def get_or_create_collection(name):
    collection = bpy.data.collections.get(name)
    if collection is None:
        collection = bpy.data.collections.new(name)
        bpy.context.scene.collection.children.link(collection)
    return collection


def move_to_collection(obj, collection):
    for source_collection in list(obj.users_collection):
        if source_collection != collection:
            source_collection.objects.unlink(obj)
    if obj.name not in collection.objects:
        collection.objects.link(obj)


def get_or_create_material(name, color, roughness=0.5):
    material = bpy.data.materials.get(name)
    if material is None:
        material = bpy.data.materials.new(name)
        material.use_nodes = True
    material.diffuse_color = color
    principled = material.node_tree.nodes.get("Principled BSDF")
    if principled is not None:
        principled.inputs["Base Color"].default_value = color
        principled.inputs["Roughness"].default_value = roughness
        principled.inputs["Metallic"].default_value = 0.0
        if "Specular IOR Level" in principled.inputs:
            principled.inputs["Specular IOR Level"].default_value = 0.25
    return material


def assign_material(obj, material):
    if len(obj.data.materials) == 0:
        obj.data.materials.append(material)
    else:
        obj.data.materials[0] = material


def face_camera(obj, camera):
    direction = camera.location - obj.location
    obj.rotation_euler = direction.to_track_quat("Z", "Y").to_euler()


def get_or_create_text(name, body, location, size, material, collection, camera):
    obj = bpy.data.objects.get(name)
    if obj is None or obj.type != "FONT":
        if obj is not None:
            bpy.data.objects.remove(obj, do_unlink=True)
        curve = bpy.data.curves.new(name, type="FONT")
        obj = bpy.data.objects.new(name, curve)
        collection.objects.link(obj)
    else:
        move_to_collection(obj, collection)

    obj.data.body = body
    obj.data.align_x = "CENTER"
    obj.data.align_y = "CENTER"
    obj.data.size = size
    obj.data.extrude = 0.008
    obj.data.bevel_depth = 0.002
    obj.data.materials.clear()
    obj.data.materials.append(material)
    obj.location = location
    obj.hide_render = False
    obj.hide_viewport = False
    obj.show_in_front = True
    face_camera(obj, camera)
    return obj


def get_or_create_panel(name, location, dimensions, material, collection):
    obj = bpy.data.objects.get(name)
    if obj is None or obj.type != "MESH":
        if obj is not None:
            bpy.data.objects.remove(obj, do_unlink=True)
        bpy.ops.mesh.primitive_cube_add(size=1.0, location=location)
        obj = bpy.context.object
        obj.name = name
    move_to_collection(obj, collection)

    obj.location = location
    obj.dimensions = dimensions
    obj.rotation_euler = (0.0, 0.0, 0.0)
    assign_material(obj, material)
    obj.hide_render = False
    obj.hide_viewport = False
    return obj


def get_or_create_indicator(name, location, radius, material, collection):
    obj = bpy.data.objects.get(name)
    if obj is None or obj.type != "MESH":
        if obj is not None:
            bpy.data.objects.remove(obj, do_unlink=True)
        bpy.ops.mesh.primitive_uv_sphere_add(segments=24, ring_count=12, radius=radius, location=location)
        obj = bpy.context.object
        obj.name = name
    move_to_collection(obj, collection)
    obj.location = location
    assign_material(obj, material)
    obj.hide_render = False
    obj.hide_viewport = False
    obj.show_in_front = True
    return obj


def create_axis_line(name, start, end, material, collection):
    curve = bpy.data.curves.get(name)
    if curve is None:
        curve = bpy.data.curves.new(name, type="CURVE")
    curve.dimensions = "3D"
    curve.bevel_depth = 0.012
    curve.bevel_resolution = 2
    curve.splines.clear()
    spline = curve.splines.new("POLY")
    spline.points.add(1)
    spline.points[0].co = (*start, 1.0)
    spline.points[1].co = (*end, 1.0)
    obj = bpy.data.objects.get(name)
    if obj is None or obj.type != "CURVE":
        if obj is not None:
            bpy.data.objects.remove(obj, do_unlink=True)
        obj = bpy.data.objects.new(name, curve)
        collection.objects.link(obj)
    else:
        move_to_collection(obj, collection)
    obj.data.materials.clear()
    obj.data.materials.append(material)
    obj.hide_render = False
    obj.hide_viewport = False
    return obj


def main():
    collection = get_or_create_collection(UI_COLLECTION_NAME)
    camera = bpy.data.objects.get(CAMERA_NAME)
    if camera is None or camera.type != "CAMERA":
        raise RuntimeError(f"Required camera {CAMERA_NAME} was not found")

    materials = {
        "panel": get_or_create_material("EXPO_UI_PANEL", COLORS["charcoal"]),
        "title": get_or_create_material("EXPO_UI_TITLE", COLORS["warm_white"]),
        "body": get_or_create_material("EXPO_UI_BODY", COLORS["muted"]),
        "lavender": get_or_create_material("EXPO_UI_LAVENDER", COLORS["lavender"]),
        "mint": get_or_create_material("EXPO_UI_MINT", COLORS["mint"]),
        "peach": get_or_create_material("EXPO_UI_PEACH", COLORS["peach"]),
        "blue": get_or_create_material("EXPO_UI_BLUE", COLORS["blue"]),
    }

    # Title sits above the object field, leaving the central workspace open.
    get_or_create_text("EXPO_UI_TITLE", "LEAP MOTION", (-7.0, 1.0, 11.0), 0.9, materials["title"], collection, camera)
    get_or_create_text("EXPO_UI_SUBTITLE", "3D INTERACTION", (-7.0, 1.0, 10.1), 0.48, materials["lavender"], collection, camera)
    get_or_create_text("EXPO_UI_TAGLINE", "Hand-controlled object manipulation", (-7.0, 1.0, 9.5), 0.25, materials["body"], collection, camera)

    # Compact status panel to the upper-right of the object field.
    get_or_create_panel("EXPO_UI_STATUS_PANEL", (8.0, 1.0, 9.5), (4.8, 0.12, 1.35), materials["panel"], collection)
    get_or_create_indicator("EXPO_UI_STATUS_INDICATOR", (6.3, 0.75, 9.5), 0.16, materials["mint"], collection)
    get_or_create_text("EXPO_UI_STATUS_LABEL", "TRACKING  ACTIVE", (8.1, 0.7, 9.5), 0.34, materials["mint"], collection, camera)

    # Four visual-only controls below the interaction field.
    button_names = ("RESET", "DEMO", "HELP", "HANDS")
    button_materials = (materials["peach"], materials["lavender"], materials["blue"], materials["mint"])
    for index, (label, material) in enumerate(zip(button_names, button_materials)):
        x = -5.4 + index * 3.6
        get_or_create_panel(f"EXPO_UI_BUTTON_{label}", (x, 1.0, -11.0), (2.8, 0.12, 0.8), materials["panel"], collection)
        get_or_create_text(f"EXPO_UI_BUTTON_TEXT_{label}", label, (x, 0.72, -11.0), 0.3, material, collection, camera)

    # Compact guide at the right edge, away from the six objects.
    guide_lines = (
        ("INDEX FINGER", "Move cursor"),
        ("PINCH", "Grab object"),
        ("FIST", "Freeze cursor"),
        ("TWO HANDS", "Expand / Contract"),
    )
    for index, (heading, detail) in enumerate(guide_lines):
        z = 6.0 - index * 1.15
        get_or_create_text(f"EXPO_UI_GUIDE_HEAD_{index}", heading, (10.0, 1.0, z), 0.26, materials["title"], collection, camera)
        get_or_create_text(f"EXPO_UI_GUIDE_DETAIL_{index}", detail, (10.0, 1.0, z - 0.35), 0.2, materials["body"], collection, camera)

    # Small, quiet axis reference in the lower-left corner.
    create_axis_line("EXPO_UI_AXIS_X", (-10.0, 0.8, -9.8), (-7.8, 0.8, -9.8), materials["peach"], collection)
    create_axis_line("EXPO_UI_AXIS_Z", (-10.0, 0.8, -9.8), (-10.0, 0.8, -7.6), materials["mint"], collection)
    get_or_create_text("EXPO_UI_AXIS_LABEL_X", "X  Left / Right", (-8.8, 0.65, -10.2), 0.2, materials["peach"], collection, camera)
    get_or_create_text("EXPO_UI_AXIS_LABEL_Z", "Z  Up / Down", (-10.0, 0.65, -7.25), 0.2, materials["mint"], collection, camera)
    get_or_create_text("EXPO_UI_AXIS_LABEL_Y", "Y  Depth", (-10.0, 0.65, -10.65), 0.2, materials["blue"], collection, camera)

    output_path = bpy.path.abspath("//leap_hand_control_EXPO.blend")
    bpy.ops.wm.save_as_mainfile(filepath=output_path)

    print("EXPO UI SETUP COMPLETE")
    print("Collection: EXPO_UI")
    print("Visual controls: RESET, DEMO, HELP, HANDS")
    print("Tracking status: ACTIVE")
    print("Gradients: none")
    print("Interaction logic: unchanged")
    print(f"Saved: {output_path}")


if __name__ == "__main__":
    main()
