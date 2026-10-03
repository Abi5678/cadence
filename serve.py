"""Serve the bundled Cadence UI locally using Python 3."""
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from functools import partial
from pathlib import Path
import sys
port = int(sys.argv[1]) if len(sys.argv) > 1 else 8080
handler = partial(SimpleHTTPRequestHandler, directory=str(Path(__file__).resolve().parent))
print(f"Cadence: http://localhost:{port}/web/", flush=True)
try:
    ThreadingHTTPServer(("127.0.0.1", port), handler).serve_forever()
except KeyboardInterrupt:
    print("\nCadence stopped.")
