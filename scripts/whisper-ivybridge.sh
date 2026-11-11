#!/bin/sh
exec /qemu/qemu-x86_64 -cpu IvyBridge /usr/local/bin/whisper-server "$@"
