"""Build a LightWorld UPBGE template using UPBGE's Blender executable."""

import argparse
import sys
from math import radians
from pathlib import Path

import bpy

# The background builder reads modules from the signed app bundle.
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bridge.scene_design import write_world
from bridge.schema import DEFAULT_DESIGN


def create_camera(scene):
    camera_data = bpy.data.cameras.new("LightWorld Eye")
    camera_data.lens = 32
    camera_data.clip_start = 0.04
    camera_data.clip_end = 20000
    camera = bpy.data.objects.new("LightWorld Eye", camera_data)
    scene.collection.objects.link(camera)
    scene.camera = camera
    # Blenderplayer selects the scene camera only from a saved camera view.
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == "VIEW_3D":
                space = area.spaces.active
                space.camera = camera
                space.region_3d.view_perspective = "CAMERA"
    return camera


def create_lighting(scene):
    world = bpy.data.worlds.new("LightWorld Sky")
    world.use_nodes = True
    scene.world = world
    sun = bpy.data.lights.new("LightWorld Sun", "SUN")
    sun.angle = radians(5)
    light = bpy.data.objects.new("LightWorld Sun", sun)
    scene.collection.objects.link(light)


def install_runtime(camera):
    for name in ("lightworld_walk.py", "lightworld_main.py"):
        script = Path(__file__).with_name(name)
        module = bpy.data.texts.new(name)
        module.write(script.read_text(encoding="utf-8"))
        module.use_module = False
    bpy.context.scene["__main__"] = "lightworld_main.py"
    bpy.context.view_layer.objects.active = camera
    bpy.ops.logic.sensor_add(type="ALWAYS", name="LightWorld Frame", object=camera.name)
    sensor = camera.game.sensors[-1]
    sensor.use_pulse_true_level = True
    bpy.ops.logic.controller_add(type="PYTHON", name="LightWorld Walk", object=camera.name)
    controller = camera.game.controllers[-1]
    controller.mode = "MODULE"
    controller.module = "lightworld_walk.update"
    controller.link(sensor=sensor)


def build(output):
    scene = bpy.context.scene
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    scene.name = "LightWorld"
    render = scene.render
    render.engine = "BLENDER_EEVEE"
    render.resolution_x = 1280
    render.resolution_y = 800
    render.resolution_percentage = 100
    scene.view_settings.view_transform = "AgX"

    game = scene.game_settings
    game.resolution_x = render.resolution_x
    game.resolution_y = render.resolution_y
    game.show_fullscreen = False
    game.frame_type = "EXTEND"
    game.exit_key = "NONE"
    game.show_mouse = True
    game.use_python_console = False
    game.use_auto_start = False
    game.use_frame_rate = True
    game.use_viewport_render = False

    camera = create_camera(scene)
    create_lighting(scene)
    install_runtime(camera)
    output.parent.mkdir(parents=True, exist_ok=True)
    write_world(output, DEFAULT_DESIGN)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args(sys.argv[sys.argv.index("--") + 1 :])
    build(args.output.resolve())
