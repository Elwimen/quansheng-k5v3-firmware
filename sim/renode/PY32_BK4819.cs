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
using System;
using System.Collections.Generic;
using System.IO;

using Antmicro.Renode.Core;
using Antmicro.Renode.Peripherals;
using Antmicro.Renode.Logging;

namespace Antmicro.Renode.Peripherals.Miscellaneous
{
    public class PY32_BK4819 : IGPIOReceiver, IPeripheral
    {
        public PY32_BK4819(IMachine machine)
        {
            this.machine = machine;
            SdaOut = new GPIO();
            registers = new ushort[128];
            // The monitor cannot reach a peripheral registered `@ none` by name, so the
            // keying log is switched on with an environment variable instead of a
            // property. Unset (the normal case) means no logging and no overhead.
            KeyLogPath = Environment.GetEnvironmentVariable("UVK5_CW_KEYLOG");
            Reset();
        }

        // Chip -> MCU data line; wire to the SDA GPIO input (GPIOB pin 9).
        public GPIO SdaOut { get; }

        // While a register is being shifted out, GPIOB serves PB9 from these rather than
        // from the port's own state -- see PY32_GPIOPortB. Trying to drive the pin through
        // the GPIO instead just gets clobbered by the MCU's own clock writes.
        public bool IsDrivingSda => phase == Phase.ReadData;
        public bool SdaLevel => (outValue & 0x8000) != 0;

        // Where to record the transmitter's keying, as "<virtual ms>,<0|1>" per edge.
        //
        // CW is keyed by BK4819_CW_KeyDown/KeyUp -> BK4819_ToggleGpioOut(PA_ENABLE),
        // which is a write to REG_33 bit 0x20 -- so the radio's Morse is already
        // crossing this bus and we only have to write it down. Timestamps are the
        // *emulated* clock, not the host's, so they are unaffected by how fast Renode
        // happens to be running, and a dot is exactly 1200/WPM milliseconds.
        public string KeyLogPath
        {
            get => keyLogPath;
            set
            {
                keyLog?.Dispose();
                keyLog = null;
                keyLogPath = value;
                if(!string.IsNullOrEmpty(value))
                {
                    keyLog = new StreamWriter(value, append: false) { AutoFlush = true };
                }
            }
        }

        // The firmware keys CW two different ways, depending on the mode (cw.c):
        //
        //   OOK  -- BK4819_CW_KeyDown/KeyUp, i.e. the PA on REG_33 bit 0x20. The carrier
        //           itself is switched.
        //   AFCW -- BK4819_ExitTxMute/EnterTxMute, i.e. REG_50. The PA stays on and the
        //           audio tone is keyed instead.
        //
        // Either way the element is "on air", so record both as one keyed/not-keyed
        // signal: that is what a receiver would hear, and what the timing tests check.
        private void RecordKeying(int register, ushort value)
        {
            if(register == GpioOutRegister)
            {
                paOn = (value & PaEnableBit) != 0;
            }
            else if(register == TxMuteRegister)
            {
                txMuted = value == TxMuted;
            }

            var keyed = paOn && !txMuted;
            if(keyed == lastKeyed)
            {
                return;
            }
            lastKeyed = keyed;
            keyLog?.WriteLine("{0},{1}",
                (long)machine.ElapsedVirtualTime.TimeElapsed.TotalMilliseconds,
                keyed ? 1 : 0);
        }

        // ---- Receive: key a Morse signal *at* the radio -------------------------------
        //
        // cw_rx_tick() polls one thing: BK4819_GetAfTxRx(), the AF input amplitude in
        // REG_6F<5:0>. It compares that against a threshold and times the marks and spaces.
        // So "a station calling us" is just that register going up and down: synthesize it
        // from the emulated clock and the decoder cannot tell it from a real signal.
        //
        // The radio auto-calibrates its threshold to (ambient + 10dB) when it has none
        // stored, so the noise floor must be quiet and the tone well clear of it.
        public void SendMorse(string text, int wpm)
        {
            var dit = 1200.0 / wpm;      // the definition of Morse speed, in milliseconds
            var at = machine.ElapsedVirtualTime.TimeElapsed.TotalMilliseconds + 300;
            var marks = new List<Tuple<double, double>>();

            foreach(var raw in text.ToUpperInvariant())
            {
                if(raw == ' ')
                {
                    at += 4 * dit;       // word gap: 7 dits, 3 of which the last char added
                    continue;
                }
                if(!Morse.TryGetValue(raw, out var pattern))
                {
                    continue;
                }
                foreach(var element in pattern)
                {
                    var length = (element == '-' ? 3 : 1) * dit;
                    marks.Add(Tuple.Create(at, at + length));
                    at += length + dit;  // element gap: one dit
                }
                at += 2 * dit;           // character gap: 3 dits, one already added
            }

            keying = marks;
            this.Log(LogLevel.Info, "RX: keying '{0}' at {1} WPM ({2} elements)",
                text, wpm, marks.Count);
        }

        // Reproduce the on-air "stream of E's": real REG_6F is a *broadband* amplitude with
        // no tone selectivity, so band noise and QRN poke above the (noise+10) threshold and
        // the decoder times a 1-tick blip as a dit. The clean model (noise floor 3, tone 24)
        // never showed this. SetRxNoise(level) dials it in: `level` is the peak white-noise
        // amplitude added per sample, plus sparse impulsive spikes that can cross the
        // threshold. 0 = clean (unchanged behaviour, keeps the existing tests deterministic).
        public void SetRxNoise(int level)
        {
            rxNoise = level < 0 ? 0 : level;
            this.Log(LogLevel.Info, "RX noise level set to {0}", rxNoise);
        }

        // Is the keyed carrier up right now? This is the *clean* carrier (independent of the
        // broadband audio noise), which is what an RSSI-based squelch actually sees.
        private bool CarrierPresent()
        {
            if(keying == null)
            {
                return false;
            }
            var now = machine.ElapsedVirtualTime.TimeElapsed.TotalMilliseconds;
            foreach(var mark in keying)
            {
                if(now >= mark.Item1 && now < mark.Item2)
                {
                    return true;
                }
            }
            return false;
        }

        // Model the squelch interrupt the firmware polls: REG_0C bit0 = "an interrupt is
        // pending", REG_02 = the latched event bits (sqlLost/sqlFound). The squelch tracks the
        // clean carrier, not the noisy audio -- that is exactly why the firmware's arming gate
        // can trust it to reject band noise. Evaluated lazily on each REG_0C poll; only a level
        // *change* latches an event, so repeated polls don't loop forever.
        private void UpdateSquelch()
        {
            bool open = CarrierPresent();
            if(open != squelchOpen)
            {
                squelchOpen     = open;
                interruptFlags |= open ? IntSqlLost : IntSqlFound;
                pendingInterrupt = true;
            }
        }

        private ushort ReadModelRegister(byte reg)
        {
            switch(reg)
            {
            case AfAmplitudeRegister:
                return AfAmplitude();
            case InterruptFlagRegister:      // REG_0C: bit0 = interrupt request pending
                UpdateSquelch();
                return (ushort)(pendingInterrupt ? 1u : 0u);
            case InterruptRegister:          // REG_02: latched event bits, consumed on read
                {
                    ushort flags = interruptFlags;
                    interruptFlags = 0;
                    return flags;
                }
            case RssiRegister:               // REG_67<8:0>: RSSI. dBm = rssi/2 - 160.
                {
                    // Carrier present -> a decent signal (~S8); noise floor otherwise. A
                    // little jitter so the S-meter isn't dead-static.
                    int rssi = CarrierPresent() ? 126 : 40;
                    rssi += rng.Next(-4, 5);
                    return (ushort)(rssi < 0 ? 0 : rssi & 0x1FF);
                }
            default:
                return registers[reg];
            }
        }

        private ushort AfAmplitude()
        {
            ushort baseAmp = CarrierPresent() ? RxTone : RxNoiseFloor;
            if(rxNoise <= 0)
            {
                return baseAmp;
            }
            // Per-sample white noise around the current level, plus a sparse impulsive spike
            // (chance scales with the level) big enough to clear the threshold on its own.
            int val = baseAmp + rng.Next(-rxNoise, rxNoise + 1);
            if(rng.Next(100) < rxNoise)
            {
                val += rng.Next(rxNoise, 3 * rxNoise + 1);
            }
            // Bursts: real QRN and band noise arrive in runs of several ms, not lone samples,
            // so they survive a short debounce. A held burst is what a per-sample gate cannot
            // reject and only the squelch (carrier-present) gate can. Start one occasionally
            // and hold an elevated level for a run of samples.
            if(noiseBurst > 0)
            {
                noiseBurst--;
                val += rxNoise + rng.Next(0, rxNoise + 1);
            }
            else if(rng.Next(1000) < rxNoise)
            {
                noiseBurst = rng.Next(10, 40);   // ~10-40ms at the 1ms sample rate
            }
            if(val < 0) val = 0;
            if(val > 63) val = 63;
            return (ushort)val;
        }

        public void Reset()
        {
            keying = null;
            squelchOpen = false;
            interruptFlags = 0;
            pendingInterrupt = false;
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
                        outValue = ReadModelRegister(address);
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
                    if(address == GpioOutRegister || address == TxMuteRegister)
                    {
                        RecordKeying(address, shifter);
                    }
                    if(address == InterruptRegister)
                    {
                        // Firmware writes REG_02 = 0 to acknowledge; drop the pending request.
                        pendingInterrupt = false;
                    }
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

        private readonly IMachine machine;
        private string keyLogPath;
        private StreamWriter keyLog;
        private bool paOn;
        private bool txMuted = true;
        private bool lastKeyed;

        // BK4819_ToggleGpioOut writes REG_33; PA_ENABLE is GPIO1_PIN29, i.e. 0x40 >> 1.
        private const int GpioOutRegister = 0x33;
        private const ushort PaEnableBit = 0x20;
        // BK4819_EnterTxMute / ExitTxMute write REG_50 (mute = 0xBB18, unmute = 0x3B18).
        private const int TxMuteRegister = 0x50;
        private const ushort TxMuted = 0xBB18;

        // BK4819_GetAfTxRx reads REG_6F<5:0>: the AF input amplitude in dB, 0..63.
        private const int AfAmplitudeRegister = 0x6F;
        private const ushort RxNoiseFloor = 3;

        // Interrupt path the firmware polls in CheckRadioInterrupts(): REG_0C bit0 signals a
        // pending interrupt, REG_02 holds the event bits, and writing REG_02 acknowledges.
        private const byte   InterruptFlagRegister = 0x0C;
        private const byte   InterruptRegister     = 0x02;
        private const byte   RssiRegister          = 0x67;
        private const ushort IntSqlLost  = 0x0004;  // squelch opened (carrier present)
        private const ushort IntSqlFound = 0x0008;  // squelch closed (carrier gone)
        private bool   squelchOpen;
        private ushort interruptFlags;
        private bool   pendingInterrupt;
        // The firmware auto-calibrates its threshold to (noise + 10) and then smooths the
        // amplitude with an IIR whose decay is slower than its rise. If the tone sits far
        // above the threshold the decay takes many ticks to fall back through it, every mark
        // is stretched, dots read as dashes and the text comes out as "?". The crossings are
        // symmetric when the threshold sits near the middle of the span, so key the tone at
        // roughly twice the auto-calibrated threshold.
        private const ushort RxTone = 24;

        // Fixed seed: reproducible noise so the "no false E's" regression is stable in CI.
        private readonly Random rng = new Random(12345);
        private int rxNoise;
        private int noiseBurst;   // remaining samples of an in-progress noise burst

        private List<Tuple<double, double>> keying;

        private static readonly Dictionary<char, string> Morse = new Dictionary<char, string>
        {
            {'A', ".-"},   {'B', "-..."}, {'C', "-.-."}, {'D', "-.."},  {'E', "."},
            {'F', "..-."}, {'G', "--."},  {'H', "...."}, {'I', ".."},   {'J', ".---"},
            {'K', "-.-"},  {'L', ".-.."}, {'M', "--"},   {'N', "-."},   {'O', "---"},
            {'P', ".--."}, {'Q', "--.-"}, {'R', ".-."},  {'S', "..."},  {'T', "-"},
            {'U', "..-"},  {'V', "...-"}, {'W', ".--"},  {'X', "-..-"}, {'Y', "-.--"},
            {'Z', "--.."},
            {'0', "-----"}, {'1', ".----"}, {'2', "..---"}, {'3', "...--"}, {'4', "....-"},
            {'5', "....."}, {'6', "-...."}, {'7', "--..."}, {'8', "---.."}, {'9', "----."},
        };

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
