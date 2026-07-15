// Web Serial shim for the simulator.
//
// Chrome only enumerates real tty devices, so navigator.serial can never see the
// PTY that Renode exposes (/tmp/ttyUV0). This replaces navigator.serial with an
// implementation backed by a WebSocket to bridge.py, which owns the PTY. The
// viewer's own code is unchanged: it still calls requestPort() / open() /
// readable / writable, and cannot tell the difference.
//
// Override the bridge with ?ws=ws://host:port
(function () {
    'use strict';

    const WS_URL = new URLSearchParams(location.search).get('ws')
        || `ws://${location.hostname || 'localhost'}:8089`;

    class WebSocketSerialPort {
        // The viewer shows whatever getInfo() returns; label it as the simulator.
        getInfo() {
            return { usbVendorId: 0x0000, usbProductId: 0x0000, simulator: true };
        }

        async open() {
            const ws = new WebSocket(WS_URL);
            ws.binaryType = 'arraybuffer';

            await new Promise((resolve, reject) => {
                ws.onopen = resolve;
                ws.onerror = () => reject(
                    new Error(`no simulator bridge at ${WS_URL} — is bridge.py running?`));
            });
            this._ws = ws;

            this.readable = new ReadableStream({
                start(controller) {
                    ws.onmessage = (event) => controller.enqueue(new Uint8Array(event.data));
                    ws.onclose = () => {
                        try { controller.close(); } catch (e) { /* already closed */ }
                    };
                },
                cancel() { ws.close(); },
            });

            this.writable = new WritableStream({
                write(chunk) {
                    if (ws.readyState === WebSocket.OPEN) {
                        ws.send(chunk);
                    }
                },
                close() { ws.close(); },
                abort() { ws.close(); },
            });
        }

        async close() {
            if (this._ws) {
                this._ws.close();
                this._ws = null;
            }
        }
    }

    // navigator.serial is a read-only accessor in Chrome, and absent in Firefox.
    Object.defineProperty(navigator, 'serial', {
        configurable: true,
        value: {
            requestPort: async () => new WebSocketSerialPort(),
            getPorts: async () => [],
            addEventListener() {},
            removeEventListener() {},
        },
    });

    console.info(`[sim] navigator.serial is bridged to ${WS_URL}`);
})();
