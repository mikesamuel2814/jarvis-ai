#!/bin/bash
# Jarvis v3 Cloudflared Wrapper
# Reads tunnel token from secure file instead of command line.
# Prevents token exposure in 'ps' output.

TOKEN_FILE="/home/kali/.jarvis/config/cloudflared.token"

if [[ ! -f "$TOKEN_FILE" ]]; then
    echo "ERROR: Token file not found: $TOKEN_FILE" >&2
    exit 1
fi

# File must be readable only by owner
PERM=$(stat -c %a "$TOKEN_FILE")
if [[ "$PERM" != "600" && "$PERM" != "400" ]]; then
    echo "WARNING: Token file permissions are $PERM (expected 600 or 400)" >&2
fi

TOKEN=$(cat "$TOKEN_FILE")
if [[ -z "$TOKEN" ]]; then
    echo "ERROR: Token file is empty" >&2
    exit 1
fi

exec /usr/local/bin/cloudflared --no-autoupdate tunnel run --token "$TOKEN"
