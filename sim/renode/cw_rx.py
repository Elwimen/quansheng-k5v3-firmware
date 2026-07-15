# -*- coding: utf-8 -*-
#
# Monitor command `cw_rx_send`: key a Morse signal at the radio, as if another station were
# calling. The BK4819 model synthesizes the AF input amplitude (REG_6F) that cw_rx_tick()
# polls, timed on the emulated clock, so the firmware's decoder gets a signal it cannot
# tell from a real one.
#
#   (monitor) cw_rx_send "CQ" 15
#
# The model is registered `@ none`, so the monitor cannot reach it by name; find it by
# following the GPIO wiring, as ptt.py does.


def _bk4819():
    for entry in monitor.Machine.GetRegisteredPeripherals():
        if entry.Name != "gpioPortB":
            continue
        for pair in entry.Peripheral.Connections:
            for endpoint in pair.Value.Endpoints:
                if type(endpoint.Receiver).__name__ == "PY32_BK4819":
                    return endpoint.Receiver
    return None


def mc_cw_rx_send(text, wpm):
    radio = _bk4819()
    if radio is None:
        print("cw_rx_send: no PY32_BK4819 in this machine")
        return
    radio.SendMorse(str(text), int(str(wpm)))
    print("keying '%s' at %s WPM" % (text, wpm))
