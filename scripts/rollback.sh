#!/bin/sh
# Roll the stack back to a previously built image tag, without rebuilding.
#   scripts/rollback.sh <tag>        e.g. scripts/rollback.sh 3f9c2a1
# Images are tagged by IMAGE_TAG at build time:  IMAGE_TAG=$(git rev-parse --short HEAD) docker compose build
# List what is available with:  docker images claimpilot/backend
set -eu
tag="${1:?usage: rollback.sh <image-tag>}"
for image in claimpilot/backend claimpilot/console; do
  docker image inspect "$image:$tag" > /dev/null 2>&1 || { echo "no image $image:$tag" >&2; exit 1; }
done
IMAGE_TAG="$tag" docker compose up -d --no-build api worker console
echo "running $tag"
