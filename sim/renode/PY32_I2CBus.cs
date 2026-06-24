//
// Bit-banged I2C bus for the UV-K5, decoded from GPIOF pins:
//   SCL = PF5 -> OnGPIO input 0
//   SDA = PF6 -> OnGPIO input 1 (master -> bus)
//   SdaOut    -> back to PF6 input (bus -> master), open-drain emulation
//
// The firmware (App/driver/i2c.c) bit-bangs standard I2C: START = SDA falls
// while SCL high, STOP = SDA rises while SCL high, data MSB-first sampled on
// SCL rising, slave ACK = SDA pulled low on the 9th clock. Two slaves live on
// this bus:
//   * 24C64 EEPROM at 0xA0/0xA1 (16-bit address, 8 KB) — backed by a file via
//     a MappedMemory, holds calibration + settings.
//   * BK1080 FM receiver at 0x80/0x81 — minimal stub (ACKs, reads 0).
//
using Antmicro.Renode.Core;
using Antmicro.Renode.Peripherals;
using Antmicro.Renode.Peripherals.Memory;
using Antmicro.Renode.Logging;

namespace Antmicro.Renode.Peripherals.Miscellaneous
{
    public class PY32_I2CBus : IGPIOReceiver, IPeripheral
    {
        public PY32_I2CBus(MappedMemory eepromMemory)
        {
            eeprom = eepromMemory;
            SdaOut = new GPIO();
            Reset();
        }

        // Bus -> master data line; wire to the SDA GPIO input (GPIOF pin 9... pin 6).
        public GPIO SdaOut { get; }

        public void Reset()
        {
            scl = true;
            masterSda = true;
            weDriveSda = false;
            suppress = false;
            state = State.Idle;
            bitPos = 0;
            shifter = 0;
            byteRole = ByteRole.Address;
            eeAddr = 0;
            slaveRead = false;
            slaveSelected = false;
            txByte = 0;
            txBit = 0;
            ReleaseSda();
        }

        public void OnGPIO(int number, bool value)
        {
            switch(number)
            {
            case PinScl:
                HandleScl(value);
                break;
            case PinSda:
                HandleSda(value);
                break;
            }
        }

        private void HandleSda(bool value)
        {
            if(suppress)
            {
                return; // our own SdaOut feeding back
            }
            // This is a master-driven event (our own drives are suppressed). A
            // START/STOP is always a bus re-sync point, so detect it regardless
            // of our state — otherwise a mishandled transaction could wedge the
            // decoder forever.
            if(scl)
            {
                if(masterSda && !value)
                {
                    masterSda = value;
                    StartCondition();
                    return;
                }
                if(!masterSda && value)
                {
                    masterSda = value;
                    StopCondition();
                    return;
                }
            }
            masterSda = value;

            if(weDriveSda)
            {
                // Open-drain emulation: STM32_GPIOPort is push-pull, so a master
                // write to SDA while we hold a level would clobber it. Re-assert.
                suppress = true;
                SdaOut.Set(drivenLevel);
                suppress = false;
            }
        }

        private void HandleScl(bool value)
        {
            var rising = value && !scl;
            var falling = !value && scl;
            scl = value;

            if(rising)
            {
                if(state == State.Receiving && bitPos < 8)
                {
                    shifter = (byte)((shifter << 1) | (masterSda ? 1 : 0));
                    if(++bitPos == 8)
                    {
                        ByteReceived();
                    }
                }
                // During the ACK bit (receive) or master-ACK bit (transmit) the
                // master reads/drives; nothing to sample on the rising edge.
            }
            else if(falling)
            {
                switch(state)
                {
                case State.Receiving:
                    if(bitPos == 8)
                    {
                        // Drive ACK low (if we're addressed) for the 9th clock.
                        DriveSda(!ackThisByte);
                        state = State.AckSend;
                    }
                    break;
                case State.AckSend:
                    ReleaseSda();
                    bitPos = 0;
                    shifter = 0;
                    if(slaveSelected && slaveRead)
                    {
                        BeginTransmitByte();
                        state = State.Transmitting;
                    }
                    else
                    {
                        state = State.Receiving;
                    }
                    break;
                case State.Transmitting:
                    if(++txBit < 8)
                    {
                        txByte <<= 1;
                        DriveSda((txByte & 0x80) != 0);
                    }
                    else
                    {
                        // Byte sent; release SDA so the master can ACK/NACK.
                        ReleaseSda();
                        state = State.AckRecv;
                    }
                    break;
                case State.AckRecv:
                    // master drove ACK(low)=more / NACK(high)=stop during the high phase
                    if(!masterSda)
                    {
                        BeginTransmitByte();
                        state = State.Transmitting;
                    }
                    else
                    {
                        state = State.Idle; // NACK: wait for STOP
                    }
                    break;
                }
            }
        }

        private void StartCondition()
        {
            this.Log(LogLevel.Noisy, "START");
            // (repeated) START: next byte is an address.
            state = State.Receiving;
            byteRole = ByteRole.Address;
            bitPos = 0;
            shifter = 0;
            ReleaseSda();
        }

        private void StopCondition()
        {
            state = State.Idle;
            slaveSelected = false;
            ReleaseSda();
        }

        private void ByteReceived()
        {
            switch(byteRole)
            {
            case ByteRole.Address:
                {
                    var sevenBit = (byte)(shifter & 0xFE);
                    slaveRead = (shifter & 1) != 0;
                    this.Log(LogLevel.Noisy, "ADDR byte 0x{0:X2} (slave 0x{1:X2}, {2})", shifter, sevenBit, slaveRead ? "read" : "write");
                    if(sevenBit == EepromAddr)
                    {
                        slaveSelected = true;
                        ackThisByte = true;
                        byteRole = slaveRead ? ByteRole.Data : ByteRole.EeAddrHi;
                    }
                    else if(sevenBit == Bk1080Addr)
                    {
                        slaveSelected = true;
                        ackThisByte = true;
                        byteRole = ByteRole.Data; // BK1080: ignore exact register addressing
                    }
                    else
                    {
                        slaveSelected = false;
                        ackThisByte = false;
                    }
                }
                break;
            case ByteRole.EeAddrHi:
                eeAddr = (ushort)((shifter << 8) | (eeAddr & 0x00FF));
                ackThisByte = true;
                byteRole = ByteRole.EeAddrLo;
                break;
            case ByteRole.EeAddrLo:
                eeAddr = (ushort)((eeAddr & 0xFF00) | shifter);
                ackThisByte = true;
                byteRole = ByteRole.Data;
                break;
            case ByteRole.Data:
                if(slaveSelected && !slaveRead)
                {
                    // EEPROM write (BK1080 writes are dropped).
                    if(eeAddr < EepromSize)
                    {
                        eeprom.WriteByte(eeAddr, shifter);
                    }
                    eeAddr = (ushort)((eeAddr + 1) & (EepromSize - 1));
                }
                ackThisByte = slaveSelected;
                break;
            }
        }

        private void BeginTransmitByte()
        {
            if(slaveSelected && eeAddr < EepromSize && IsEepromRead())
            {
                txByte = eeprom.ReadByte(eeAddr);
                eeAddr = (ushort)((eeAddr + 1) & (EepromSize - 1));
            }
            else
            {
                txByte = 0x00; // BK1080 / unselected
            }
            txBit = 0;
            DriveSda((txByte & 0x80) != 0);
        }

        private bool IsEepromRead()
        {
            return slaveRead; // EepromAddr was matched in ByteReceived
        }

        // Actively present a level on SDA (used for slave ACK and read data).
        private void DriveSda(bool level)
        {
            weDriveSda = true;
            drivenLevel = level;
            suppress = true;
            SdaOut.Set(level);
            suppress = false;
        }

        // Stop driving: let the line float high (pull-up) so the master can drive.
        private void ReleaseSda()
        {
            weDriveSda = false;
            drivenLevel = true;
            suppress = true;
            SdaOut.Set(true);
            suppress = false;
        }

        private bool scl;
        private bool masterSda;
        private bool weDriveSda;
        private bool drivenLevel;
        private bool suppress;
        private State state;
        private int bitPos;
        private byte shifter;
        private ByteRole byteRole;
        private bool ackThisByte;
        private bool slaveSelected;
        private bool slaveRead;
        private ushort eeAddr;
        private byte txByte;
        private int txBit;

        private readonly MappedMemory eeprom;

        private const int PinScl = 0;
        private const int PinSda = 1;
        private const byte EepromAddr = 0xA0;
        private const byte Bk1080Addr = 0x80;
        private const int EepromSize = 0x2000; // 8 KB / 24C64

        private enum State
        {
            Idle,
            Receiving,
            AckSend,
            Transmitting,
            AckRecv,
        }

        private enum ByteRole
        {
            Address,
            EeAddrHi,
            EeAddrLo,
            Data,
        }
    }
}
