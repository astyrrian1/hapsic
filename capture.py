import time

import serial

try:
    s = serial.Serial('/dev/cu.usbmodem101', 115200, timeout=1.0)
    # Give it 5 seconds to capture
    start = time.time()
    with open('logs.txt', 'wb') as f:
        while time.time() - start < 5.0:
            data = s.read(1024)
            if data:
                f.write(data)
                f.flush()
    print("Capture complete.")
except Exception as e:
    print(f"Error: {e}")
