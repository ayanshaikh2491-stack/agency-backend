#!/usr/bin/env python
"""Entrypoint: (R2 restore if configured) -> FastAPI on $PORT.

Render (aur koi bhi PaaS) PORT env var deta hai — usse respect karte hain,
default 7860. R2 sync + PocketBase optional hain — bina env ke clean boot.

Local test: python app.py   (PORT unset -> 7860)
Render:      PORT=10000 python app.py
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time

ROOT = os.path.dirname(os.path.abspath(__file__))

# 1) R2 restore (agar config hai) — restart pe ephemeral storage empty hota hai
if os.environ.get("R2_SYNC_BUCKET"):
    try:
        subprocess.run([sys.executable, os.path.join(ROOT, "admin", "hf_sync.py"), "restore"], check=False)
    except Exception:
        pass

# 2) PocketBase background (agar binary + PB_URL config hai — optional)
pb = os.path.join(ROOT, "pocketbase")
if os.path.exists(pb) and os.environ.get("POCKETBASE_URL"):
    try:
        subprocess.Popen([pb, "serve", "--http=127.0.0.1:8090", "--dir=" + os.path.join(ROOT, "pb_data")])
    except Exception:
        pass

# 3) R2 periodic sync background (har 5 min, agar config hai)
if os.environ.get("R2_SYNC_BUCKET") and os.path.exists(os.path.join(ROOT, "admin", "hf_sync.py")):
    def _loop():
        while True:
            time.sleep(300)
            subprocess.run([sys.executable, os.path.join(ROOT, "admin", "hf_sync.py"), "sync"], check=False)
    threading.Thread(target=_loop, daemon=True).start()

# 4) FastAPI — PORT env (Render convention), fallback 7860
if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT") or 7860)
    uvicorn.run("admin.main:app", host="0.0.0.0", port=port)
