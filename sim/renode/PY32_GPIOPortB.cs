//
// GPIOB, with one addition: while the BK4819 is shifting a register out, reading the port
// gives you the chip's bit on PB9 (SDA).
//
// The firmware bit-bangs the BK4819 on three pins and reads a register by releasing SDA
// and sampling the pin before each clock. Getting that to work through the GPIO machinery
// is a fight we do not need: the port is push-pull, so every SCL_Set/SCL_Reset is a BSRR
// write that rewrites the whole port and wipes whatever the chip drove, and the port's
// OnGPIO re-emits on the same pin, feeding a driven bit straight back into the chip's own
// SDA input. The result was that every register read returned 0xFFFF -- the MCU reading
// its own idle-high line.
//
// Modelling real open-drain arbitration would fix it and is far more fidelity than anyone
// needs here. What the firmware actually requires is much smaller: when it reads the pin
// while the chip is talking, it must see the chip's bit. So take it straight from the
// BK4819 model at read time and leave the electrical story out of it. Writes, the keyboard
// matrix, PTT and everything else go through the stock port untouched.
//
using Antmicro.Renode.Core;
using Antmicro.Renode.Peripherals.Bus;
using Antmicro.Renode.Peripherals.Miscellaneous;

namespace Antmicro.Renode.Peripherals.GPIOPort
{
    public class PY32_GPIOPortB : STM32_GPIOPort, IDoubleWordPeripheral
    {
        public PY32_GPIOPortB(IMachine machine) : base(machine)
        {
        }

        // Set from the platform description; the BK4819 hangs off this port's PB8/PB9.
        public PY32_BK4819 Bk4819 { get; set; }

        public new uint ReadDoubleWord(long offset)
        {
            var value = base.ReadDoubleWord(offset);
            if(offset == InputDataRegister && Bk4819 != null && Bk4819.IsDrivingSda)
            {
                value = Bk4819.SdaLevel
                    ? value | (1u << SdaPin)
                    : value & ~(1u << SdaPin);
            }
            return value;
        }

        private const long InputDataRegister = 0x10;   // GPIOx_IDR
        private const int SdaPin = 9;                  // PB9 = BK4819 SDA
    }
}
