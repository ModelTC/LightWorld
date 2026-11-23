"""Start the installed Blender Lab MCP add-on in UPBGE's background process."""

import importlib.util
from pathlib import Path
import sys


def main():
    sys.dont_write_bytecode = True
    addon = Path(sys.argv[sys.argv.index("--") + 1])
    spec = importlib.util.spec_from_file_location(
        "lightworld_blender_lab_mcp", addon / "__init__.py", submodule_search_locations=[str(addon)]
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    server = module.mcp_to_blender_server
    server.start("127.0.0.1", 0)
    port = server._state.sock.getsockname()[1]
    print(f"LIGHTWORLD_MCP_PORT={port}", flush=True)
    try:
        blocking = importlib.import_module(spec.name + ".execute_blocking")
        blocking.run()
    finally:
        server.stop()


if __name__ == "__main__":
    main()
