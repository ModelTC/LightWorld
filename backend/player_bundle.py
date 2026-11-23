"""Prepare the locally installed renderer with LightWorld's macOS app name."""

from ctypes import CDLL, c_char_p, c_int, c_uint, get_errno
import fcntl
from hashlib import sha256
import os
from pathlib import Path
import plistlib
import subprocess
from tempfile import TemporaryDirectory


PLAYER_NAME = "LightWorld"
PLAYER_ID = "local.lightworld.player"
LIGHTWORLD_VERSION = "0.0.1"
PLAYER_EXECUTABLE = Path("Contents/MacOS/Blenderplayer")
INFO = Path("Contents/Info.plist")


def _fingerprint(path):
    stat = path.stat()
    return [stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns]


def _run(command):
    result = subprocess.run(command, capture_output=True, text=True, timeout=90)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or f"{command[0]} failed.")


def _install(staged, installed):
    if not os.path.lexists(installed):
        staged.rename(installed)
        return
    # Keep a complete bundle at the cache path if two sessions start together.
    exchange = CDLL(None, use_errno=True).renamex_np
    exchange.argtypes = (c_char_p, c_char_p, c_uint)
    exchange.restype = c_int
    if exchange(os.fsencode(staged), os.fsencode(installed), 0x00000002):
        error = get_errno()
        raise OSError(error, os.strerror(error), str(installed))


def prepare_player(source, cache, icon, *, build_date=None):
    source = source.resolve()
    cache.mkdir(parents=True, exist_ok=True)
    bundle = cache / f"{PLAYER_NAME}.app"
    icon_bytes = icon.read_bytes() if icon.is_file() else None
    build_version = f"{LIGHTWORLD_VERSION} {build_date}" if build_date else LIGHTWORLD_VERSION

    with (cache / "prepare.lock").open("a") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        origin = {
            "path": str(source),
            "executable": _fingerprint(source / PLAYER_EXECUTABLE),
            "info": _fingerprint(source / INFO),
            "icon": sha256(icon_bytes).hexdigest() if icon_bytes else "",
        }
        try:
            cached = plistlib.loads((bundle / INFO).read_bytes())
        except (OSError, ValueError, plistlib.InvalidFileException):
            cached = {}
        if (isinstance(cached, dict)
                and cached.get("LightWorldPlayerSource") == origin
                and cached.get("CFBundleIdentifier") == PLAYER_ID
                and cached.get("CFBundleShortVersionString") == LIGHTWORLD_VERSION
                and cached.get("CFBundleVersion") == build_version
                and "CFBundleGetInfoString" not in cached
                and (bundle / PLAYER_EXECUTABLE).is_file()):
            return bundle / PLAYER_EXECUTABLE

        with TemporaryDirectory(prefix=".prepare-", dir=cache) as directory:
            staged = Path(directory) / bundle.name
            staged.mkdir()
            _run(["/bin/cp", "-cR", source / "Contents", staged / "Contents"])
            info = plistlib.loads((staged / INFO).read_bytes())
            info.update(CFBundleName=PLAYER_NAME, CFBundleDisplayName=PLAYER_NAME,
                        CFBundleIdentifier=PLAYER_ID, CFBundleShortVersionString=LIGHTWORLD_VERSION,
                        CFBundleVersion=build_version, LightWorldPlayerSource=origin)
            info.pop("CFBundleGetInfoString", None)
            if icon_bytes:
                info["CFBundleIconFile"] = "LightWorld.icns"
                (staged / "Contents/Resources/LightWorld.icns").write_bytes(icon_bytes)
            (staged / INFO).write_bytes(plistlib.dumps(info))
            _run(["/usr/bin/codesign", "--force", "--sign", "-", "--identifier", PLAYER_ID,
                  "--preserve-metadata=entitlements,flags,runtime", staged])
            _run(["/usr/bin/codesign", "--verify", "--strict", staged])
            _install(staged, bundle)
    return bundle / PLAYER_EXECUTABLE
