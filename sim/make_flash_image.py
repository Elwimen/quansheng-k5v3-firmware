#!/usr/bin/env python3
"""Generate the PY25Q16 flash backing image (sim/data/spi_PY25Q16.bin) with
synthesized minimal calibration, so the firmware boots out of gReducedService
and renders normally. Channels/settings stay at firmware defaults.

The settings/calibration store on the UV-K5 V3 is the PY25Q16 SPI flash;
SETTINGS_LoadCalibration() reads gBatteryCalibration (6x uint16) from flash
0x010140. battery.c computes gBatteryVoltageAverage = ADC*760/cal[3], which must
land in ~[630,890] for a non-critical level. With the modelled ADC (~2400),
cal[3] ~ 2280 gives ~800.
"""
import os, struct

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "data", "spi_PY25Q16.bin")
SIZE = 0x200000  # 16 Mbit

img = bytearray(b"\xff" * SIZE)

def put16(addr, value):
    img[addr:addr + 2] = struct.pack("<H", value)

# gBatteryCalibration[0..5] @ flash 0x010140 (logical EEPROM 0x1F40).
for i, v in enumerate((1900, 2000, 2100, 2280, 2400, 2500)):
    put16(0x010140 + 2 * i, v)

# POWER_ON_DISPLAY_MODE = NONE, i.e. settings.c reads it as Data[7] of the block at
# flash 0x00A0A8. It is an ordinary user setting, not a patch -- but it is the single
# biggest cost of a simulated boot: with any other value main.c spins 2.55 simulated
# seconds on the boot screen, polling the keyboard (GPIO) and SysTick, and every one of
# those register reads leaves Renode's translated code for a managed peripheral model.
# Blank flash reads 0xFF, fails settings.c's "< 6" check and falls back to VOLTAGE, so
# an unset image pays the full cost. Skipping it takes a sim boot from ~15s to ~2s.
POWER_ON_DISPLAY_MODE_NONE = 5      # ALL, SOUND, MESSAGE, VOLTAGE, LOGO, NONE
img[0x00A0A8 + 7] = POWER_ON_DISPLAY_MODE_NONE

os.makedirs(os.path.dirname(OUT), exist_ok=True)
with open(OUT, "wb") as f:
    f.write(img)
print("wrote", OUT, SIZE, "bytes with synthesized battery calibration")
