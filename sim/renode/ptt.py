# -*- coding: utf-8 -*-
#
# Monitor commands `ptt_press` / `ptt_release`.
#
# PTT is the one key that cannot be injected over the serial protocol: the firmware
# blocks it on purpose (keyboard.c: "PTT release cannot be guaranteed over serial") and
# reads the real pin instead. So transmitting in the simulator means pulling the actual
# line, which is the key matrix's job.
#
#   (monitor) ptt_press
#   (monitor) ptt_release
#
# The model is registered `@ none`, so the monitor cannot reach it by name -- find it by
# walking the machine's peripherals instead.


def _key_matrix():
    # A peripheral registered `@ none` is on no bus, so it does not appear in the
    # machine's registered-peripheral list at all. It is reachable only through its
    # wiring: GPIOB drives the keyboard columns into it, so follow one of those.
    for entry in monitor.Machine.GetRegisteredPeripherals():
        if entry.Name != "gpioPortB":
            continue
        for pair in entry.Peripheral.Connections:
            for endpoint in pair.Value.Endpoints:
                if type(endpoint.Receiver).__name__ == "PY32_KeyMatrix":
                    return endpoint.Receiver
    return None


def _set_ptt(pressed):
    matrix = _key_matrix()
    if matrix is None:
        print("ptt: no PY32_KeyMatrix in this machine")
        return
    matrix.PttPressed = pressed
    print("PTT %s" % ("pressed" if pressed else "released"))


def mc_ptt_press():
    _set_ptt(True)


def mc_ptt_release():
    _set_ptt(False)
