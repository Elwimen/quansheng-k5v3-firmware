#!/usr/bin/env python3
"""Serve the K5Viewer web app against the simulator instead of a real radio.

The web viewer speaks Web Serial, and Chrome only enumerates real tty devices --
it will never offer the PTY that Renode exposes. This serves the upstream viewer
unmodified (a <script> tag for serial-ws-shim.js is injected on the fly) and
bridges a WebSocket to the PTY, so display streaming and key injection both work.

    renode -e "include @sim/scripts/run.resc; start"    # sim, creates /tmp/ttyUV0
    python3 sim/webviewer/bridge.py                     # then open the printed URL
"""

import argparse
import asyncio
import functools
import http.server
import pathlib
import threading

import serial
import websockets

HERE = pathlib.Path(__file__).resolve().parent
DEFAULT_VIEWER = HERE.parents[2] / "armel.github.io" / "k5viewer"
SHIM_NAME = "__serial_ws_shim.js"
BAUDRATE = 38400


class ViewerHandler(http.server.SimpleHTTPRequestHandler):
    """Serves the upstream viewer, with the Web Serial shim injected into the page."""

    def do_GET(self):
        if self.path.lstrip("/") == SHIM_NAME:
            return self._send(200, "application/javascript",
                              (HERE / "serial-ws-shim.js").read_bytes())

        if self.path in ("/", "/index.html"):
            html = (pathlib.Path(self.directory) / "index.html").read_text()
            tag = '<script src="js/k5viewer.js"></script>'
            html = html.replace(tag, f'<script src="/{SHIM_NAME}"></script>\n    {tag}', 1)
            return self._send(200, "text/html; charset=utf-8", html.encode())

        return super().do_GET()

    def _send(self, code, ctype, body):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        # The shim is regenerated on every request; never let a stale copy stick.
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


async def bridge(ws, port):
    """Pipe one WebSocket <-> the PTY. The serial port allows a single client."""
    loop = asyncio.get_running_loop()
    try:
        ser = serial.Serial(port, BAUDRATE, timeout=0.05)
    except serial.SerialException as e:
        await ws.close(code=1011, reason=f"cannot open {port}: {e}")
        print(f"  ! client rejected: cannot open {port}: {e}")
        return

    print(f"  + viewer connected -> {port}")
    stop = threading.Event()

    def reader():
        # pyserial has no asyncio transport here, so read on a thread and hand
        # each chunk to the loop.
        while not stop.is_set():
            try:
                data = ser.read(256)
            except (serial.SerialException, OSError):
                # Restarting the simulator destroys the PTY under us. Drop the
                # viewer rather than sit on a dead port looking connected; it can
                # reconnect once the sim is back.
                break
            if data:
                asyncio.run_coroutine_threadsafe(ws.send(data), loop)
        asyncio.run_coroutine_threadsafe(ws.close(), loop)

    thread = threading.Thread(target=reader, daemon=True)
    thread.start()
    try:
        async for message in ws:
            if isinstance(message, (bytes, bytearray)):
                try:
                    ser.write(message)
                except (serial.SerialException, OSError):
                    break
    except websockets.ConnectionClosed:
        pass
    finally:
        stop.set()
        thread.join(timeout=1)
        ser.close()
        print("  - viewer disconnected")


async def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", default="/tmp/ttyUV0", help="simulator PTY (default: %(default)s)")
    ap.add_argument("--http-port", type=int, default=8088)
    ap.add_argument("--ws-port", type=int, default=8089)
    ap.add_argument("--viewer", type=pathlib.Path, default=DEFAULT_VIEWER,
                    help="K5Viewer web app directory (default: %(default)s)")
    args = ap.parse_args()

    if not (args.viewer / "index.html").is_file():
        raise SystemExit(f"no K5Viewer at {args.viewer} (pass --viewer)")

    handler = functools.partial(ViewerHandler, directory=str(args.viewer))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", args.http_port), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()

    handler_ws = functools.partial(bridge, port=args.port)
    async with websockets.serve(handler_ws, "127.0.0.1", args.ws_port):
        print(f"K5Viewer:  http://localhost:{args.http_port}/")
        print(f"bridge:    ws://localhost:{args.ws_port}/ -> {args.port}")
        print('Click "Connect" in the page (the port picker is bypassed). Ctrl-C to stop.')
        await asyncio.Future()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
