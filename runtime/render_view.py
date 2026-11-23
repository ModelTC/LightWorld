"""Render a review image of a saved world from a frozen UPBGE camera pose."""

import argparse
import json
from pathlib import Path
import sys

import bpy


def render_view(source, output, view):
    if "FINISHED" not in bpy.ops.wm.open_mainfile(filepath=str(source), load_ui=False, use_scripts=False):
        raise RuntimeError("Could not open the world for preview rendering.")

    scene = bpy.context.scene
    camera = scene.camera
    if camera is None or camera.type != "CAMERA":
        raise RuntimeError("The world has no LightWorld camera.")
    camera.location = view["camera_position"]
    camera.rotation_mode = "XYZ"
    camera.rotation_euler = view["rotation_radians"]

    width, height = view["view_size"]
    scale = 960 / max(width, height)
    render = scene.render
    render.resolution_x = max(32, round(width * scale))
    render.resolution_y = max(32, round(height * scale))
    render.resolution_percentage = 100
    projection = view.get("projection_scale")
    if projection is not None:
        x_scale, y_scale = projection
        # UPBGE's Extend framing can change the field of view when the window is resized.
        camera.data.sensor_fit = "HORIZONTAL"
        camera.data.lens = camera.data.sensor_width * x_scale / 2
        pixel_ratio = y_scale * render.resolution_y / (x_scale * render.resolution_x)
        render.pixel_aspect_x = max(1.0, pixel_ratio)
        render.pixel_aspect_y = max(1.0, 1.0 / pixel_ratio)
    render.film_transparent = False
    render.use_compositing = False
    render.use_sequencer = False
    scene.eevee.taa_render_samples = 16
    render.image_settings.file_format = "JPEG"
    render.image_settings.color_mode = "RGB"
    render.image_settings.quality = 88
    render.filepath = str(output)
    render.use_file_extension = False
    result = bpy.ops.render.render(write_still=True)
    if "FINISHED" not in result or not output.is_file():
        raise RuntimeError("Blender did not write the rendered preview.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("view", type=json.loads)
    args = parser.parse_args(sys.argv[sys.argv.index("--") + 1:])
    render_view(args.source, args.output, args.view)
