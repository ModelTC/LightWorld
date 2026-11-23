# LightWorld

LightWorld is a 3D tool for designing and exploring real-world scenes. Open **LightWorld.app** from this folder to start in a blank world.

- Enter your **API Key**, **Base URL**, and **Model ID** at startup. You can skip setup and still explore, open, and save worlds. Use the gear button or **LightWorld → Model Settings** to enable prompts or change your settings. The settings card opens inside the scene view and pauses navigation until you dismiss it.
- Click the prompt field and describe what you want to create or change in any language. The field highlights while you edit. Click the scene to resume navigation and keep your draft.
- Press **Shift+Return** for a new line. Click **Send** or press **Return** to submit. The **Design Log** shows your original prompt, camera coordinates, the image count for each model request, object changes, and the result in English. The log collapses after a successful update. Click its title to expand or collapse it.
- The default **Third Person** camera follows the character from behind and above. Press **V** or click the view button in the toolbar to switch to **First Person** and back. The follow camera moves closer near walls and ceilings and returns to its usual distance with room to spare.
- Use **W / S** to move forward or backward and **A / D** to move to either side. While grounded, walking follows ramps and steps up or down onto nearby surfaces. Hold the left mouse button and drag, or drag with one finger on a trackpad, to change your heading and look up or down.
- Scroll up or down to move quickly forward or backward, about 2.5 meters per notch. On the ground, scrolling follows ramps and stairs. Hold **Shift+W** to put on the bamboo copter and lift off. Hold **Shift+S** to descend. The character puts the copter away on landing. Scroll with the pointer over the log to read earlier entries.
- Choose **Save World** to save the current world as a `.blend` file. Use **Open World** to edit a working copy of a LightWorld file. Choose **Save World** again to keep any later edits.
- For requests about sightlines, composition, or lighting, face the area you want to change. LightWorld can send Blender renderings from that viewpoint to the model and compare the original and proposed scenes. This workflow requires image input and tool calls through the Responses API.
- If a draft fails review, the model can revise it up to twice using the review findings. LightWorld publishes a draft only after it passes review.

Try “Build a two-story modern house ahead of me, with a walk-in entrance, floor-to-ceiling windows, and stairs to a second-floor terrace.”

LightWorld uses the model to define geometry and meshes and lets you customize materials, lights, ground, and the starting viewpoint. Walkable geometry can form floors, ramps, and stairs. UPBGE Blender builds and checks the scene locally through Blender MCP. LightWorld sends the prompt, relevant scene data, and viewpoint to the configured model provider. View-dependent requests can also include a local rendering.

Session files and logs live in `~/Library/Caches/LightWorld/`. Use **Save World** to keep a copy in a folder you choose.

Use an HTTPS provider that supports the Responses API. LightWorld stores model settings in macOS Keychain and uses your latest settings for the next request. The app uses UPBGE at `/Applications/UPBGE` and a local Blender MCP installation. Run `./build.sh` from this folder to rebuild the app after changing its source.

During development, each launch opens a blank world. Local builds use the current scene format.
