# Quansheng K5 Viewer

This Python script is a simple viewer that displays current screen from the Quansheng K5 transceiver (under F4HWN firmware), transmitted via a serial connection (UART).

## 🚀 Features

- Realtime display of 128×64 monochrome screen via serial connection (UART)
- Delta frame update support for reduced bandwidth
- Screenshot capture in PNG format
- Background color toggle: gray, blue or orange
- Invert video mode
- Increase or decrease window size
- Display FPS in window title

## 🛠️ Requirements

- Python **3.6+**
- pip (Python package installer)

### 📦 Install dependencies:

If necessary, use this command to install dependencies: 

```bash
pip install pyserial pygame
```

## ▶️ How to Run

1. Connect your Quansheng K5 (running F4HWN firmware) to your computer via your Baofeng/Kenwood-like USB-2-Serial-cable.
2. Edit the `SERIAL_PORT` value in `viewer.py` to match your system:

```python
SERIAL_PORT = '/dev/cu.usbserial-xxxx'  # macOS/Linux
# or
SERIAL_PORT = 'COM3'                    # Windows
```

3. Then start the viewer:

```bash
python k5viewer.py
```

## 🎮 Controls

| Key       | Action                          |
|-----------|---------------------------------|
| `Q`       | Quit the viewer                 |
| `G`       | Set background to **gray**      |
| `B`       | Set background to **blue**      |
| `O`       | Set background to **orange**    |
| `I`       | Toggle **video inversion**      |
| `SPACE`   | Save a screenshot as PNG        |
| `UP`      | Increase window size            |
| `DOWN`    | Decrease window size            |


Screenshots are saved as `screenshot_YYYYMMDD_HHMMSS.png` in the same directory.

## 📬 Contact

If you encounter issues or have suggestions, feel free to open an issue or submit a pull request. Enjoy building with your Quansheng K5! 📡
