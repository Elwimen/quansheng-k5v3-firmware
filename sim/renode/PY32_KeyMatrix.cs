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
        public PY32_KeyMatrix(IMachine machine)
        {
            Row0 = new GPIO();
            Row1 = new GPIO();
            Row2 = new GPIO();
            Row3 = new GPIO();
            Ptt = new GPIO();

            // A reset of the GPIO port clears its input state to low, while our
            // GPIOs still cache "high" -- and GPIO.Set() is a no-op when the level
            // is unchanged, so the idle levels would never be re-propagated. The
            // firmware would then see PTT held down at boot and sit forever in the
            // "RELEASE ALL KEYS" loop, which short-circuits before KEYBOARD_Poll()
            // and so never drives a column for OnGPIO() to react to. MachineReset
            // runs once every peripheral has reset, so re-driving here sticks.
            machine.MachineReset += _ => ForceIdle();
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

        // Drive the idle levels even when our GPIOs already believe they are high:
        // the low pulse makes the following Set(true) propagate to the port again.
        private void ForceIdle()
        {
            foreach(var pin in new[] { Row0, Row1, Row2, Row3, Ptt })
            {
                pin.Set(false);
                pin.Set(true);
            }
        }
    }
}
