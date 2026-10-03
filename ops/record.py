#!/usr/bin/env python3
"""Record the whole GNOME (X11) screen until stopped.  start: ops/record.py <name>   stop: touch ~/Videos/cadence/STOP
GNOME ends a screencast when the caller disconnects, so this process stays alive for the whole take."""
import os, sys, time
import gi
gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib

out_dir = os.path.expanduser("~/Videos/cadence"); os.makedirs(out_dir, exist_ok=True)
name = sys.argv[1] if len(sys.argv) > 1 else "cadence-take"
stop = os.path.join(out_dir, "STOP")
if os.path.exists(stop): os.remove(stop)
bus = Gio.bus_get_sync(Gio.BusType.SESSION)
proxy = Gio.DBusProxy.new_sync(bus, 0, None, "org.gnome.Shell.Screencast", "/org/gnome/Shell/Screencast", "org.gnome.Shell.Screencast")
ok, path = proxy.call_sync("Screencast", GLib.Variant("(sa{sv})", (os.path.join(out_dir, name + ".webm"), {"framerate": GLib.Variant("i", 30)})), 0, -1, None).unpack()
print("recording" if ok else "FAILED", path, flush=True)
t0 = time.time()
while not os.path.exists(stop) and time.time() - t0 < 900:  # 15 min safety cap
    time.sleep(0.5)
proxy.call_sync("StopScreencast", None, 0, -1, None)
os.remove(stop) if os.path.exists(stop) else None
print(f"stopped after {time.time() - t0:.0f}s: {path}", flush=True)
