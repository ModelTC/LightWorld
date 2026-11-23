"""Scene schema shared by the model and Blender."""

from copy import deepcopy
import math
import re


MAX_ELEMENTS = 1000
MAX_PARTS = 256
MAX_WORLD_PARTS = 4000
MAX_MESH_VERTICES = 4096
MAX_MESH_FACES = 4096
MAX_WORLD_MESH_VERTICES = 200000
MAX_WORLD_MESH_INDICES = 1000000
WORLD_LIMIT = 10000
ID_PATTERN = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,39}$")

DESIGN_GUIDE = """
Build whatever the brief describes. Use descriptive English names for worlds, objects and parts
by default. Preserve existing names and use names specified in the brief verbatim. Use concise
English words for new stable ASCII IDs. Group related geometry into an object so that a whole
house, tree, vehicle, or custom object can be moved by editing its position.
Coordinates are in metres, z is up, x is east and y is north. Set spawn with the feet position
and heading (degrees from north, positive counter-clockwise) if a custom entrance is needed.
An omitted spawn defaults to (0, -16, 0), facing north.
An object's position and rotation transform all of its local parts and lights. A part's rotation
is local Euler XYZ in degrees. Boxes, cylinders and ellipsoids are centred on their part position;
cylinders run along z. Extrusions extend from local z=0 to height along z and accept a simple,
counter-clockwise polygon profile. For any other shape use mesh with local vertices and faces
indexed from zero, outward winding, and simple planar faces. Keep meshes economical.
Use walkable=true for floors, ramps and stairs. Use collision=true for walls and obstacles.
Parts collide by default. For stairs keep consecutive risers at or below 0.25 m and treads at
least 0.25 m deep. Walkable parts are traversable only from nearby heights. Model openings
with real gaps between wall segments. Join visible frame and wall faces at their edges.
Place overlapping trim and cladding at a distinct depth from the wall.
For a house, compose walls, floor, roof and glazing as needed, and leave accessible
entrances. Use alpha/transmission for transparent surfaces.
Set any RGB color, roughness, metallic, transmission or emission as appropriate. Ground and sky
are world settings. Ground can be removed with null. Add point or area lights for local lighting.
For a follow-up, upsert the complete records of only the edited objects and retain stable ids.
Keep preserved parts, dimensions, colors and transforms in those records. Use replace_scene_design
for a complete redesign. Keep the spawn area and requested routes clear.
"""


def number(minimum, maximum):
    return {"type": "number", "minimum": minimum, "maximum": maximum}


def array(items, minimum=0, maximum=None):
    spec = {"type": "array", "items": items, "minItems": minimum}
    if maximum is not None:
        spec["maxItems"] = maximum
    return spec


def vector(length, minimum, maximum):
    return array(number(minimum, maximum), length, length)


def record(fields, required=()):
    return {"type": "object", "properties": fields, "required": list(required),
            "additionalProperties": False}


def nullable(spec):
    return {"anyOf": [spec, {"type": "null"}]}


NAME = {"type": "string", "minLength": 1, "maxLength": 80}
ID = {"type": "string", "pattern": ID_PATTERN.pattern}
POSITION = vector(3, -WORLD_LIMIT, WORLD_LIMIT)
ROTATION = vector(3, -360, 360)
RGB = vector(3, 0, 1)
UNIT = number(0, 1)
LENGTH = number(.001, WORLD_LIMIT)
MATERIAL = record({
    "color": RGB, "roughness": UNIT, "metallic": UNIT, "alpha": UNIT,
    "transmission": UNIT, "emission": number(0, 100),
})
PART_TRANSFORM = {"name": NAME, "position": POSITION, "rotation": ROTATION}
PART_FIELDS = {"material": MATERIAL, "collision": {"type": "boolean"}, "walkable": {"type": "boolean"}}
PART_SIZE = {"size": vector(3, .001, WORLD_LIMIT)}
PART_OPTIONS = {
    "box": (PART_SIZE, ("size",)),
    "ellipsoid": (PART_SIZE, ("size",)),
    "cylinder": (PART_SIZE, ("size",)),
    "extrusion": ({"profile": array(vector(2, -WORLD_LIMIT, WORLD_LIMIT), 3, 256),
                   "height": LENGTH}, ("profile", "height")),
    "mesh": ({"vertices": array(POSITION, 3, MAX_MESH_VERTICES),
              "faces": array(array({"type": "integer", "minimum": 0,
                                    "maximum": MAX_MESH_VERTICES - 1}, 3, 256),
                             1, MAX_MESH_FACES)}, ("vertices", "faces")),
}
PART_SCHEMAS = {
    shape: record(
        {**PART_TRANSFORM, "shape": {"type": "string", "enum": [shape]}, **PART_FIELDS, **fields},
        ("shape", *required),
    )
    for shape, (fields, required) in PART_OPTIONS.items()
}
PART = {"anyOf": list(PART_SCHEMAS.values())}
LOCAL_LIGHT = record({
    **PART_TRANSFORM, "type": {"type": "string", "enum": ["point", "area"]},
    "color": RGB, "power": number(0, 1000000), "size": LENGTH,
}, ("type", "power"))
ELEMENT = record({
    "id": ID, "name": NAME, "position": POSITION, "rotation": ROTATION,
    "parts": array(PART, 0, MAX_PARTS), "lights": array(LOCAL_LIGHT, 0, MAX_PARTS),
}, ("id", "name", "position"))
GROUND = nullable(record({
    "size": vector(2, .001, WORLD_LIMIT), "position": POSITION, "material": MATERIAL,
}, ("size",)))
SUN = record({"azimuth": number(-360, 360), "elevation": number(-90, 90),
              "color": RGB, "energy": number(0, 100)}, ("azimuth", "elevation", "energy"))
LIGHTING = record({"sky_color": RGB, "sky_strength": number(0, 100),
                   "sun": nullable(SUN)}, ("sky_color", "sky_strength", "sun"))
SPAWN = record({"position": POSITION, "heading": number(-360, 360)}, ("position", "heading"))
SITE = {"title": NAME, "ground": GROUND, "lighting": LIGHTING, "spawn": SPAWN}
ELEMENTS = array(ELEMENT, 0, MAX_ELEMENTS)
DESIGN = record({**SITE, "elements": ELEMENTS}, ("title", "ground", "lighting", "elements"))
EDIT = record({**SITE, "upsert": ELEMENTS, "remove": array(ID, 0, MAX_ELEMENTS)})


def tool(name, description, parameters):
    return {"type": "function", "name": name, "description": description,
            "parameters": parameters, "strict": False}


TOOLS = [
    tool("replace_scene_design", "Replace a complete world with custom objects and environment.", DESIGN),
    tool("edit_scene_design", "Update world settings, upsert complete custom objects, or remove objects by id.", EDIT),
]

DEFAULT_SPAWN = {"position": [0, -16, 0], "heading": 0}

DEFAULT_DESIGN = {
    "title": "Untitled World",
    "ground": {"size": [1000, 1000], "material": {"color": [0.19, 0.21, 0.22]}},
    "lighting": {"sky_color": [0.40, 0.58, 0.77], "sky_strength": 0.65,
                 "sun": {"azimuth": 225, "elevation": 42, "color": [1, 0.9, 0.77], "energy": 2.4}},
    "spawn": DEFAULT_SPAWN,
    "elements": [],
}


def _validate(value, spec, path):
    if spec is PART:
        shape = value.get("shape") if isinstance(value, dict) else None
        if not isinstance(shape, str) or shape not in PART_SCHEMAS:
            raise ValueError(f"{path} must use a supported geometry shape.")
        _validate(value, PART_SCHEMAS[shape], path)
        return
    if "anyOf" in spec:
        if value is not None:
            _validate(value, spec["anyOf"][0], path)
        return
    kind = spec["type"]
    if kind == "object":
        fields = spec["properties"]
        if not isinstance(value, dict) or not value.keys() <= fields.keys():
            raise ValueError(f"{path} must be an object with valid fields.")
        missing = set(spec["required"]) - value.keys()
        if missing:
            raise ValueError(f"{path} is missing required fields ({', '.join(sorted(missing))}).")
        for key, item in value.items():
            _validate(item, fields[key], f"{path}.{key}")
    elif kind == "array":
        if not isinstance(value, list) or not spec["minItems"] <= len(value) <= spec.get("maxItems", math.inf):
            raise ValueError(f"{path} must be a list of the required length.")
        for index, item in enumerate(value):
            _validate(item, spec["items"], f"{path}[{index}]")
    elif kind in ("number", "integer"):
        types = (int,) if kind == "integer" else (int, float)
        if type(value) not in types or not spec["minimum"] <= value <= spec["maximum"]:
            label = "integer" if kind == "integer" else "number"
            raise ValueError(f"{path} must be a {label} between {spec['minimum']} and {spec['maximum']}.")
    elif kind == "string":
        if (not isinstance(value, str) or not value.strip() or "\x00" in value
                or len(value) > spec.get("maxLength", math.inf)
                or ("pattern" in spec and not ID_PATTERN.fullmatch(value))
                or ("enum" in spec and value not in spec["enum"])):
            raise ValueError(f"{path} contains invalid text.")
    elif kind == "boolean" and type(value) is not bool:
        raise ValueError(f"{path} must be true or false.")


def _validate_elements(elements):
    identifiers = set()
    piece_count = vertex_count = index_count = 0
    for element in elements:
        asset_id = element["id"]
        if asset_id in identifiers:
            raise ValueError(f"Duplicate object ID {asset_id}.")
        identifiers.add(asset_id)
        parts = element.get("parts", [])
        lights = element.get("lights", [])
        pieces = len(parts) + len(lights)
        if not 1 <= pieces <= MAX_PARTS:
            raise ValueError(f"Object {asset_id} must have between 1 and {MAX_PARTS} parts or lights.")
        piece_count += pieces
        for part in parts:
            if part["shape"] == "mesh":
                vertices = len(part["vertices"])
                vertex_count += vertices
                for face in part["faces"]:
                    if len(set(face)) != len(face) or max(face) >= vertices:
                        raise ValueError(f"A mesh face in object {asset_id} references invalid vertices.")
                    index_count += len(face)
            elif part["shape"] == "extrusion":
                profile = [tuple(point) for point in part["profile"]]
                if len(set(profile)) != len(profile):
                    raise ValueError(f"An extrusion profile in object {asset_id} contains duplicate vertices.")
            if part.get("walkable", False) and not part.get("collision", True):
                raise ValueError(f"The walkable surfaces of object {asset_id} require collision detection.")
    if (piece_count > MAX_WORLD_PARTS or vertex_count > MAX_WORLD_MESH_VERTICES
            or index_count > MAX_WORLD_MESH_INDICES):
        raise ValueError("The scene exceeds the real-time geometry limit. Reduce the number of parts or mesh vertices.")


def validate_design(design):
    _validate(design, DESIGN, "World")
    _validate_elements(design["elements"])
    return design


def apply_operation(current, name, arguments):
    if name == "replace_scene_design":
        return deepcopy(validate_design(arguments))
    if name != "edit_scene_design":
        raise ValueError("Unknown Blender edit operation.")
    _validate(arguments, EDIT, "Design edit")
    upserts = arguments.get("upsert", [])
    _validate_elements(upserts)
    removed = set(arguments.get("remove", []))
    elements = {item["id"]: item for item in current["elements"] if item["id"] not in removed}
    elements.update((item["id"], item) for item in upserts)
    updated = {**current, **{key: arguments[key] for key in SITE if key in arguments},
               "elements": list(elements.values())}
    return deepcopy(validate_design(updated))
