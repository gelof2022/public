#!/bin/sh
set -eu

if [ "$(id -u)" = "0" ]; then
    mkdir -p /appdata/frames
    chown appuser:appuser /appdata /appdata/frames
    exec gosu appuser "$@"
fi

exec "$@"
