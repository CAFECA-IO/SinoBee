#!/usr/bin/env bash
# Print the current digests of the base images SinoBee builds on, to update the @sha256: pins in Dockerfile,
# docker/sandbox.Dockerfile and EGRESS_IMAGE (sinobee/sandbox/manager.py). Review the release notes first.
set -euo pipefail
for img in python:3.12-slim-bookworm python:3.12-alpine node:24.22.1-bookworm-slim; do
  printf '%-32s %s\n' "$img" "$(docker buildx imagetools inspect "$img" | awk '/^Digest:/{print $2; exit}')"
done
