"""Build and check the Blender scene used by UPBGE."""

from hashlib import sha256
import json
from math import cos, isclose, pi, radians, sin, tau

import bpy
from mathutils import Euler, Vector

from .schema import DEFAULT_DESIGN, DEFAULT_SPAWN, apply_operation, validate_design


COLLECTION = "LightWorld Scene"
COLLECTION_PROPERTY = "lightworld_scene_design"
DESIGN_PROPERTY = "lightworld_open_world"
ZERO = (0, 0, 0)
GROUND_THICKNESS = .20
EYE_HEIGHT = 1.69
CURVE_SIDES = 32
UNIT_CIRCLE = tuple((cos(tau * i / CURVE_SIDES), sin(tau * i / CURVE_SIDES)) for i in range(CURVE_SIDES))
DEFAULT_MATERIAL = {
    "color": [0.65, 0.65, 0.65], "roughness": 0.55, "metallic": 0,
    "alpha": 1, "transmission": 0, "emission": 0,
}
MATERIAL_INPUTS = {
    "roughness": "Roughness",
    "metallic": "Metallic",
    "alpha": "Alpha",
    "transmission": "Transmission Weight",
    "emission": "Emission Strength",
}


def _extrude(profile, low, high):
    count = len(profile)
    vertices = [(x, y, z) for z in (low, high) for x, y in profile]
    faces = [tuple(reversed(range(count))), tuple(range(count, 2 * count))]
    for index in range(count):
        following = (index + 1) % count
        faces.append((index, following, following + count, index + count))
    return vertices, faces


def _ellipsoid(size):
    rx, ry, rz = (axis / 2 for axis in size)
    sides, bands = CURVE_SIDES, 16
    vertices = [(0, 0, -rz)]
    for band in range(1, bands):
        latitude = pi * (band / bands - .5)
        radius = cos(latitude)
        x, y, z = rx * radius, ry * radius, rz * sin(latitude)
        vertices.extend((x * cosine, y * sine, z) for cosine, sine in UNIT_CIRCLE)
    top = len(vertices)
    vertices.append((0, 0, rz))
    faces = []
    for side in range(sides):
        following = (side + 1) % sides
        faces.append((0, 1 + following, 1 + side))
        for band in range(bands - 2):
            start = 1 + band * sides
            faces.append((start + side, start + following,
                          start + sides + following, start + sides + side))
        start = top - sides
        faces.append((start + side, start + following, top))
    return vertices, faces


def _geometry(part):
    shape = part["shape"]
    if shape == "mesh":
        return part["vertices"], part["faces"]
    if shape == "extrusion":
        return _extrude(part["profile"], 0, part["height"])
    if shape == "ellipsoid":
        return _ellipsoid(part["size"])
    x, y, z = (axis / 2 for axis in part["size"])
    if shape == "box":
        profile = [(-x, -y), (x, -y), (x, y), (-x, y)]
    else:
        profile = [(x * cosine, y * sine) for cosine, sine in UNIT_CIRCLE]
    return _extrude(profile, -z, z)


def _surface(spec):
    values = {**DEFAULT_MATERIAL, **spec}
    signature = json.dumps(values, sort_keys=True, separators=(",", ":"))
    name = f"LightWorld Material {sha256(signature.encode()).hexdigest()[:16]}"
    cached = bpy.data.materials.get(name)
    if cached is not None:
        return cached
    material = bpy.data.materials.new(name)
    rgba = (*values["color"], values["alpha"])
    material.diffuse_color = rgba
    material.use_nodes = True
    shader = material.node_tree.nodes.get("Principled BSDF")
    for field, socket in MATERIAL_INPUTS.items():
        shader.inputs[socket].default_value = values[field]
    shader.inputs["Base Color"].default_value = rgba
    shader.inputs["Emission Color"].default_value = (*values["color"], 1)
    if values["alpha"] < 1:
        material.surface_render_method = "BLENDED"
    return material


def _rotation(degrees):
    return Euler(tuple(map(radians, degrees)), "XYZ")


def _transform(item, parent=None):
    position = Vector(item.get("position", ZERO))
    rotation = _rotation(item.get("rotation", ZERO))
    if parent is None:
        return position, rotation
    parent_position, parent_rotation = parent
    world_position = parent_position + parent_rotation @ position
    world_rotation = (parent_rotation @ rotation.to_matrix()).to_euler("XYZ")
    return world_position, world_rotation


def _enable_collision(obj, walkable):
    obj.game.physics_type = "STATIC"
    obj.game.use_collision_bounds = True
    obj.game.collision_bounds_type = "TRIANGLE_MESH"
    flag = "LightWorldWalkable" if walkable else "LightWorldSolid"
    with bpy.context.temp_override(object=obj, active_object=obj):
        bpy.ops.object.game_property_new(type="BOOL", name=flag)
    obj.game.properties[flag].value = True


def _create_mesh(collection, name, part):
    mesh = bpy.data.meshes.new(name)
    vertices, faces = _geometry(part)
    mesh.from_pydata(vertices, [], faces)
    if mesh.validate(verbose=False) or any(face.area <= 1e-12 for face in mesh.polygons):
        bpy.data.meshes.remove(mesh)
        raise ValueError(f"Mesh {name!r} contains invalid or degenerate faces.")
    mesh.materials.append(_surface(part.get("material", {})))
    if part["shape"] == "ellipsoid":
        mesh.polygons.foreach_set("use_smooth", [True] * len(mesh.polygons))
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    collection.objects.link(obj)
    if part.get("collision", True):
        _enable_collision(obj, part.get("walkable", False))
    else:
        obj.game.physics_type = "NO_COLLISION"
    return obj


def _create_light(collection, name, spec):
    light = bpy.data.lights.new(name, spec["type"].upper())
    light.color = spec.get("color", (1, 1, 1))
    light.energy = spec["power"]
    if spec["type"] == "area":
        light.size = spec.get("size", 1)
    else:
        light.shadow_soft_size = spec.get("size", .10)
    obj = bpy.data.objects.new(name, light)
    collection.objects.link(obj)
    return obj


def _ground_part(spec):
    x, y, z = spec.get("position", ZERO)
    return {"shape": "box", "size": [*spec["size"], GROUND_THICKNESS],
            "position": [x, y, z - GROUND_THICKNESS / 2],
            "material": spec.get("material", {}), "walkable": True}


def _sun_rotation(spec):
    azimuth, altitude = map(radians, (spec["azimuth"], spec["elevation"]))
    direction = Vector((-sin(azimuth)*cos(altitude), -cos(azimuth)*cos(altitude), -sin(altitude)))
    return direction.to_track_quat("-Z", "Y").to_euler()


def _spawn_transform(design):
    spawn = design.get("spawn", DEFAULT_SPAWN)
    x, y, z = spawn["position"]
    return (x, y, z + EYE_HEIGHT), (pi/2 - radians(7), 0, radians(spawn["heading"]))


def _configure_lighting(scene, settings):
    background = scene.world.node_tree.nodes.get("Background")
    background.inputs["Color"].default_value = (*settings["sky_color"], 1)
    background.inputs["Strength"].default_value = settings["sky_strength"]
    sun = scene.objects.get("LightWorld Sun")
    spec = settings["sun"]
    if spec is None:
        sun.data.energy = 0
        return
    sun.data.energy = spec["energy"]
    sun.data.color = spec.get("color", (1, 1, 1))
    sun.rotation_euler = _sun_rotation(spec)


def _design_collection(scene):
    return next((item for item in scene.collection.children if item.get(COLLECTION_PROPERTY) is True), None)


def _remove_collection(collection):
    for obj in list(collection.objects):
        datablocks = bpy.data.meshes if obj.type == "MESH" else bpy.data.lights
        data = obj.data
        bpy.data.objects.remove(obj, do_unlink=True)
        if not data.users:
            datablocks.remove(data)
    bpy.data.collections.remove(collection)


def _piece_specs(design):
    if design["ground"] is not None:
        ground = _ground_part(design["ground"])
        yield ("", "ground", 0), "World Ground", ground, _transform(ground)
    for element in design["elements"]:
        position, rotation = _transform(element)
        parent = position, rotation.to_matrix()
        for key, field in (("part", "parts"), ("light", "lights")):
            for index, spec in enumerate(element.get(field, [])):
                label = spec.get("name", f"{key} {index + 1}")
                yield (element["id"], key, index), f"{element['name']} · {label}", spec, _transform(spec, parent)


def _build_design(design):
    scene = bpy.context.scene
    previous = _design_collection(scene)
    created = bpy.data.collections.new(COLLECTION)
    created[COLLECTION_PROPERTY] = True
    scene.collection.children.link(created)
    try:
        for (identifier, kind, index), label, spec, transform in _piece_specs(design):
            create = _create_light if kind == "light" else _create_mesh
            obj = create(created, label, spec)
            obj.location, obj.rotation_euler = transform
            obj["lightworld_id"] = identifier
            obj["lightworld_piece"] = kind
            obj["lightworld_index"] = index
        _configure_lighting(scene, design["lighting"])
        scene.camera.location, scene.camera.rotation_euler = _spawn_transform(design)
    except Exception:
        _remove_collection(created)
        raise
    if previous is not None:
        _remove_collection(previous)
    created.name = COLLECTION
    scene[DESIGN_PROPERTY] = json.dumps(design, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    bpy.context.view_layer.update()


def inspect_design():
    raw = bpy.context.scene.get(DESIGN_PROPERTY)
    if not isinstance(raw, str):
        raise ValueError("The file does not contain LightWorld world data in the current format.")
    return validate_design(json.loads(raw))


def _close(actual, expected):
    return isclose(actual, expected, rel_tol=2e-6, abs_tol=2e-6)


def _near(actual, expected):
    return len(actual) == len(expected) and all(_close(a, b) for a, b in zip(actual, expected))


def _matches_material(material, spec):
    shader = material and material.node_tree and material.node_tree.nodes.get("Principled BSDF")
    if shader is None:
        return False
    values = {**DEFAULT_MATERIAL, **spec}
    rgba = (*values["color"], values["alpha"])
    return (_near(shader.inputs["Base Color"].default_value, rgba)
            and all(_close(shader.inputs[socket].default_value, values[field])
                    for field, socket in MATERIAL_INPUTS.items()))


def _matches_mesh(obj, spec):
    if obj.type != "MESH" or len(obj.data.materials) != 1:
        return False
    mesh = obj.data
    vertices, faces = _geometry(spec)
    if len(mesh.vertices) != len(vertices) or len(mesh.polygons) != len(faces):
        return False
    if any(not _near(vertex.co, expected) for vertex, expected in zip(mesh.vertices, vertices)):
        return False
    if any(tuple(face.vertices) != tuple(expected) for face, expected in zip(mesh.polygons, faces)):
        return False

    walkable = spec.get("walkable", False)
    collision = spec.get("collision", True)
    game = obj.game
    flags = {"LightWorldWalkable": walkable, "LightWorldSolid": collision and not walkable}
    for name, expected in flags.items():
        prop = game.properties.get(name)
        if bool(prop and prop.value) != expected:
            return False
    if game.physics_type != ("STATIC" if collision else "NO_COLLISION"):
        return False
    if collision and (not game.use_collision_bounds or game.collision_bounds_type != "TRIANGLE_MESH"):
        return False
    return _matches_material(mesh.materials[0], spec.get("material", {}))


def _matches_light(obj, spec):
    if obj.type != "LIGHT" or obj.data.type != spec["type"].upper():
        return False
    size = obj.data.size if spec["type"] == "area" else obj.data.shadow_soft_size
    return (_near(obj.data.color, spec.get("color", (1, 1, 1)))
            and _near([obj.data.energy, size],
                      [spec["power"], spec.get("size", 1 if spec["type"] == "area" else .10)]))


def _verify_environment(scene, design):
    expected_position, expected_rotation = _spawn_transform(design)
    camera = scene.camera
    if camera is None or not _near(camera.location, expected_position) or not _near(camera.rotation_euler, expected_rotation):
        raise ValueError("The saved spawn point does not match the design.")
    lighting = design["lighting"]
    background = scene.world and scene.world.node_tree and scene.world.node_tree.nodes.get("Background")
    if (background is None
            or not _near(background.inputs["Color"].default_value, (*lighting["sky_color"], 1))
            or not _close(background.inputs["Strength"].default_value, lighting["sky_strength"])):
        raise ValueError("The saved sky does not match the design.")
    sun = scene.objects.get("LightWorld Sun")
    spec = lighting["sun"]
    if (sun is None or sun.type != "LIGHT" or sun.data.type != "SUN"
            or not _close(sun.data.energy, spec["energy"] if spec is not None else 0)
            or (spec is not None and (not _near(sun.data.color, spec.get("color", (1, 1, 1)))
                                        or not _near(sun.rotation_euler, _sun_rotation(spec))))):
        raise ValueError("The saved sunlight does not match the design.")


def _verify_geometry(design):
    scene = bpy.context.scene
    _verify_environment(scene, design)
    collection = _design_collection(scene)
    if collection is None or collection.hide_render or collection.hide_viewport or collection.children:
        raise ValueError("The scene object collection is missing or hidden in Blender.")
    pieces = {}
    for obj in collection.objects:
        key = (obj.get("lightworld_id"), obj.get("lightworld_piece"), obj.get("lightworld_index"))
        if key in pieces or obj.hide_render or obj.hide_viewport or obj.hide_get():
            raise ValueError(f"Blender object {obj.name!r} is duplicated or hidden.")
        pieces[key] = obj
    vertices = meshes = lights = 0
    for key, label, spec, (position, rotation) in _piece_specs(design):
        obj = pieces.pop(key, None)
        if (obj is None or obj.parent is not None or not _near(obj.scale, (1, 1, 1))
                or not _near(obj.location, position) or not _near(obj.rotation_euler, rotation)):
            raise ValueError(f"The transform of Blender object {label!r} does not match the design.")
        if key[1] == "light":
            if not _matches_light(obj, spec):
                raise ValueError(f"Blender light {label!r} does not match the design.")
            lights += 1
        else:
            if not _matches_mesh(obj, spec):
                raise ValueError(f"Blender mesh {label!r} does not match the design.")
            meshes += 1
            vertices += len(obj.data.vertices)
    if pieces:
        raise ValueError("The Blender scene contains objects outside the design.")
    return {"assets": len(design["elements"]), "mesh_objects": meshes, "vertices": vertices, "lights": lights}


def _open(path):
    if "FINISHED" not in bpy.ops.wm.open_mainfile(filepath=str(path), load_ui=False, use_scripts=False):
        raise ValueError("Blender could not open the selected .blend file.")


def _read_world(expected=None):
    design = inspect_design()
    if expected is not None and design != expected:
        raise ValueError("The saved world data does not match the submitted design.")
    return {"design": design, "verification": _verify_geometry(design)}


def write_world(output, design):
    _build_design(design)
    bpy.context.preferences.filepaths.save_version = 0
    if "FINISHED" not in bpy.ops.wm.save_as_mainfile(filepath=str(output), check_existing=False, compress=False):
        raise ValueError("Blender could not save the generated scene.")
    _open(output)
    return _read_world(expected=design)


def execute(source, output, name, arguments):
    """Operate on world data in the dedicated Blender process."""
    # Reload from disk even after a prior operation failed partway through.
    _open(source)
    if name == "get_scene_design":
        return inspect_design()
    if name == "verify_world":
        return _read_world()
    current = inspect_design() if name == "edit_scene_design" else DEFAULT_DESIGN
    return write_world(output, apply_operation(current, name, arguments))
