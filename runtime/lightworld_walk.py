"""Player navigation, camera placement, and scene loading in Blenderplayer."""

import json
import math
import os
from pathlib import Path
from time import monotonic, sleep

import bge
import bpy
from mathutils import Euler, Vector


EYE_HEIGHT = 1.69
AVATAR_EYE = Vector((0, -0.14, 1.49))
WALK_SPEED = 4.1
BODY_RADIUS = 0.22
BODY_FOOTPRINT = (Vector((0, 0, 0)), *(
    BODY_RADIUS * Vector((math.cos(index * math.tau / 8), math.sin(index * math.tau / 8), 0))
    for index in range(8)
))
HEAD_CLEARANCE = 0.10
BAMBOO_CLEARANCE = 0.34
BAMBOO_GESTURE = "LightWorld Fit Bamboo Copter"
BAMBOO_ACTION_LAYER = 1
BAMBOO_REVEAL_FRAME = 2
BAMBOO_MOUNT_FRAME = 8
BAMBOO_TAKEOFF_FRAME = 10
BAMBOO_FRAME_RATE = 24
BAMBOO_SPIN_RATE = math.tau * 5
FLOOR_CONTACT = 0.02
BODY_CLEARANCE = 0.002
STEP_HEIGHT = 0.50
STEP_SETTLE_RATE = 18
DRAG_THRESHOLD = 3
LOOK_SENSITIVITY = math.radians(0.25)
MAX_LOOK_PITCH = math.radians(85)
CHASE_TILT = math.radians(17)
CHASE_DISTANCE = 4.25
CHASE_PIVOT_DROP = 0.52
CHASE_MIN_DISTANCE = 0.68
CAMERA_RADIUS = 0.12
CAMERA_PADDING = 0.17
CAMERA_RETURN_RATE = 10
WHEEL_SPEED = 4 * WALK_SPEED
WHEEL_STEP = 2.5
MAX_MOVE_STEP = 0.12
EVENT_POLL_INTERVAL = 0.001
IDLE_DRAW_INTERVAL = 0.25
HOVER_DRAW_INTERVAL = 1 / 30
RENDER_SETTLE_TIME = 0.5
POLL_INTERVAL = 0.4
REPORT_INTERVAL = 0.4
WALKABLE = "LightWorldWalkable"
SOLID = "LightWorldSolid"
HANDOFF_KEY = "lightworld_view"
PID = os.getpid()


def _axis(keyboard, positive, negative):
    return int(keyboard[positive].active) - int(keyboard[negative].active)


def _ground_heights(camera, eye, above, below):
    feet = eye - Vector((0, 0, EYE_HEIGHT))
    top = feet + Vector((0, 0, above))
    bottom = feet - Vector((0, 0, below))
    for offset in BODY_FOOTPRINT:
        hit, point, normal = camera.rayCast(bottom + offset, top + offset, 0, WALKABLE, 1, 1)
        if hit is not None and normal.z >= 0.55:
            yield point.z


def _step_target(camera, current, displacement, head_clearance):
    target = current + displacement
    reach = STEP_HEIGHT + 2 * BODY_CLEARANCE
    floor = max(_ground_heights(camera, target, reach, reach), default=None)
    if floor is None:
        return None
    change = floor + EYE_HEIGHT - current.z
    if abs(change) <= BODY_CLEARANCE:
        return None

    # Lift before moving onto a riser and move past the edge before descending.
    if change > 0:
        lift = Vector((0, 0, change))
        if _travel_fraction(camera, current, lift, head_clearance) != 1.0:
            return None
        current = current + lift
        target += lift
    if _travel_fraction(camera, current, displacement, head_clearance) != 1.0:
        return None
    if change < 0:
        drop = Vector((0, 0, change + BODY_CLEARANCE))
        if _travel_fraction(camera, target, drop, head_clearance) != 1.0:
            return None
        target += drop
    return target


def _travel_fraction(camera, current, displacement, head_clearance=HEAD_CLEARANCE):
    distance = displacement.length
    if not distance:
        return 1.0

    fraction = _sweep_body(camera, current, displacement, distance, BODY_FOOTPRINT, head_clearance)
    if fraction or not _body_overlaps(camera, current, BODY_FOOTPRINT, head_clearance):
        return fraction

    # A scene edit or a tight corner can leave the outside of the body overlapping.
    # Let it move with the largest clear footprint until the full radius fits again.
    clear = BODY_FOOTPRINT[:1]
    if _body_overlaps(camera, current, clear, head_clearance):
        return 0.0
    low, high = 0.0, 1.0
    for _ in range(8):
        scale = (low + high) / 2
        footprint = tuple(offset * scale for offset in BODY_FOOTPRINT)
        if _body_overlaps(camera, current, footprint, head_clearance):
            high = scale
        else:
            low, clear = scale, footprint
    return _sweep_body(camera, current, displacement, distance, clear, head_clearance)


def _body_overlaps(camera, eye, footprint, head_clearance):
    feet = eye + Vector((0, 0, FLOOR_CONTACT - EYE_HEIGHT))
    head = eye + Vector((0, 0, head_clearance))
    return any(camera.rayCast(head + offset, feet + offset, 0, flag, 0, 1)[0] is not None
               for offset in footprint for flag in (SOLID, WALKABLE))


def _sweep_body(camera, current, displacement, distance, footprint, head_clearance):
    # Sweep the feet, torso and head to find the first contact along the path.
    bottom = -EYE_HEIGHT if displacement.z < 0 else -EYE_HEIGHT + FLOOR_CONTACT
    fraction = 1.0
    for offset in footprint:
        for height in (bottom, -EYE_HEIGHT / 2, head_clearance):
            start = current + offset
            start.z += height
            for flag in (SOLID, WALKABLE):
                end = start + displacement * fraction
                hit, point, _ = camera.rayCast(end, start, 0, flag, 0, 1)
                if hit is not None:
                    clear = ((point - start).length - BODY_CLEARANCE) / distance
                    fraction = max(0.0, min(fraction, clear))
                    if not fraction:
                        return 0.0

    target = current + displacement * fraction
    return 0.0 if _body_overlaps(camera, target, footprint, head_clearance) else fraction


def _bamboo_has_room(camera, eye):
    start = eye + Vector((0, 0, HEAD_CLEARANCE))
    end = eye + Vector((0, 0, BAMBOO_CLEARANCE + FLOOR_CONTACT))
    return all(camera.rayCast(end + offset, start + offset, 0, flag, 0, 1)[0] is None
               for offset in BODY_FOOTPRINT for flag in (SOLID, WALKABLE))


def _clear_camera_distance(camera, pivot, rotation):
    back = rotation.col[2]
    side = rotation.col[0] * CAMERA_RADIUS
    up = rotation.col[1] * CAMERA_RADIUS
    distance = CHASE_DISTANCE
    # Leave room for the near plane when the camera approaches edges or corners.
    for offset in (Vector((0, 0, 0)), side, -side, up, -up):
        origin = pivot + offset
        for flag in (SOLID, WALKABLE):
            hit, point, _ = camera.rayCast(origin + back * distance, origin, 0, flag, 0, 1)
            if hit is not None:
                distance = max(0.0, (point - origin).length - CAMERA_PADDING)
                if distance == 0:
                    return 0.0
    return distance


class BambooCopter:
    def __init__(self, rig, scene, collection, scale, gesture):
        self.rig = rig
        source = collection.objects["LightWorld Avatar Rig"]
        bones = source.data.bones
        self.seat = bones["Head"].matrix_local.inverted() @ bones["Bamboo Grip"].matrix_local
        self.animation = source.animation_data
        self.gesture = gesture
        self.hub = Vector((0, 0, collection["lightworld_bamboo_hub_height"]))
        self.mount = scene.objects["LightWorld Bamboo Mount"]
        self.blades = scene.objects["LightWorld Bamboo Blades"]
        for obj in (self.mount, self.blades):
            obj.worldScale = (scale, scale, scale)
            obj.visible = False
        self.end_frame = gesture.frame_range[1]
        self.frame = 0.0
        self.powered = False
        self.angular_speed = 0.0
        self.angle = 0.0

    @property
    def mounted(self):
        return self.frame >= BAMBOO_MOUNT_FRAME

    def advance(self, delta, powered):
        self.powered = powered
        change = BAMBOO_FRAME_RATE * delta
        self.frame = min(self.end_frame, self.frame + change) if powered else max(0.0, self.frame - change)

    def update(self, delta, show_head, base_action):
        layer = BAMBOO_ACTION_LAYER
        gesturing = 0 < self.frame < self.end_frame
        if not gesturing and self.rig.isPlayingAction(layer):
            self.rig.stopAction(layer)

        # Keep an action reference for UPBGE's layer cleanup. An unbound slot leaves
        # pose evaluation to the game engine, which blends from the previous pose.
        action = self.gesture if gesturing else base_action
        if self.animation.action != action:
            self.animation.action = action
        if self.animation.action_slot is not None:
            self.animation.action_slot = None
        if gesturing:
            if not self.rig.isPlayingAction(layer):
                # Keep the layer running while takeoff and stowing sample it in either direction.
                self.rig.playAction(BAMBOO_GESTURE, 0, self.end_frame + 1, layer=layer, speed=0.05,
                                    play_mode=bge.logic.KX_ACTION_MODE_LOOP)
            self.rig.setActionFrame(self.frame, layer)

        if self.mounted:
            target = BAMBOO_SPIN_RATE if self.powered else 0.0
            self.angular_speed += (target - self.angular_speed) * (1 - math.exp(-12 * delta))
            self.angle = math.remainder(self.angle + self.angular_speed * delta, math.tau)
        else:
            self.angular_speed = self.angle = 0.0

        visible = show_head and self.frame >= BAMBOO_REVEAL_FRAME
        for obj in (self.mount, self.blades):
            if obj.visible != visible:
                obj.visible = visible
        if not visible:
            return
        channels = self.rig.channels
        anchor = (channels["Head"].pose_matrix @ self.seat if self.mounted
                  else channels["Bamboo Grip"].pose_matrix)
        transform = self.rig.worldTransform @ anchor
        rotation = transform.to_3x3().normalized()
        self.mount.worldPosition = transform.translation
        self.mount.worldOrientation = rotation
        self.blades.worldPosition = transform @ self.hub
        self.blades.worldOrientation = rotation @ Euler((0, 0, self.angle), "XYZ").to_matrix()


class PlayerAvatar:
    def __init__(self, scene, path):
        with bpy.data.libraries.load(path) as (_, asset):
            asset.collections = ["LightWorld Avatar"]
            poses = ("Stand", "Walk", "Run", "Hover", "Step Left", "Step Right")
            asset.actions = [f"LightWorld {pose}" for pose in poses] + [BAMBOO_GESTURE]

        collection = asset.collections[0]
        bpy.context.scene.collection.children.link(collection)
        self.actions = {action.name: action for action in asset.actions}
        for action in asset.actions:
            scene.convertBlenderAction(action)
        scene.convertBlenderCollection(collection)

        self.rig = scene.objects["LightWorld Avatar Rig"]
        self.head = scene.objects["LightWorld Avatar Head"]
        # UPBGE requires explicit parenting for objects converted at runtime.
        scene.objects["LightWorld Avatar Body"].setParent(self.rig)
        self.head.setParent(self.rig)
        scale = EYE_HEIGHT / AVATAR_EYE.z
        self.rig.worldScale = (scale, scale, scale)
        self.eye_offset = AVATAR_EYE * scale
        self.copter = BambooCopter(self.rig, scene, collection, scale, self.actions[BAMBOO_GESTURE])
        self.playing = None
        self.pose_origin = None

    def follow(self, eye, yaw, pose, backwards, delta):
        origin = (*eye, yaw)
        if origin != self.pose_origin:
            # The asset faces -Y and the camera faces +Y at zero yaw.
            heading = Euler((0, 0, yaw + math.pi), "XYZ").to_matrix()
            self.rig.worldOrientation = heading
            self.rig.worldPosition = eye - heading @ self.eye_offset
            self.pose_origin = origin

        animation = (pose, backwards)
        action = self.actions[f"LightWorld {pose}"]
        if animation != self.playing:
            start, end = action.frame_range
            if backwards:
                start, end = end, start
            self.rig.playAction(action.name, start, end, blendin=4 if self.playing else 0,
                                play_mode=bge.logic.KX_ACTION_MODE_LOOP)
            self.playing = animation
        self.copter.update(delta, self.head.visible, action)


class WalkSession:
    def __init__(self, camera):
        self.camera = camera
        state_dir = os.environ.get("LIGHTWORLD_STATE_DIR")
        self.state_dir = Path(state_dir).resolve() if state_dir else None
        self.movement_file = self.state_dir / "movement.json" if self.state_dir else None
        self.blend = Path(bpy.data.filepath).resolve()
        self.target_blend = None
        self.reported_snapshot = None
        self.frame_presented = False
        self.status = "ready"
        self.load_message = None
        self.movement_signature = None
        self.paused = False
        self.typing_session = None
        self.canvas_click_session = None
        self.pointer_position = None
        self.dragging = False
        self.wheel_distance = 0.0
        self.step_offset = 0.0
        self.ground_contact = None
        self.chase_target = None
        self.camera_pose = camera.worldPosition.copy(), camera.worldOrientation.copy()
        self.last_tick = None
        self.last_poll = self.last_report = -math.inf
        self.draw_at_full_rate_until = 0.0
        self.next_draw = 0.0
        self.viewport_size = None
        bge.logic.setRender(True)

        handoff = bge.logic.globalDict.pop(HANDOFF_KEY, {})
        if handoff.get("blend_path") != str(self.blend):
            handoff = {}
        self.third_person = handoff.get("third_person", True)
        self.chase_distance = handoff.get("chase_distance")
        self.view_toggle_count = handoff.get("view_toggle_count", 0)
        self.requested_view_toggle_count = self.view_toggle_count
        if "position" in handoff:
            self.position = Vector(handoff["position"])
            self.yaw = handoff["yaw"]
            self.pitch = handoff["pitch"]
        else:
            self.position = camera.worldPosition.copy()
            forward = camera.worldOrientation @ Vector((0, 0, -1))
            self.yaw = math.atan2(-forward.x, forward.y)
            self.pitch = 0.0
        camera.scene.active_camera = camera
        avatar_path = os.environ.get("LIGHTWORLD_AVATAR")
        self.avatar = PlayerAvatar(camera.scene, avatar_path) if avatar_path else None
        self._update_camera(0)
        if self.avatar is not None:
            self._pose_avatar(self.position, 0)
        bge.render.showMouse(True)
        self._sync_movement()

    def step(self):
        now = monotonic()
        delta = 0 if self.last_tick is None else min(now - self.last_tick, 0.06)
        self.last_tick = now

        movement_changed = self._sync_movement()
        events = bge.events
        mouse = bge.logic.mouse.inputs
        button = mouse[events.LEFTMOUSE]
        mouse_clicked = self.typing_session is not None and button.activated
        if mouse_clicked:
            self.canvas_click_session = self.typing_session
        before = self.position
        copter = self.avatar.copter if self.avatar is not None else None
        copter_frame = copter.frame if copter is not None else 0
        view_changed = camera_moved = False
        navigating = not self.paused and self.status == "ready"
        if not navigating:
            self.pointer_position = None
            self.dragging = False
            self.wheel_distance = 0.0
        else:
            view_changed = self._switch_view_if_requested()
            self._look(mouse, button)
            wheel = int(mouse[events.WHEELUPMOUSE].activated) - int(mouse[events.WHEELDOWNMOUSE].activated)
            self._walk(delta, wheel)
        if navigating or self.step_offset:
            self._settle_step(before, delta)
            camera_moved = self._update_camera(delta)
        if self.avatar is not None:
            self._pose_avatar(before, delta)
        copter_moved = copter is not None and copter.frame != copter_frame
        active = button.activated or view_changed or camera_moved or self.dragging or self.position != before or copter_moved
        self._schedule_draw(now, active, copter)
        if movement_changed or view_changed or mouse_clicked or now - self.last_report >= REPORT_INTERVAL:
            self._report()
            self.last_report = now
        if now - self.last_poll >= POLL_INTERVAL:
            self._poll_scene()
            self.last_poll = now

    def _schedule_draw(self, now, active, copter):
        size = bge.render.getWindowWidth(), bge.render.getWindowHeight()
        if active or size != self.viewport_size:
            self.draw_at_full_rate_until = now + RENDER_SETTLE_TIME
            self.viewport_size = size

        draw = now < self.draw_at_full_rate_until or now >= self.next_draw
        if draw:
            spinning = copter is not None and copter.blades.visible and not self.paused
            self.next_draw = now + (HOVER_DRAW_INTERVAL if spinning else IDLE_DRAW_INTERVAL)
        bge.logic.setRender(draw)

    def _switch_view_if_requested(self):
        keyboard_switch = bge.logic.keyboard.inputs[bge.events.VKEY].activated
        button_switches = self.requested_view_toggle_count - self.view_toggle_count
        self.view_toggle_count = self.requested_view_toggle_count
        if (button_switches + int(keyboard_switch)) % 2 == 0:
            return False
        self.third_person = not self.third_person
        self.chase_distance = None
        return True

    def _camera_rotation(self, pitch):
        return Euler((math.pi / 2 + pitch, 0, self.yaw), "XYZ").to_matrix()

    def _view_eye(self):
        if self.step_offset:
            return self.position + Vector((0, 0, self.step_offset))
        return self.position

    def _grounded(self):
        # Scene colliders stay fixed until the next world loads.
        if self.ground_contact is None or self.ground_contact[0] != self.position:
            heights = _ground_heights(self.camera, self.position, FLOOR_CONTACT, FLOOR_CONTACT)
            self.ground_contact = self.position.copy(), next(heights, None) is not None
        return self.ground_contact[1]

    def _settle_step(self, before, delta):
        if not self.step_offset:
            return
        travel = (self.position - before).xy.length
        # Catch up faster when scrolling carries the avatar across several treads.
        progress = max(STEP_SETTLE_RATE * delta, travel / BODY_RADIUS)
        offset = self.step_offset * math.exp(-progress)
        offset = max(-STEP_HEIGHT, min(STEP_HEIGHT, offset))
        if offset > 0:
            # A descending avatar may move under a ceiling while its visual height settles.
            lift = Vector((0, 0, offset))
            offset *= _travel_fraction(self.camera, self.position, lift, self._head_clearance())
        self.step_offset = offset if abs(offset) > 0.001 else 0.0

    def _chase_view(self, eye, delta):
        view = (*eye, self.yaw, self.pitch)
        if self.chase_target is None or self.chase_target[0] != view:
            # Tilt the default framing down toward the character.
            pitch = self.pitch - CHASE_TILT * math.cos(self.pitch)
            rotation = self._camera_rotation(pitch)
            pivot = eye - Vector((0, 0, CHASE_PIVOT_DROP))
            distance = _clear_camera_distance(self.camera, pivot, rotation)
            self.chase_target = view, pivot, rotation, distance

        _, pivot, rotation, distance = self.chase_target
        if self.chase_distance is None or distance < self.chase_distance:
            self.chase_distance = distance
        elif distance > self.chase_distance:
            fraction = 1 - math.exp(-CAMERA_RETURN_RATE * delta)
            self.chase_distance += (distance - self.chase_distance) * fraction
        if self.chase_distance < CHASE_MIN_DISTANCE:
            return None
        return pivot + rotation.col[2] * self.chase_distance, rotation

    def _update_camera(self, delta):
        eye = self._view_eye()
        chase = self._chase_view(eye, delta) if self.third_person else None
        position, rotation = chase if chase is not None else (eye, self._camera_rotation(self.pitch))
        previous_position, previous_rotation = self.camera_pose
        travel_squared = (position - previous_position).length_squared
        if travel_squared:
            self.camera.worldPosition = position
            previous_position = position.copy()
        if rotation != previous_rotation:
            self.camera.worldOrientation = rotation
            previous_rotation = rotation
        self.camera_pose = previous_position, previous_rotation
        if self.avatar is not None and self.avatar.head.visible != (chase is not None):
            self.avatar.head.visible = chase is not None
        return travel_squared > 1e-6

    def _pose_avatar(self, before, delta):
        eye = self.position
        travel = (eye - before).xy
        backwards = False
        copter = self.avatar.copter
        if not self._grounded():
            pose = "Hover"
            if not copter.mounted and _bamboo_has_room(self.camera, eye):
                copter.frame = copter.end_frame
                copter.powered = True
        elif 0 < delta < 0.001 and not self.paused:
            # Catch-up ticks can be too short to infer movement from displacement.
            pose, backwards = self.avatar.playing
        elif travel.length_squared < 1e-6:
            pose = "Stand"
        else:
            sine, cosine = math.sin(self.yaw), math.cos(self.yaw)
            along = travel.y * cosine - travel.x * sine
            across = travel.x * cosine + travel.y * sine
            if abs(across) > abs(along) * 1.1:
                pose = "Step Right" if across > 0 else "Step Left"
            else:
                pose = "Run" if travel.length > 1.1 * WALK_SPEED * delta else "Walk"
                backwards = along < -0.001
        self.avatar.follow(self._view_eye(), self.yaw, pose, backwards, 0 if self.paused else delta)

    def _look(self, mouse, button):
        if button.released or not (button.activated or button.active):
            self.pointer_position = None
            self.dragging = False
            return
        events = bge.events
        x = mouse[events.MOUSEX].values[-1]
        y = mouse[events.MOUSEY].values[-1]
        if button.activated:
            self.pointer_position = (x, y)
            self.dragging = False
            return
        if self.pointer_position is None:
            return
        dx = x - self.pointer_position[0]
        dy = y - self.pointer_position[1]
        if not self.dragging:
            distance = math.hypot(dx, dy)
            if distance <= DRAG_THRESHOLD:
                return
            # The first few pixels distinguish a click from a drag.
            fraction = (distance - DRAG_THRESHOLD) / distance
            dx *= fraction
            dy *= fraction
            self.dragging = True
        self.pointer_position = (x, y)
        if dx or dy:
            self.yaw = math.remainder(self.yaw + dx * LOOK_SENSITIVITY, math.tau)
            self.pitch = max(-MAX_LOOK_PITCH, min(MAX_LOOK_PITCH, self.pitch + dy * LOOK_SENSITIVITY))

    def _walk(self, delta, wheel=0):
        events = bge.events
        keyboard = bge.logic.keyboard.inputs
        forward = _axis(keyboard, events.WKEY, events.SKEY)
        right = _axis(keyboard, events.DKEY, events.AKEY)
        vertical = bool(forward) and (keyboard[events.LEFTSHIFTKEY].active or keyboard[events.RIGHTSHIFTKEY].active)
        ready_to_rise = self._prepare_flight(delta, vertical and forward > 0)
        if forward or right:
            self.wheel_distance = 0.0
            if delta:
                step = WALK_SPEED * delta
                if vertical:
                    if forward < 0 or ready_to_rise:
                        self._move(forward * step, 0, vertical=True)
                else:
                    step /= math.hypot(forward, right)
                    self._move_horizontal(forward * step, right * step)
            return

        if wheel:
            if wheel * self.wheel_distance < 0:
                self.wheel_distance = 0.0
            pending = self.wheel_distance + wheel * WHEEL_STEP
            self.wheel_distance = max(-2 * WHEEL_STEP, min(2 * WHEEL_STEP, pending))
        if not self.wheel_distance or not delta:
            return

        travel = math.copysign(min(WHEEL_SPEED * delta, abs(self.wheel_distance)), self.wheel_distance)
        if self._move_horizontal(travel, 0):
            self.wheel_distance -= travel
        else:
            self.wheel_distance = 0.0

    def _move_horizontal(self, forward, right):
        # Check for obstacles between frames as well as at the destination.
        count = math.ceil(math.hypot(forward, right) / MAX_MOVE_STEP)
        for _ in range(count):
            if not self._move(forward / count, right / count):
                return False
        return True

    def _prepare_flight(self, delta, wants_rise):
        if self.avatar is None:
            return True
        copter = self.avatar.copter
        grounded = self._grounded()
        if grounded:
            powered = wants_rise and _bamboo_has_room(self.camera, self.position)
        else:
            powered = copter.mounted or _bamboo_has_room(self.camera, self.position)
        copter.advance(delta, powered)
        return powered and copter.frame >= BAMBOO_TAKEOFF_FRAME

    def _head_clearance(self):
        mounted = self.avatar is not None and self.avatar.copter.mounted
        return BAMBOO_CLEARANCE if mounted else HEAD_CLEARANCE

    def _move(self, forward, right, *, vertical=False):
        head_clearance = self._head_clearance()
        if vertical:
            if forward < 0 and self._grounded():
                return False
            displacement = Vector((0, 0, forward))
        else:
            sine = math.sin(self.yaw)
            cosine = math.cos(self.yaw)
            displacement = Vector((right * cosine - forward * sine,
                                   right * sine + forward * cosine, 0))
            if self._grounded():
                target = _step_target(self.camera, self.position, displacement, head_clearance)
                if target is not None:
                    # Keep the visual height continuous while the collider clears the riser.
                    self.step_offset += self.position.z - target.z
                    self.position = target
                    return True

        fraction = _travel_fraction(self.camera, self.position, displacement, head_clearance)
        if fraction:
            self.position = self.position + displacement * fraction
        return fraction == 1.0

    def _sync_movement(self):
        if self.movement_file is None:
            return False
        try:
            info = self.movement_file.stat()
            signature = (info.st_ino, info.st_mtime_ns, info.st_size)
            if signature == self.movement_signature:
                return False
            control = json.loads(self.movement_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        if not isinstance(control, dict) or not isinstance(control.get("paused"), bool):
            return False

        self.movement_signature = signature
        if control.get("pid") != PID:
            return False
        toggles = control.get("view_toggle_count")
        if type(toggles) is int and toggles >= self.requested_view_toggle_count:
            self.requested_view_toggle_count = toggles
        previous = self.paused
        self.paused = control["paused"]
        session = control.get("typing_session")
        self.typing_session = session if self.paused and isinstance(session, str) else None
        return self.paused != previous

    def _report(self):
        if self.state_dir is None or not self.frame_presented:
            return
        camera = self.camera
        snapshot = {
            "pid": PID,
            "blend_path": str(self.blend),
            "position": list(self.position),
            "camera_position": list(camera.worldPosition),
            "yaw": self.yaw,
            "third_person": self.third_person,
            "view_rotation": list(camera.worldOrientation.to_euler()),
            "view_size": [bge.render.getWindowWidth(), bge.render.getWindowHeight()],
            "status": self.status,
            "paused": self.paused,
        }
        projection = camera.projection_matrix
        if projection[3][2] == -1.0:
            snapshot["projection_scale"] = [projection[0][0], projection[1][1]]
        if self.target_blend is not None:
            snapshot["target_blend_path"] = str(self.target_blend)
        if self.load_message is not None:
            snapshot["message"] = self.load_message
        if self.canvas_click_session is not None:
            snapshot["canvas_click_typing_session"] = self.canvas_click_session

        output = self.state_dir / "player.json"
        try:
            if snapshot == self.reported_snapshot and output.is_file():
                return
            temporary = self.state_dir / f".player-{PID}.json"
            temporary.write_text(json.dumps(snapshot), encoding="utf-8")
            temporary.replace(output)
            self.reported_snapshot = snapshot
        except OSError:
            return

    def _read_publication(self):
        if self.state_dir is None:
            return None
        try:
            record = json.loads((self.state_dir / "scene.json").read_text(encoding="utf-8"))
            blend = (self.state_dir / record["blend_path"]).resolve()
        except (OSError, ValueError, TypeError, KeyError):
            return None
        if blend.parent != self.state_dir / "scenes" or blend.suffix.lower() != ".blend":
            return None
        return blend, record.get("keep_pose") is True

    def _poll_scene(self):
        publication = self._read_publication()
        if publication is None or publication[0] == self.blend:
            if self.status == "load_error":
                self.status = "ready"
                self.load_message = self.target_blend = None
                self._report()
            return

        blend, keep_pose = publication
        if blend == self.target_blend:
            return
        self.target_blend = blend
        if not blend.is_file():
            self._load_failed("The player could not find the scene file.")
            return

        self.status = "loading"
        self.load_message = None
        self._report()
        handoff = {
            "blend_path": str(blend),
            "third_person": self.third_person,
            "view_toggle_count": self.view_toggle_count,
        }
        if keep_pose:
            handoff.update({
                "position": list(self.position),
                "yaw": self.yaw,
                "pitch": self.pitch,
                "chase_distance": self.chase_distance,
            })
        bge.logic.globalDict[HANDOFF_KEY] = handoff
        print("[LightWorld] loading scene", flush=True)
        try:
            bge.logic.startGame(str(blend))
        except (RuntimeError, ValueError, OSError) as error:
            bge.logic.globalDict.pop(HANDOFF_KEY, None)
            print(f"[LightWorld] scene load failed: {error}", flush=True)
            self._load_failed("The player could not load the updated scene.")

    def _load_failed(self, message):
        self.status = "load_error"
        self.load_message = message
        self._report()


_session = None


def update(controller):
    global _session
    if _session is None:
        _session = WalkSession(controller.owner)
    else:
        _session.step()


def run():
    # UPBGE's fixed-rate loop can otherwise keep polling between logic ticks.
    next_frame = bge.logic.NextFrame
    while True:
        started = monotonic()
        if next_frame() is not False:
            return
        # NextFrame submits the rendered buffer before returning to Python.
        if _session is not None and not _session.frame_presented and bge.logic.getRender():
            _session.frame_presented = True
            _session._report()
            print("[LightWorld] scene ready", flush=True)
        remaining = EVENT_POLL_INTERVAL - (monotonic() - started)
        if remaining > 0:
            sleep(remaining)
