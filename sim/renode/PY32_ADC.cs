//
// PY32F071 ADC (STM32F1-style: SR / CR1 / CR2 / SMPRx / DR @ 0x50).
//
// The UV-K5 firmware only exercises a tiny slice of this peripheral:
//   * BOARD_ADC_Init():       LL_ADC_StartCalibration() sets CR2.CAL (bit 2)
//                             then spins on LL_ADC_IsCalibrationOnGoing()
//                             (reads CR2.CAL) until hardware self-clears it.
//   * BOARD_ADC_GetBatteryInfo(): LL_ADC_REG_StartConversionSWStart() sets
//                             CR2.SWSTART (bit 22), spins on SR.EOC (bit 1,
//                             aliased as LL_ADC_FLAG_EOS), then reads DR (0x50).
//
// So we model: instant calibration (CAL self-clears), a software-triggered
// conversion that immediately sets SR.EOC, and a DR that returns a healthy
// battery reading and clears EOC on read.
//
using Antmicro.Renode.Core;
using Antmicro.Renode.Peripherals;
using Antmicro.Renode.Peripherals.Bus;
using Antmicro.Renode.Logging;

namespace Antmicro.Renode.Peripherals.Analog
{
    public class PY32_ADC : IDoubleWordPeripheral, IKnownSize
    {
        public PY32_ADC(IMachine machine)
        {
            this.machine = machine;
            Reset();
        }

        public void Reset()
        {
            sr = 0;
            cr1 = 0;
            cr2 = 0;
        }

        public uint ReadDoubleWord(long offset)
        {
            switch(offset)
            {
                case SR:
                    return sr;
                case CR1:
                    return cr1;
                case CR2:
                    return cr2;   // CAL already cleared on write => calibration "done"
                case DR:
                    sr &= ~EOC;   // reading data clears end-of-conversion
                    return BatteryRaw & 0x0FFFu;
                default:
                    return 0;
            }
        }

        public void WriteDoubleWord(long offset, uint value)
        {
            switch(offset)
            {
                case SR:
                    // Firmware clears flags by writing the complement mask.
                    sr &= value;
                    break;
                case CR1:
                    cr1 = value;
                    break;
                case CR2:
                    // Calibration completes instantly: never report CAL as ongoing.
                    cr2 = value & ~CAL;
                    // A software-triggered regular conversion finishes instantly.
                    if((value & SWSTART) != 0 || (value & ADON) != 0)
                    {
                        sr |= EOC;
                    }
                    break;
                default:
                    break;
            }
        }

        public long Size => 0x400;

        // ~Full battery. 12-bit right-aligned; the firmware scales it via the
        // EEPROM battery calibration table.
        public uint BatteryRaw { get; set; } = 0x0960;  // 2400

        private uint sr, cr1, cr2;
        private readonly IMachine machine;

        private const long SR  = 0x00;
        private const long CR1 = 0x04;
        private const long CR2 = 0x08;
        private const long DR  = 0x50;

        private const uint EOC     = 1u << 1;   // SR.EOC (LL_ADC_FLAG_EOS)
        private const uint ADON    = 1u << 0;   // CR2.ADON
        private const uint CAL     = 1u << 2;   // CR2.CAL  (self-clearing)
        private const uint SWSTART = 1u << 22;  // CR2.SWSTART
    }
}
