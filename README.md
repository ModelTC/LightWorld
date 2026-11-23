# LightWorld

LightWorld is a 3D tool for designing and exploring real-world scenes. Open **LightWorld.app** from this folder to start in a blank world.

## Controls

| Action | Keyboard | Mouse or trackpad |
| --- | --- | --- |
| Walk forward / backward | `W` / `S` | — |
| Sidestep left / right | `A` / `D` | — |
| Fly up / descend | Hold `Shift+W` / `Shift+S` | — |
| Move quickly forward / backward | — | Scroll up / down over the scene (about 2.5 meters per wheel notch) |
| Turn and look up or down | — | Hold the left mouse button and drag, or click and drag on a trackpad |
| Switch between first and third person | `V` | Click the view button in the toolbar |
| Edit the prompt | — | Click the prompt field |
| Add a line break in the prompt | `Shift+Return` | — |
| Send the prompt | `Return` while editing | Click **Send** |
| Resume navigation and keep the prompt draft | `Esc` while editing | Click the scene |

## Usage

1. Install UPBGE at `/Applications/UPBGE` and set up Blender MCP. Run `./build.sh` and open **LightWorld.app**.
2. In **Model Settings**, enter your **API Key**, **Base URL**, and **Model ID** for an HTTPS provider that supports the Responses API.
3. Submit a prompt to create or change the scene.
4. Use **Open World** and **Save World** to work with `.blend` files.

You can skip model setup and still explore, open, and save worlds.
