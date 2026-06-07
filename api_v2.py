#!/usr/bin/env python3
"""
Jarvis API — v2→v3 Compatibility Shim
=====================================

The original monolithic `api_v2.py` was retired and archived to
`.v2_archive/api_v2.py`. The systemd unit (`jarvis.service`) historically launches
`api_v2.py`, so this shim keeps that entry point working by booting the v3 app —
which already preserves all v2-compatible endpoints — on the same port (8181).

This prevents the crash-loop that occurs when the unit points at a missing file,
WITHOUT requiring a privileged systemd edit. Once `jarvis.service` is repointed
to `api_v3.py` (see `systemd/jarvis.service`), this shim is no longer used and
can be removed.
"""

import os
import sys

# Ensure JARVIS_HOME is importable.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

if __name__ == "__main__":
    import logging

    import uvicorn

    from api_v3 import app, API_HOST, API_PORT

    logging.getLogger("jarvis.api_v3").warning(
        "Booting v3 app via api_v2.py compatibility shim on %s:%s "
        "(repoint jarvis.service to api_v3.py to retire this shim)",
        API_HOST, API_PORT,
    )
    uvicorn.run(app, host=API_HOST, port=API_PORT)
