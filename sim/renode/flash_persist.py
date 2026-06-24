#
# Monitor commands to persist the PY25Q16 serial-flash image to/from a host file.
#
# The flash content lives in the `spiFlashMem` MappedMemory backing the
# GenericSpiFlash model, also mapped on the bus at 0x90000000. That memory is
# in-RAM, so flash writes by the firmware are NOT written through to disk; call
# `save_flash` on demand / on exit / periodically to snapshot it back to the file.
#
#   (monitor) save_flash @sim/data/spi_PY25Q16.bin
#   (monitor) load_flash @sim/data/spi_PY25Q16.bin
#
# These are registered as Monitor commands because of the `mc_` prefix.
#

FLASH_BASE = 0x90000000
FLASH_SIZE = 0x200000  # 16 Mbit


def mc_save_flash(path):
    import System
    bus = monitor.Machine.SystemBus
    data = bus.ReadBytes(FLASH_BASE, FLASH_SIZE, True)
    System.IO.File.WriteAllBytes(str(path), data)
    print("save_flash: wrote {0} bytes to {1}".format(FLASH_SIZE, path))


def mc_load_flash(path):
    import System
    bus = monitor.Machine.SystemBus
    data = System.IO.File.ReadAllBytes(str(path))
    bus.WriteBytes(data, FLASH_BASE, True)
    print("load_flash: read {0} bytes from {1}".format(len(data), path))
