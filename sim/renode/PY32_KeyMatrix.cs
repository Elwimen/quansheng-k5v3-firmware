//
// Minimal UV-K5 key-matrix stub. The firmware scans a GPIOB matrix: columns
// PB3..PB6 are outputs, rows PB12..PB15 are inputs (active low), and PTT is on
// PB10 (active low). A key is "pressed" when a row reads 0.
//
// We don't inject keys through the matrix (key injection rides the serial
// protocol), so this model simply holds all rows and PTT HIGH = "no key" /
// "PTT released". It re-asserts them whenever the firmware drives a column,
// which keeps them high even if a full ODR write to the port momentarily
// clobbered the input state.
//
// Wiring (see platform .repl):
//   gpioPortB 3,4,5,6  -> keyMatrix 0,1,2,3   (column activity, just a trigger)
//   keyMatrix.Row0..Row3 -> gpioPortB 12,13,14,15
//   keyMatrix.Ptt        -> gpioPortB 10
//
using Antmicro.Renode.Core;
using Antmicro.Renode.Peripherals;

namespace Antmicro.Renode.Peripherals.Miscellaneous
{
    public class PY32_KeyMatrix : IGPIOReceiver, IPeripheral
    {
        public PY32_KeyMatrix()
        {
            Row0 = new GPIO();
            Row1 = new GPIO();
            Row2 = new GPIO();
            Row3 = new GPIO();
            Ptt = new GPIO();
            Reset();
        }

        public GPIO Row0 { get; }
        public GPIO Row1 { get; }
        public GPIO Row2 { get; }
        public GPIO Row3 { get; }
        public GPIO Ptt { get; }

        public void Reset()
        {
            DriveIdle();
        }

        public void OnGPIO(int number, bool value)
        {
            // Any column change: re-assert "no key / PTT released".
            DriveIdle();
        }

        private void DriveIdle()
        {
            Row0.Set(true);
            Row1.Set(true);
            Row2.Set(true);
            Row3.Set(true);
            Ptt.Set(true);
        }
    }
}
