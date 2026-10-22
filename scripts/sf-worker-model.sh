#!/usr/bin/env bash
# Project settings for Software Factory worker sessions on template computers.
# A template run starts in ~/.factory/software-factory/workstreams/<slug>, and
# hydration restores only memory/, scripts/ and skills/ there, so the project
# settings that beat the user defaults must be baked into the image. The
# ain3sh-dev template runs this as this repository's setup script; it takes
# effect on the next template build.
#
# sessionDefaultSettings pins every workstream's workers to Opus 5.5. Cloud
# session sync comes from the user settings the template writes; the activity
# coordinator needs it to see a worker's session as alive.
set -euo pipefail
pin() {
  dir="$HOME/.factory/software-factory/workstreams/$1/.factory"
  mkdir -p "$dir"
  printf '{"sessionDefaultSettings":{"model":"%s","reasoningEffort":"high"}}\n' "$2" >"$dir/settings.json"
}
pin pr-shepherd claude-opus-5-5
pin ownership-incident-fixer claude-opus-5-5
pin re-review claude-opus-5-5
