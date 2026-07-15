//
// PY32 USART1 with the firmware's circular RX DMA emulated.
//
// The firmware (App/driver/uart.c) transmits by polling SR.TXE then writing DR,
// and *receives* via DMA1 channel 2 in circular mode: each incoming byte is
// written by the DMA to UART_DMA_Buffer[256 - CNDTR] and CNDTR counts down from
// 256 (reloading at 0). The firmware finds new data with
//   write_ptr = 256 - LL_DMA_GetDataLength(DMA1, ch2)   // = CNDTR
//
// Renode's stock STM32_UART signals RX DMA via a GPIO that our STM32LDMA can't
// receive, and STM32LDMA only does one-shot copies, so RX never reaches the
// buffer. This model instead performs the circular RX write itself on each
// received byte: it reads DMA1-ch2 CMAR/CNDTR over the bus, writes the byte into
// the buffer, and decrements CNDTR -- exactly what the firmware polls.
//
// STM32F1-style registers: SR(0x00) DR(0x04) BRR(0x08) CR1(0x0C) CR2(0x10) CR3(0x14).
//
using System;
using Antmicro.Migrant;
using Antmicro.Renode.Core;
using Antmicro.Renode.Logging;
using Antmicro.Renode.Peripherals;
using Antmicro.Renode.Peripherals.Bus;
using Antmicro.Renode.Peripherals.UART;

namespace Antmicro.Renode.Peripherals.UART
{
    public class PY32_UART : IDoubleWordPeripheral, IBytePeripheral, IWordPeripheral, IKnownSize, IUART
    {
        public PY32_UART(IMachine machine)
        {
            this.machine = machine;
            Reset();
        }

        public long Size => 0x400;
        public uint BaudRate => 38400;
        public Bits StopBits => Bits.One;
        public Parity ParityBit => Parity.None;

        [field: Transient]
        public event Action<byte> CharReceived;

        public void Reset()
        {
            cr1 = cr2 = cr3 = 0;
        }

        public byte ReadByte(long offset) => (byte)Read(offset);
        public ushort ReadWord(long offset) => (ushort)Read(offset);
        public uint ReadDoubleWord(long offset) => Read(offset);
        public void WriteByte(long offset, byte value) => Write(offset, value);
        public void WriteWord(long offset, ushort value) => Write(offset, value);
        public void WriteDoubleWord(long offset, uint value) => Write(offset, value);

        // RX from the host terminal: emulate the firmware's circular RX DMA.
        public void WriteChar(byte value)
        {
            var sysbus = machine.SystemBus;
            var cmar = sysbus.ReadDoubleWord(Dma1Ch2Cmar);
            if(cmar < SramBase || cmar >= SramEnd)
            {
                // DMA not pointed at the RX buffer yet (UART not set up) -- drop.
                return;
            }
            var cndtr = sysbus.ReadDoubleWord(Dma1Ch2Cndtr);
            if(cndtr == 0 || cndtr > DmaBufSize)
            {
                cndtr = DmaBufSize; // circular reload
            }
            sysbus.WriteByte(cmar + (DmaBufSize - cndtr), value);
            sysbus.WriteDoubleWord(Dma1Ch2Cndtr, cndtr - 1);
        }

        private uint Read(long offset)
        {
            switch(offset)
            {
            case SR:
                return (1u << TxeBit) | (1u << TcBit); // always ready to transmit
            case CR1:
                return cr1;
            case CR2:
                return cr2;
            case CR3:
                return cr3;
            default:
                return 0; // DR reads unused (RX is via DMA)
            }
        }

        private void Write(long offset, uint value)
        {
            switch(offset)
            {
            case DR:
                var handler = CharReceived;
                if(handler != null)
                {
                    handler((byte)value); // TX -> terminal
                }
                break;
            case CR1:
                cr1 = value;
                break;
            case CR2:
                cr2 = value;
                break;
            case CR3:
                cr3 = value;
                break;
            }
        }

        private uint cr1, cr2, cr3;
        private readonly IMachine machine;

        private const long SR = 0x00;
        private const long DR = 0x04;
        private const long BRR = 0x08;
        private const long CR1 = 0x0C;
        private const long CR2 = 0x10;
        private const long CR3 = 0x14;

        private const int RxneBit = 5;
        private const int TcBit = 6;
        private const int TxeBit = 7;

        // DMA1 channel 2 registers: base 0x40020000 + 0x08 + (2-1)*0x14 = 0x4002001C.
        private const ulong Dma1Ch2Cndtr = 0x40020020; // +0x04
        private const ulong Dma1Ch2Cmar = 0x40020028;  // +0x0C
        private const uint DmaBufSize = 256;           // sizeof(UART_DMA_Buffer)
        private const uint SramBase = 0x20000000;
        private const uint SramEnd = 0x20004000;       // 16 KB
    }
}
