#!/bin/bash
# Specialty models — delegates to docker/pull-models.sh (includes nomic-embed-text).
exec bash "$(dirname "$0")/docker/pull-models.sh" "$@"
