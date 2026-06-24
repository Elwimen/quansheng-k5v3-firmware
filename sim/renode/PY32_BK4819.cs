//
// BK4819 RF transceiver, as driven by the UV-K5 firmware over a 3-wire
// bit-banged interface (App/driver/bk4829.c):
//
//   CSN = GPIOF pin 9 (active low)   -> OnGPIO input 0
//   SCL = GPIOB pin 8 (clock)        -> OnGPIO input 1
//   SDA = GPIOB pin 9 (bidirectional)-> OnGPIO input 2 (MCU->chip)
//   SdaOut                            -> back to GPIOB pin 9 (chip->MCU)
//
// Frame: CS low; then an 8-bit command MSB-first where bit7 is R/W (1=read,
// 0=write) and bits6..0 are the register index; then 16 bits of data MSB-first.
// On a write the MCU drives SDA and we sample on each SCL rising edge. On a read
// the MCU releases SDA and reads a bit *before* each SCL pulse, so we present the
// MSB as soon as the address phase ends and advance on each rising edge.
//
using Antmicro.Renode.Core;
using Antmicro.Renode.Peripherals;
using Antmicro.Renode.Logging;

namespace Antmicro.Renode.Peripherals.Miscellaneous
{
    public class PY32_BK4819 : IGPIOReceiver, IPeripheral
    {
        public PY32_BK4819()
        {
            SdaOut = new GPIO();
            registers = new ushort[128];
            Reset();
        }

        // Chip -> MCU data line; wire to the SDA GPIO input (GPIOB pin 9).
        public GPIO SdaOut { get; }

        public void Reset()
        {
            csn = true;
            scl = false;
            sdaIn = false;
            phase = Phase.Idle;
            bitCount = 0;
            shifter = 0;
            address = 0;
            outValue = 0;
            for(var i = 0; i < registers.Length; i++)
            {
                registers[i] = 0;
            }
            SdaOut.Set(false);
        }

        public void OnGPIO(int number, bool value)
        {
            switch(number)
            {
            case PinCsn:
                HandleCsn(value);
                break;
            case PinScl:
                HandleScl(value);
                break;
            case PinSda:
                sdaIn = value;
                break;
            default:
                this.Log(LogLevel.Warning, "Unexpected GPIO {0}", number);
                break;
            }
        }

        // Direct register access for host scripting (RSSI/status scenarios).
        public ushort ReadRegister(int index)
        {
            return registers[index & 0x7F];
        }

        public void WriteRegister(int index, ushort value)
        {
            registers[index & 0x7F] = value;
        }

        private void HandleCsn(bool value)
        {
            if(value == csn)
            {
                return;
            }
            csn = value;
            if(!value)
            {
                // CS asserted (falling edge): begin a new frame, expect the command byte.
                phase = Phase.Address;
                bitCount = 0;
                shifter = 0;
            }
            else
            {
                // CS released: end of frame.
                phase = Phase.Idle;
                SdaOut.Set(false);
            }
        }

        private void HandleScl(bool value)
        {
            var rising = value && !scl;
            scl = value;
            if(!rising)
            {
                return;
            }

            switch(phase)
            {
            case Phase.Address:
                shifter = (ushort)((shifter << 1) | (sdaIn ? 1u : 0u));
                if(++bitCount == 8)
                {
                    address = (byte)(shifter & 0x7F);
                    if((shifter & 0x80) != 0)
                    {
                        // Read: present the register MSB now; the MCU samples before each pulse.
                        phase = Phase.ReadData;
                        outValue = registers[address];
                        bitCount = 0;
                        SdaOut.Set((outValue & 0x8000) != 0);
                    }
                    else
                    {
                        phase = Phase.WriteData;
                        bitCount = 0;
                        shifter = 0;
                    }
                }
                break;

            case Phase.WriteData:
                shifter = (ushort)((shifter << 1) | (sdaIn ? 1u : 0u));
                if(++bitCount == 16)
                {
                    registers[address] = shifter;
                    this.Log(LogLevel.Noisy, "REG 0x{0:X2} <= 0x{1:X4}", address, shifter);
                    phase = Phase.Idle;
                }
                break;

            case Phase.ReadData:
                // MCU already sampled the current bit; shift to the next one.
                outValue <<= 1;
                SdaOut.Set((outValue & 0x8000) != 0);
                break;
            }
        }

        private bool csn;
        private bool scl;
        private bool sdaIn;
        private Phase phase;
        private int bitCount;
        private ushort shifter;
        private byte address;
        private ushort outValue;
        private readonly ushort[] registers;

        private const int PinCsn = 0;
        private const int PinScl = 1;
        private const int PinSda = 2;

        private enum Phase
        {
            Idle,
            Address,
            WriteData,
            ReadData,
        }
    }
}
