"""Compare two scene designs and check which changes satisfy the request."""

from backend.model import ModelError


SETTING_LABELS = {"title": "World name", "ground": "Ground", "lighting": "Lighting", "spawn": "Spawn point"}
ASSET_ACTIONS = ("added", "updated", "removed")


def summarize(before, after):
    old = {asset["id"]: asset for asset in before["elements"]}
    new = {asset["id"]: asset for asset in after["elements"]}
    return {
        "added": [asset for key, asset in new.items() if key not in old],
        "updated": [{**asset, "previous": old[key]} for key, asset in new.items()
                    if key in old and asset != old[key]],
        "removed": [asset for key, asset in old.items() if key not in new],
        "settings": [{"field": key, "before": before.get(key), "after": after.get(key)}
                     for key in SETTING_LABELS if before.get(key) != after.get(key)],
    }


def require_coverage(changes, requirements):
    if not any(changes.values()):
        raise ModelError("The plan makes no changes to the current world. Specify what to add or change.")
    if not isinstance(requirements, list) or not (1 <= len(requirements) <= 24):
        raise ModelError("The model did not list the requested changes. The design was not published.")
    changed_assets = {asset["id"] for action in ASSET_ACTIONS for asset in changes[action]}
    changed_settings = {item["field"] for item in changes["settings"]}
    covered = []
    for index, requirement in enumerate(requirements, 1):
        if not isinstance(requirement, dict):
            raise ModelError("The model returned an invalid list of requested changes. The design was not published.")
        label = requirement.get("request")
        asset_ids = requirement.get("asset_ids", [])
        fields = requirement.get("settings", [])
        if not isinstance(label, str) or not (0 < len(label.strip()) <= 180):
            raise ModelError(f"Requested change {index} has no valid description. The design was not published.")
        if not all(isinstance(items, list) and all(isinstance(key, str) for key in items)
                   for items in (asset_ids, fields)):
            raise ModelError(f"Requested change {index} has invalid object IDs or settings. The design was not published.")
        if not (asset_ids or fields) or set(asset_ids) - changed_assets or set(fields) - changed_settings:
            raise ModelError(f"The plan does not account for requested change {index}. The design was not published.")
        covered.append([*asset_ids, *(SETTING_LABELS[field] for field in fields)])
    return covered


def summary_message(changes):
    parts = [f"{len(changes[action])} {action}" for action in ASSET_ACTIONS if changes[action]]
    if count := len(changes["settings"]):
        label = "world setting" if count == 1 else "world settings"
        parts.append(f"{count} {label} changed")
    return ", ".join(parts)


def geometry_message(geometry):
    counts = (("Objects", "assets"), ("Meshes", "mesh_objects"), ("Lights", "lights"))
    return "Geometry verified · " + " · ".join(f"{label} {geometry[key]}" for label, key in counts)


def display_changes(changes):
    summary = {
        action: [{"id": item["id"], "position": item["position"]} for item in changes[action]]
        for action in ASSET_ACTIONS
    }
    summary["settings"] = [{"field": item["field"], "label": SETTING_LABELS[item["field"]]}
                           for item in changes["settings"]]
    return summary
