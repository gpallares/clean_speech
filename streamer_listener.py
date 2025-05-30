import socket
import argparse
import signal
import sys
import time
import threading
import logging
from multiprocessing import Pipe
from tactigon_speech import Tactigon_Speech, VoiceConfig, Command, TSpeechObject, TSpeech, HotWord, Transcription

speech_obj = TSpeechObject(
        [
            TSpeech(
                [HotWord("start"), HotWord("enter")],
                TSpeechObject(
                    [
                        TSpeech(
                            [HotWord("application")]
                        )
                    ]
                )
            )
        ])  
# Set up logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger("Client")

#Similar to ros arguments for editing the hardcoded values and stuff
parser = argparse.ArgumentParser()
parser.add_argument('--host', default='127.0.0.1', help='Server IP address')
parser.add_argument('--port', type=int, default=65430, help='Server port')
parser.add_argument('--tflite', default='examples/speech/models.tflite', help='Path to .tflite model')
parser.add_argument('--scorer', default='examples/speech/tos.scorer', help='Path to .scorer file')
args = parser.parse_args()

HOST = args.host
PORT = args.port
CHUNK = 80
TERMINATE_SIGNAL = b"STOP"

#  Prepare VoiceConfig
voice_cfg = VoiceConfig(args.tflite, args.scorer)

#  Create pipes
audio_rx, audio_tx = Pipe(duplex=False)
cmd_rx, cmd_tx = Pipe()  # this is the part where I get and send the commands/ results

tactigon_speech = Tactigon_Speech(voice_cfg, audio_rx, cmd_tx, debug=True)

stop_event = threading.Event()

def audio_receiver(sock):
    """Thread to receive audio from socket and feed to Tactigon_Speech"""
    logger.info("Audio receiver thread started")
    try:
        while not stop_event.is_set():
            try:
                data = sock.recv(CHUNK)
                if not data:
                    logger.info("Server closed connection")
                    break
                if data == TERMINATE_SIGNAL:
                    logger.info("Received STOP signal from server")
                    break
                
                # Feed audio data to Tactigon_Speech
                audio_tx.send_bytes(data)
                logger.debug(f"Sent {len(data)} bytes to speech engine")
                
            except socket.timeout:
                continue
            except Exception as e:
                logger.error(f"Audio receiver error: {e}")
                break
    finally:
        stop_event.set()
        logger.info("Audio receiver exiting")

def signal_handler(sig, frame):
    logger.info("\nTerminating...")
    stop_event.set()
    try:
        if 's' in locals():
            s.sendall(TERMINATE_SIGNAL)
    except:
        pass
    sys.exit(0)

signal.signal(signal.SIGINT, signal_handler)

# Connect to server
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.connect((HOST, PORT))
s.settimeout(1.0)
logger.info(f"Connected to server at {HOST}:{PORT}")



#tactigon_speech.command = Command.LISTEN
#cmd_tx.send(speech_obj)

# Start Tactigon_Speech AFTER connecting to server
tactigon_speech.start()


# Wait for model to initialize - this is CRITICAL
logger.info("Waiting for model initialization...")
while not hasattr(tactigon_speech, 'initialized') or not tactigon_speech.initialized:
    time.sleep(0.1)
logger.info("Model initialized")

# 6. Start LISTEN command
logger.info("Sending speech configuration and starting LISTEN")
cmd_tx.send(speech_obj)  # Send speech configuration
time.sleep(0.2)
tactigon_speech.command = Command.LISTEN  # Start listening

# Start audio receiver thread
audio_thread = threading.Thread(target=audio_receiver, args=(s,))
audio_thread.daemon = True
audio_thread.start()

try:
    logger.info("Waiting for transcriptions...")
    while not stop_event.is_set():
        # Check for results from Tactigon_Speech
        if cmd_rx.poll(0.5):  # Short timeout to avoid blocking
            result = cmd_rx.recv()
            
            if isinstance(result, Transcription):
                logger.info(f"\nFINAL TRANSCRIPTION: {result.text}")
                # Send stop command to server
                s.sendall(TERMINATE_SIGNAL)
                stop_event.set()
                break
            elif isinstance(result, str):
                logger.info(f"Partial: {result}")
        
        # Check if audio thread is still alive
        if not audio_thread.is_alive():
            logger.warning("Audio receiver thread died")
            stop_event.set()
            break
            
        time.sleep(0.05)
        
except Exception as e:
    logger.error(f"Main error: {e}")
finally:
    # Cleanup
    logger.info("Cleaning up resources")
    stop_event.set()
    
    # Send stop command to Tactigon_Speech
    tactigon_speech.command = Command.STOP
    
    # Wait for threads
    audio_thread.join(timeout=1.0)
    
    # Close pipes and socket
    audio_rx.close()
    audio_tx.close()
    cmd_rx.close()
    cmd_tx.close()
    
    try:
        s.sendall(TERMINATE_SIGNAL)
        s.close()
    except:
        pass
    
    tactigon_speech.join(timeout=2.0)
    logger.info("All resources released")