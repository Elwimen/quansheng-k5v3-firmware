# UV-K5 V3 (PY32F071) simulator

Runs the **unmodified** firmware under [Renode](https://renode.io) so the display,
keyboard, serial line, RF and storage can be driven from a host — for fast
iteration and AI-driven CI.

## Design rule

The firmware must never know it is running in a simulator. There are **no
`#ifdef SIM` patches** and no simulator-specific build: the simulator runs the
exact `Fusion` binary built from this branch. All hardware fidelity lives in
Renode peripheral models under `sim/renode/`.

## Layout

```
sim/
  platforms/py32f071.repl   Renode platform: Cortex-M0+, RAM/flash, NVIC/SysTick,
                            USART1, and the custom PY32F071 peripheral models.
  scripts/run.resc          Loads models + platform + firmware, opens a UART
                            socket (:3456) and a GDB server (:3333).
  scripts/boottest.resc     Headless smoke test (boots, reports CPU state).
  renode/*.cs               Custom C# peripheral models (compiled at load time).
```

## Build the firmware

```bash
cmake --preset Fusion && cmake --build --preset Fusion -j
# -> build/Fusion/f4hwn.fusion.elf  (the binary the simulator runs)
```

## Run

```bash
renode sim/scripts/run.resc                 # interactive
renode --console --plain sim/scripts/boottest.resc   # headless smoke test
```

## Peripheral model status

| Peripheral | Bus | Model | State |
|---|---|---|---|
| ADC1 (battery) | mmio 0x40012400 | `PY32_ADC.cs` | done — calibration + conversion |
| USART1 | mmio 0x40013800 | Renode `STM32_UART` | done (TX; RX/DMA pending) |
| DMA1 | mmio 0x40020000 | Renode `STM32LDMA` | done (channel-enable transfer + TC IRQ) |
| SPI2 | mmio 0x40003800 | Renode `STM32SPI` | done |
| PY25Q16 flash | SPI2 | Renode `GenericSpiFlash`, file-backed | read+write framed via GPIOA CS; DMA read returns 0s (content correctness pending custom SPI2/DMA) |
| GPIOA | mmio 0x50000000 | Renode `STM32_GPIOPort` | done — PA3 wired to flash CS |
| GPIOB/C/F | mmio 0x50000400+ | stubbed high (keyboard reads "no key") | model with keyboard + BK4819/EEPROM CS later |
| BK4819 radio | bit-bang GPIO PF9/PB8/PB9 | TODO (custom C#) | next — current boot blocker |
| 24Cxx EEPROM / BK1080 | bit-bang I2C PF5/PF6 | TODO (custom + `GenericI2cEeprom`) | pending |

The flash backing image is `sim/data/spi_PY25Q16.bin` (2 MB, blank = 0xFF),
loaded at start and visible on the bus at 0x90000000 for host inspection.

Unmodelled on-chip registers (RCC, FLASH, SPI status, etc.) are stubbed with
`sysbus Tag` in `run.resc` and will be replaced by real models as needed.
