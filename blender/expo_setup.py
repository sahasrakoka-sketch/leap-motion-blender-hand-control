import bpy
import math
from mathutils import Vector


PROJECT_DIR = bpy.path.abspath("//")
OUTPUT_FILE = "leap_hand_control_EXPO.blend"
EXPO_CAMERA_NAME = "EXPO_CAMERA"
EXPO_COLLECTION_NAME = "EXPO_PRESENTATION"
EXPO_OBJECTS_COLLECTION_NAME = "EXPO_OBJECTS"

BACKGROUND_COLOR = (0.025, 0.035, 0.05, 1.0)

OBJECT_COLORS = {
    "EXPO_Cube": (0.35, 0.65, 0.95, 1.0),
    "EXPO_Sphere": (0.95, 0.68, 0.48, 1.0),
    "EXPO_Cone": (0.45, 0.85, 0.70, 1.0),
    "EXPO_Cylinder": (0.65, 0.55, 0.90, 1.0),
    "EXPO_Torus": (0.95, 0.78, 0.35, 1.0),
    "EXPO_Icosphere": (0.95, 0.50, 0.62, 1.0),
}

EXPO_OBJECT_SPECS = {
    "EXPO_Cube": ("cube", (-7.0, 0.0, 5.0)),
    "EXPO_Sphere": ("sphere", (7.0, 0.0, 5.0)),
    "EXPO_Cone": ("cone", (0.0, 0.0, 9.0)),
    "EXPO_Cylinder": ("cylinder", (0.0, 0.0, -9.0)),
    "EXPO_Torus": ("torus", (-7.0, 0.0, -6.0)),
    "EXPO_Icosphere": ("icosphere", (7.0, 0.0, -6.0)),
}


def get_or_create_collection(name):
    collection = bpy.data.collections.get(name)
    if collection is None:
        collection = bpy.data.collections.new(name)
        bpy.context.scene.collection.children.link(collection)
    return collection


def get_or_create_material(name, color, roughness=0.42, metallic=0.0):
    material = bpy.data.materials.get(name)
    if material is None:
        material = bpy.data.materials.new(name)
        material.use_nodes = True

    material.diffuse_color = color
    nodes = material.node_tree.nodes
    principled = nodes.get("Principled BSDF")
    if principled is not None:
        principled.inputs["Base Color"].default_value = color
        principled.inputs["Roughness"].default_value = roughness
        principled.inputs["Metallic"].default_value = metallic
        if "Specular IOR Level" in principled.inputs:
            principled.inputs["Specular IOR Level"].default_value = 0.35
        elif "Specular" in principled.inputs:
            principled.inputs["Specular"].default_value = 0.35
    return material


def assign_material(obj, material):
    if obj is None or obj.type != "MESH":
        return
    if len(obj.data.materials) == 0:
        obj.data.materials.append(material)
    else:
        obj.data.materials[0] = material


def move_to_collection(obj, collection):
    for source_collection in list(obj.users_collection):
        if source_collection != collection:
            source_collection.objects.unlink(obj)
    if obj.name not in collection.objects:
        collection.objects.link(obj)


def create_primitive(spec):
    primitive, location = spec
    if primitive == "cube":
        bpy.ops.mesh.primitive_cube_add(size=2.4, location=location)
    elif primitive == "sphere":
        bpy.ops.mesh.primitive_uv_sphere_add(segments=32, ring_count=20, radius=1.45, location=location)
    elif primitive == "cone":
        bpy.ops.mesh.primitive_cone_add(vertices=32, radius1=1.5, radius2=0.45, depth=2.8, location=location)
    elif primitive == "cylinder":
        bpy.ops.mesh.primitive_cylinder_add(vertices=32, radius=1.35, depth=2.8, location=location)
    elif primitive == "torus":
        bpy.ops.mesh.primitive_torus_add(major_radius=1.35, minor_radius=0.42, major_segments=40, minor_segments=16, location=location)
    elif primitive == "icosphere":
        bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=3, radius=1.5, location=location)
    else:
        raise ValueError(f"Unsupported expo primitive: {primitive}")
    return bpy.context.object


def get_or_create_expo_object(name, spec, collection):
    obj = bpy.data.objects.get(name)
    if obj is None:
        obj = create_primitive(spec)
        obj.name = name
    elif obj.type != "MESH":
        raise TypeError(f"Existing object {name} is not a mesh")

    obj.location = spec[1]
    move_to_collection(obj, collection)
    obj.hide_render = False
    obj.hide_viewport = False
    return obj


def point_camera_at(camera, target):
    camera.rotation_euler = (target - camera.location).to_track_quat("-Z", "Y").to_euler()


def get_or_create_light(name, light_type, location, energy, color, size):
    obj = bpy.data.objects.get(name)
    if obj is None or obj.type != "LIGHT":
        if obj is not None:
            bpy.data.objects.remove(obj, do_unlink=True)
        light_data = bpy.data.lights.new(name, type=light_type)
        obj = bpy.data.objects.new(name, light_data)
        bpy.context.scene.collection.objects.link(obj)
    obj.location = location
    obj.data.energy = energy
    obj.data.color = color
    if light_type == "AREA":
        obj.data.shape = "DISK"
        obj.data.size = size
    return obj


def configure_world():
    world = bpy.context.scene.world
    if world is None:
        world = bpy.data.worlds.new("EXPO_WORLD")
        bpy.context.scene.world = world
    world.use_nodes = True
    background = world.node_tree.nodes.get("Background")
    if background is not None:
        background.inputs["Color"].default_value = BACKGROUND_COLOR
        background.inputs["Strength"].default_value = 0.22


def configure_objects():
    collection = get_or_create_collection(EXPO_OBJECTS_COLLECTION_NAME)
    objects = []
    for name, spec in EXPO_OBJECT_SPECS.items():
        obj = get_or_create_expo_object(name, spec, collection)
        material = get_or_create_material(f"EXPO_MAT_{name}", OBJECT_COLORS[name])
        assign_material(obj, material)
        objects.append(obj)
    return objects


def configure_cursors():
    collection = get_or_create_collection(EXPO_COLLECTION_NAME)
    left_material = get_or_create_material("EXPO_CURSOR_LEFT", (0.65, 0.22, 0.95, 1.0), roughness=0.3)
    right_material = get_or_create_material("EXPO_CURSOR_RIGHT", (0.20, 0.95, 0.52, 1.0), roughness=0.3)
    cursor_specs = {
        "LEAP_LEFT_CURSOR": ((-5.0, 0.0, 0.0), left_material),
        "LEAP_RIGHT_CURSOR": ((5.0, 0.0, 0.0), right_material),
    }

    for name, (location, material) in cursor_specs.items():
        cursor = bpy.data.objects.get(name)
        if cursor is None:
            bpy.ops.mesh.primitive_uv_sphere_add(
                segments=32,
                ring_count=20,
                radius=0.5,
                location=location,
            )
            cursor = bpy.context.object
            cursor.name = name
        elif cursor.type != "MESH":
            raise TypeError(f"Existing cursor {name} is not a mesh")

        cursor.location = location
        move_to_collection(cursor, collection)
        assign_material(cursor, material)
        cursor.hide_viewport = False
        cursor.hide_render = False
        cursor.show_in_front = True

    for name, material in (
        ("LEAP_LEFT_CURSOR", left_material),
        ("LEAP_RIGHT_CURSOR", right_material),
        ("IndexCursor", right_material),
    ):
        cursor = bpy.data.objects.get(name)
        if cursor is not None:
            assign_material(cursor, material)
            cursor.show_in_front = True
            cursor.hide_render = False
            cursor.hide_viewport = False


def configure_hand_rig():
    left_material = get_or_create_material("EXPO_HAND_LEFT", (0.32, 0.68, 0.92, 1.0), roughness=0.5)
    right_material = get_or_create_material("EXPO_HAND_RIGHT", (0.95, 0.45, 0.25, 1.0), roughness=0.5)
    for obj in bpy.data.objects:
        if obj.name.startswith("LEAP_Left_"):
            assign_material(obj, left_material)
            obj.show_in_front = True
        elif obj.name.startswith("LEAP_Right_"):
            assign_material(obj, right_material)
            obj.show_in_front = True


def configure_camera():
    camera = bpy.data.objects.get(EXPO_CAMERA_NAME)
    if camera is None or camera.type != "CAMERA":
        camera_data = bpy.data.cameras.new(EXPO_CAMERA_NAME)
        camera = bpy.data.objects.new(EXPO_CAMERA_NAME, camera_data)
        bpy.context.scene.collection.objects.link(camera)

    camera.location = (18.0, -26.0, 15.0)
    camera.data.type = "PERSP"
    camera.data.lens = 52
    camera.data.clip_start = 0.1
    camera.data.clip_end = 200.0
    point_camera_at(camera, Vector((0.0, 0.0, 1.5)))
    bpy.context.scene.camera = camera
    return camera


def configure_lighting():
    get_or_create_light(
        "EXPO_KEY_LIGHT",
        "AREA",
        (4.0, -8.0, 16.0),
        1100.0,
        (1.0, 0.88, 0.76),
        8.0,
    )
    key = bpy.data.objects["EXPO_KEY_LIGHT"]
    point_camera_at(key, Vector((0.0, 0.0, 1.5)))

    get_or_create_light(
        "EXPO_FILL_LIGHT",
        "AREA",
        (-12.0, -3.0, 8.0),
        650.0,
        (0.62, 0.78, 1.0),
        10.0,
    )
    fill = bpy.data.objects["EXPO_FILL_LIGHT"]
    point_camera_at(fill, Vector((0.0, 0.0, 1.5)))

    get_or_create_light(
        "EXPO_RIM_LIGHT",
        "AREA",
        (0.0, 8.0, 10.0),
        850.0,
        (0.70, 0.82, 1.0),
        6.0,
    )
    rim = bpy.data.objects["EXPO_RIM_LIGHT"]
    point_camera_at(rim, Vector((0.0, 0.0, 2.0)))


def configure_render():
    scene = bpy.context.scene
    scene.render.engine = "BLENDER_EEVEE_NEXT"
    scene.render.resolution_x = 1600
    scene.render.resolution_y = 900
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.film_transparent = False
    scene.render.filepath = "//expo_preview.png"


def main():
    get_or_create_collection(EXPO_COLLECTION_NAME)
    configure_world()
    expo_objects = configure_objects()
    configure_cursors()
    configure_hand_rig()
    configure_camera()
    configure_lighting()
    configure_render()

    output_path = bpy.path.abspath(f"//{OUTPUT_FILE}")
    bpy.ops.wm.save_as_mainfile(filepath=output_path)

    object_names = [obj.name for obj in expo_objects]
    print("EXPO SCENE SETUP COMPLETE")
    print("Objects:")
    for name in object_names:
        print(name)
    print(f"Object count: {len(expo_objects)}")
    print("All objects:")
    print("- exist in the scene")
    print("- are MESH objects")
    print(f"- are in {EXPO_OBJECTS_COLLECTION_NAME}")
    print("- have solid pastel materials")
    print("- have no gradients")
    print("- are independent of the tracking system")
    print(f"Camera: {EXPO_CAMERA_NAME}")
    print("Lighting: configured")
    print("Background: configured")
    print("Gradients: none")
    print("Core interaction: unchanged")
    print(f"Saved: {output_path}")


if __name__ == "__main__":
    main()
