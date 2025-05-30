import socket
import wave
import argparse
import signal
import sys

parser = argparse.ArgumentParser()
parser.add_argument('--host', default='127.0.0.1', help='Server IP address')
parser.add_argument('--port', type=int, default=65430, help='Server port')
parser.add_argument('--output', default='received.wav', help='Output WAV file')
args = parser.parse_args()

HOST = args.host
PORT = args.port
OUT_PATH = args.output
CHUNK = 4096
TERMINATE_SIGNAL = b"STOP"  # Signal to send when terminating

# Create socket and connect
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.connect((HOST, PORT))

params = {'nchannels': 1, 'sampwidth': 2, 'framerate': 16000}

def signal_handler(sig, frame):
    """Handle Ctrl+C by sending termination signal"""
    print("\nSending STOP command to server...")
    try:
        s.sendall(TERMINATE_SIGNAL)
    finally:
        s.close()
    sys.exit(0)

# Register signal handler for Ctrl+C
signal.signal(signal.SIGINT, signal_handler)

try:
    with wave.open(OUT_PATH, 'wb') as wf:
        wf.setnchannels(params['nchannels'])
        wf.setsampwidth(params['sampwidth'])
        wf.setframerate(params['framerate'])
        
        while True:
            data = s.recv(CHUNK)
            # Exit if termination signal received from server
            if not data or data == TERMINATE_SIGNAL:
                break
            wf.writeframes(data)
finally:
    s.close()
    print(f'Received audio saved to {OUT_PATH}')