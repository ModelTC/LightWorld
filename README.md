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

- Enter your **API Key**, **Base URL**, and **Model ID** at startup. You can skip setup and still explore, open, and save worlds. Use the gear button or **LightWorld → Model Settings** to enable prompts or change your settings. The settings card opens inside the scene view and pauses navigation until you dismiss it.
- Describe what you want to create or change in any language. The prompt field highlights while you edit.
- The **Design Log** shows your original prompt, camera coordinates, the image count for each model request, object changes, and the result in English. The log collapses after a successful update.
- The default **Third Person** camera follows the character from behind and above. The follow camera moves closer near walls and ceilings and returns to its usual distance with room to spare.
- Walking and scrolling follow ramps and stairs while grounded. The character puts on the bamboo copter when taking off and puts it away on landing.
- Choose **Save World** to save the current world as a `.blend` file. Use **Open World** to edit a working copy of a LightWorld file. Choose **Save World** again to keep any later edits.
- For requests about sightlines, composition, or lighting, face the area you want to change. LightWorld can send Blender renderings from that viewpoint to the model and compare the original and proposed scenes. This workflow requires image input and tool calls through the Responses API.
- If a draft fails review, the model can revise it up to twice using the review findings. LightWorld publishes a draft only after it passes review.

Try “Build a two-story modern house ahead of me, with a walk-in entrance, floor-to-ceiling windows, and stairs to a second-floor terrace.”

LightWorld uses the model to define geometry and meshes and lets you customize materials, lights, ground, and the starting viewpoint. Walkable geometry can form floors, ramps, and stairs. UPBGE Blender builds and checks the scene locally through Blender MCP. LightWorld sends the prompt, relevant scene data, and viewpoint to the configured model provider. View-dependent requests can also include a local rendering.

Session files and logs live in `~/Library/Caches/LightWorld/`. Use **Save World** to keep a copy in a folder you choose.

Use an HTTPS provider that supports the Responses API. LightWorld stores model settings in macOS Keychain and uses your latest settings for the next request. The app uses UPBGE at `/Applications/UPBGE` and a local Blender MCP installation. Run `./build.sh` from this folder to rebuild the app after changing its source.

During development, each launch opens a blank world. Local builds use the current scene format.
