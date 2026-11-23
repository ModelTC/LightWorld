# LightWorld

LightWorld is a 3D tool for designing and exploring real-world scenes.

## Usage

1. [Download LightWorld](https://github.com/ModelTC/LightWorld/releases), unzip it, and open **LightWorld.app** on an Apple Silicon Mac with macOS 13 or later. The app includes its runtime dependencies.
2. In **Model Settings**, enter your **API Key**, **Base URL**, and **Model ID** for an HTTPS provider that supports the Responses API.
3. Submit a prompt to create or change the scene.
4. Use **Open World** and **Save World** to work with `.blend` files.

You can skip model setup and still explore, open, and save worlds.

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

## Build

Run `./scripts/package-macos.sh` on an Apple Silicon Mac with Xcode to build the download ZIP in `dist/`. GitHub Actions builds, checks, and publishes each push to `main`.

The workflow publishes an ad-hoc-signed preview by default. To publish a notarized release, set the repository Actions secrets `APPLE_CERTIFICATE_BASE64`, `APPLE_CERTIFICATE_PASSWORD`, `APPLE_ID`, `APPLE_APP_SPECIFIC_PASSWORD`, and `APPLE_TEAM_ID`. Use a Developer ID Application certificate and its private key exported as a password-protected PKCS#12 file for the first two secrets.
