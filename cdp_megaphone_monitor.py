"""CDP-based megaphone monitor: reads megaphone messages from game's WS traffic via CDP.

Supports monitoring multiple Mafia42 game instances simultaneously (up to 6).
Each instance runs with its own --remote-debugging-port (9222-9227).
Uses the existing megaphone store + webserver for the full frontend.
"""
import json
import sys
import os
import time

os.chdir(r"C:\Users\admin\mafia42_test")
sys.path.insert(0, ".")

from megaphone.multi_cdp import (
    CDP_PORT_END,
    CDP_PORT_START,
    MAX_CLIENTS,
    MultiCDPMonitor,
    load_host_to_channel,
)
from megaphone.store import store
from megaphone.webserver import start_web_server


def detect_active_ports(start: int = CDP_PORT_START, end: int = CDP_PORT_END) -> list[int]:
    """Detect which CDP ports have a game instance running."""
    import urllib.request

    active = []
    for port in range(start, end + 1):
        try:
            url = f"http://127.0.0.1:{port}/json"
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=2) as resp:
                targets = json.loads(resp.read().decode())
            if any(t.get("type") == "page" for t in targets):
                active.append(port)
        except Exception:
            pass
    return active


def main() -> None:
    print("=" * 60, flush=True)
    print("  Mafia42 Megaphone Monitor (Multi-CDP)", flush=True)
    print("=" * 60, flush=True)
    print(
        f"[INFO] Supports up to {MAX_CLIENTS} game instances "
        f"(ports {CDP_PORT_START}-{CDP_PORT_END})",
        flush=True,
    )

    # Load channel host mapping.
    load_host_to_channel()

    # Start web server.
    start_web_server()

    # Detect active game instances.
    print("[INFO] Detecting active game instances...", flush=True)
    active_ports = detect_active_ports()

    if not active_ports:
        print(
            f"[WARN] No game instances found on ports {CDP_PORT_START}-{CDP_PORT_END}.",
            flush=True,
        )
        print("[INFO] Launch Mafia42 with --remote-debugging-port=<port>", flush=True)
        print("[INFO] Waiting for game instances to appear...", flush=True)

        # Poll until at least one instance appears.
        while not active_ports:
            time.sleep(5)
            active_ports = detect_active_ports()

    print(f"[INFO] Found {len(active_ports)} active instance(s): {active_ports}", flush=True)

    # Create multi-CDP monitor with detected ports.
    monitor = MultiCDPMonitor.from_port_range(
        start=min(active_ports),
        end=max(active_ports),
        msg_store=store,
    )

    # Only start clients for ports that are actually active.
    # Remove clients for inactive ports.
    inactive = set(monitor.clients.keys()) - set(active_ports)
    for port in inactive:
        monitor.remove_client(port)

    print(f"[INFO] Starting {len(monitor.clients)} CDP client(s)...", flush=True)
    monitor.start()

    # Keep running and print status periodically.
    try:
        while True:
            time.sleep(30)
            status = monitor.get_status()
            connected = sum(1 for s in status.values() if s["connected"])
            print(
                f"[STATUS] {connected}/{len(status)} clients connected",
                flush=True,
            )
    except KeyboardInterrupt:
        print("\n[INFO] Shutting down...", flush=True)
        monitor.stop()
        store.flush()


if __name__ == "__main__":
    main()
