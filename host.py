"""
Convenience launcher: starts the server and the client together.

Usage:
    python host.py          # solo mode (control both colours)
    python host.py --port 8765
"""

import argparse
import subprocess
import sys
import time

def main():
    parser = argparse.ArgumentParser(description="Indiscreet Chess host launcher")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    python = sys.executable

    server = subprocess.Popen(
        [python, "-m", "server.main", "--solo", "--port", str(args.port)],
    )
    time.sleep(0.4)   # give server a moment to bind

    try:
        subprocess.run(
            [python, "-m", "client.main", "--solo",
             "--port", str(args.port)],
        )
    finally:
        server.terminate()
        server.wait()

if __name__ == "__main__":
    main()
