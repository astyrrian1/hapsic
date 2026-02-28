import serial
import time

port = '/dev/cu.usbmodem101'
baud = 115200

try:
    with serial.Serial(port, baud, timeout=1) as ser:
        print(f"Reading from {port} at {baud} baud...")
        start_time = time.time()
        while time.time() - start_time < 10:
            line = ser.readline()
            if line:
                print(line.decode('utf-8', errors='ignore'), end='')
except Exception as e:
    print(f"Error: {e}")
