#!/bin/zsh
set -euo pipefail

upstream=v0.54-alpha-2026-11-09-5a103f6e16d5
filename=upbge-0.54-alpha-macos-arm64-2026-11-09.dmg
checksum=2b2daa752f9a1701ba1e7ce2de7ec511741a4fe3342b0d96b88ea5309c3a7444
mirror=upbge-runtime

verify_upbge() {
    printf '%s  %s\n' "$checksum" "$1" | shasum -a 256 --check >&2
}

fetch_upbge() {
    local directory=$1 archive="$1/$filename" mode=${2:-}
    local assets=
    mkdir -p "$directory"
    if [[ "$mode" == --mirror ]]; then
        if assets=$(gh release view "$mirror" --json assets --jq '.assets[].name' 2>/dev/null); then
            if grep -Fqx -- "$filename" <<< "$assets"; then
                gh release download "$mirror" --pattern "$filename" --dir "$directory" --clobber
                verify_upbge "$archive"
                print -r -- "$archive"
                return
            fi
        fi
    fi
    curl --fail --location --retry 3 --silent --show-error \
        "https://github.com/languel/upbge-mac-build/releases/download/$upstream/$filename" --output "$archive"
    verify_upbge "$archive"
    # Upstream rotates alpha releases. Preserve the pinned disk image in a private draft.
    if [[ "$mode" == --mirror ]]; then
        if ! gh release view "$mirror" >/dev/null 2>&1; then
            gh release create "$mirror" --draft --title "UPBGE build dependency" \
                --notes "Pinned UPBGE disk images for LightWorld's automated builds." >&2
        fi
        gh release upload "$mirror" "$archive" --clobber >&2
    fi
    print -r -- "$archive"
}

if [[ "${ZSH_EVAL_CONTEXT}" == toplevel ]]; then
    fetch_upbge "$@"
fi
