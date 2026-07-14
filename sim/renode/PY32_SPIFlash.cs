//
// PY32 SPI2 controller + PY25Q16 serial-flash, modelled as one unit so that the
// firmware's flash reads are served faithfully (the stock Renode STM32SPI does
// not model the RX-FIFO level the firmware flushes, nor the DMA read ordering).
//
// Memory-mapped SPI registers at 0x40003800: CR1(0x00) CR2(0x04) SR(0x08) DR(0x0C).
// The chip-select is bit-banged on GPIOA pin 3 -> OnGPIO input 0 (active low):
// every command frame is delimited by CS.
//
// Flash commands used by App/driver/py25q16.c:
//   0x03 read, 0x02 page-program, 0x06 write-enable, 0x20 4 KB sector-erase,
//   0x05/0x35/0x15 read-status (WIP always 0 here -> never busy).
//
// Read-data phase: each DR *read* returns flash[pos++] (so both the polling loop
// and the DMA RX channel get sequential bytes regardless of when the dummy TX
// bytes are written); DR writes in that phase are just clocks. FRLVL always
// reads 0, so the firmware's "flush RX FIFO" loop is a no-op and reads aren't
// shifted.
//
using System.IO;

using Antmicro.Renode.Core;
using Antmicro.Renode.Peripherals;
using Antmicro.Renode.Peripherals.Bus;
using Antmicro.Renode.Peripherals.Memory;
using Antmicro.Renode.Logging;

namespace Antmicro.Renode.Peripherals.SPI
{
    public class PY32_SPIFlash : IBytePeripheral, IWordPeripheral, IDoubleWordPeripheral, IKnownSize, IGPIOReceiver
    {
        public PY32_SPIFlash(MappedMemory flashMemory, string imagePath = null)
        {
            flash = flashMemory;
            flashMask = (uint)flash.Size - 1;
            ImagePath = imagePath;
            Reset();
        }

        // Backing file for the part. The whole radio configuration lives in this flash
        // (eeprom_compat.c maps the logical EEPROM layout onto it, so channels, settings
        // and calibration all land here -- including everything CHIRP writes). Renode
        // memory is not written through to disk, so without this the radio would forget
        // everything the firmware saved as soon as the machine was reset. Every program
        // and erase the firmware performs is mirrored into the file, at the same offset,
        // so the config survives restarts exactly as it would on the real part.
        public string ImagePath
        {
            get => imagePath;
            set
            {
                image?.Dispose();
                image = null;
                imagePath = value;
                if(!string.IsNullOrEmpty(value))
                {
                    image = new FileStream(value, FileMode.OpenOrCreate, FileAccess.ReadWrite);
                }
            }
        }

        public long Size => 0x400;

        // The firmware uses 8-bit DR access and byte DMA transfers; CR/SR are
        // accessed wider. Route every width through the same register logic so a
        // byte access never triggers a read-modify-write that would double-step
        // the flash pointer.
        public byte ReadByte(long offset) => (byte)ReadRegister(offset);
        public ushort ReadWord(long offset) => (ushort)ReadRegister(offset);
        public uint ReadDoubleWord(long offset) => ReadRegister(offset);
        public void WriteByte(long offset, byte value) => WriteRegister(offset, value);
        public void WriteWord(long offset, ushort value) => WriteRegister(offset, value);
        public void WriteDoubleWord(long offset, uint value) => WriteRegister(offset, value);

        public void Reset()
        {
            cr1 = 0;
            cr2 = 0;
            rxne = false;
            pendingRx = 0;
            csAsserted = false;
            phase = Phase.Idle;
            command = 0;
            addr = 0;
            addrGot = 0;
            pos = 0;
            justEnteredRead = false;
        }

        public void OnGPIO(int number, bool value)
        {
            if(number != PinCs)
            {
                return;
            }
            // CS is active low: low = frame start, high = frame end.
            csAsserted = !value;
            phase = csAsserted ? Phase.ExpectCommand : Phase.Idle;
            addrGot = 0;
        }

        private uint ReadRegister(long offset)
        {
            switch(offset)
            {
            case CR1:
                return cr1;
            case CR2:
                return cr2;
            case SR:
                // TXE always ready; RXNE as tracked; BSY clear; FRLVL = 0.
                return (uint)((1u << 1) | (rxne ? 1u : 0u));
            case DR:
                rxne = false;
                if(phase == Phase.ReadData)
                {
                    // The read that pairs with the final address-byte write must
                    // still return the command-phase response, not flash[addr];
                    // only later reads (the data loop / DMA) pull from flash.
                    if(justEnteredRead)
                    {
                        justEnteredRead = false;
                        return pendingRx;
                    }
                    var b = flash.ReadByte(pos & flashMask);
                    pos++;
                    return b;
                }
                return pendingRx;
            default:
                return 0;
            }
        }

        private void WriteRegister(long offset, uint value)
        {
            switch(offset)
            {
            case CR1:
                cr1 = value;
                break;
            case CR2:
                cr2 = value;
                break;
            case DR:
                HandleByte((byte)value);
                rxne = true; // a transfer always produces a received byte
                break;
            }
        }

        private void HandleByte(byte value)
        {
            switch(phase)
            {
            case Phase.ExpectCommand:
                command = value;
                pendingRx = 0;
                switch(value)
                {
                case 0x03: phase = Phase.ReadAddr; addrGot = 0; addr = 0; break;
                case 0x02: phase = Phase.ProgAddr; addrGot = 0; addr = 0; break;
                case 0x20: phase = Phase.EraseAddr; addrGot = 0; addr = 0; break;
                case 0x05: case 0x35: case 0x15: phase = Phase.Status; break;
                case 0x06: phase = Phase.Done; break; // write-enable: no state to track
                default: phase = Phase.Done; break;
                }
                break;

            case Phase.ReadAddr:
                addr = (addr << 8) | value;
                if(++addrGot == 3)
                {
                    pos = addr;
                    phase = Phase.ReadData;
                    justEnteredRead = true;
                }
                pendingRx = 0;
                break;

            case Phase.ReadData:
                // dummy clock byte; the read side advances the pointer
                break;

            case Phase.ProgAddr:
                addr = (addr << 8) | value;
                if(++addrGot == 3)
                {
                    pos = addr;
                    phase = Phase.ProgData;
                }
                pendingRx = 0;
                break;

            case Phase.ProgData:
                flash.WriteByte(pos & flashMask, value);
                Persist(pos & flashMask, value);
                pos++;
                pendingRx = 0;
                break;

            case Phase.EraseAddr:
                addr = (addr << 8) | value;
                if(++addrGot == 3)
                {
                    EraseSector(addr);
                    phase = Phase.Done;
                }
                pendingRx = 0;
                break;

            case Phase.Status:
                pendingRx = 0x00; // WIP=0, WEL=0 -> never busy
                break;

            case Phase.Done:
            case Phase.Idle:
                pendingRx = 0;
                break;
            }
        }

        private void EraseSector(uint address)
        {
            var start = address & ~(SectorSize - 1) & flashMask;
            for(uint i = 0; i < SectorSize; i++)
            {
                flash.WriteByte((start + i) & flashMask, 0xFF);
            }

            if(image != null)
            {
                image.Seek(start, SeekOrigin.Begin);
                image.Write(erased, 0, (int)SectorSize);
                image.Flush();
            }
        }

        private void Persist(uint offset, byte value)
        {
            if(image == null)
            {
                return;
            }
            image.Seek(offset, SeekOrigin.Begin);
            image.WriteByte(value);
            // The firmware saves in small bursts and a settings write is the last thing
            // it does before you pull the battery, so flush eagerly rather than risk
            // losing the write if the simulator is killed.
            image.Flush();
        }

        private uint cr1;
        private uint cr2;
        private bool rxne;
        private byte pendingRx;
        private bool csAsserted;
        private Phase phase;
        private byte command;
        private uint addr;
        private int addrGot;
        private uint pos;
        private bool justEnteredRead;

        private string imagePath;
        private FileStream image;
        private static readonly byte[] erased = CreateErased();

        private static byte[] CreateErased()
        {
            var buffer = new byte[SectorSize];
            for(var i = 0; i < buffer.Length; i++)
            {
                buffer[i] = 0xFF;
            }
            return buffer;
        }

        private readonly MappedMemory flash;
        private readonly uint flashMask;

        private const int PinCs = 0;
        private const long CR1 = 0x00;
        private const long CR2 = 0x04;
        private const long SR = 0x08;
        private const long DR = 0x0C;
        private const uint SectorSize = 0x1000; // 4 KB

        private enum Phase
        {
            Idle,
            ExpectCommand,
            ReadAddr,
            ReadData,
            ProgAddr,
            ProgData,
            EraseAddr,
            Status,
            Done,
        }
    }
}
