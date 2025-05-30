import time
import wave
import socket
import select
import logging

from os import path
from multiprocessing import Process, Pipe, Event, log_to_stderr
from multiprocessing.synchronize import Event as EventClass
from multiprocessing.connection import _ConnectionBase

from datetime import datetime
from typing import Optional

from tactigon_gear import TSkin, TSkinConfig, Hand, OneFingerGesture, TwoFingerGesture
from tactigon_gear.models import TBleSelector, GestureConfig


class Audio(Process):
    _TICK      = 0.02
    _SAMPLE_RATE = 16000
    _PIPE_TIMEOUT: float = 0.1
    _FRAME_BYTES = 80  # must match the size you send into the pipe

    def __init__(self,
                 stop: EventClass,
                 pipe: _ConnectionBase,
                 listen: EventClass,
                 host: str = '127.0.0.1',
                 port: int = 6543,
                 debug: bool = True):
        super().__init__()
        self.logger = log_to_stderr()
        if debug:
            self.logger.setLevel(logging.DEBUG)

        self.stop   = stop
        self.pipe   = pipe
        self.listen = listen
        self.host   = host
        self.port   = port

    def run(self):
        self.logger.debug("[Audio] Process started")
        while not self.stop.is_set():
            # wait until somebody wants to listen
            if not self.listen.is_set():
                time.sleep(self._TICK)
                continue

            self.logger.debug("[Audio] Listening & streaming start")
            # prepare local WAV archive, just to be sure is the same as the other 
            wf = wave.open("sent_audio.wav", "wb")
            wf.setsampwidth(2)
            wf.setnchannels(1)
            wf.setframerate(self._SAMPLE_RATE)

            # Use context manager for socket
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as srv:
                srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                srv.bind((self.host, self.port))
                srv.listen(1)
                srv.setblocking(False)
                self.logger.debug(f"[Streamer] Awaiting client on {self.host}:{self.port}")

                conn = None

                try:
                    # wait for a client to connect
                    while not conn and self.listen.is_set() and not self.stop.is_set():
                        try:
                            conn, addr = srv.accept()
                            conn.setblocking(False)
                            self.logger.debug(f"[Streamer] Client connected: {addr}")
                        except BlockingIOError:
                            time.sleep(self._TICK)

                    # main loop: read audio from pipe, send to client, watch for STOP
                    while self.listen.is_set() and not self.stop.is_set():
                        readers, _, _ = select.select(
                            [self.pipe.fileno(), conn],
                            [], [],
                            self._TICK
                        )

                        # Audio data available?
                        if self.pipe.fileno() in readers:
                            try:
                                frame = self.pipe.recv_bytes()
                            except EOFError:
                                break
                            wf.writeframes(frame)
                            try:
                                conn.sendall(frame)
                            except (BrokenPipeError, OSError):
                                self.logger.debug("[Streamer] Client disconnected")
                                break

                        # STOP command from client?
                        if conn in readers:
                            try:
                                cmd = conn.recv(16)
                            except OSError:
                                cmd = b''
                            if cmd.strip().upper() == b"STOP":
                                print("stopped by client")
                                self.logger.debug("[Audio] STOP received; ending stream")
                                break

                    self.logger.debug("[Audio] Recording/stream session ended")

                finally:
                    wf.close()
                    if conn:
                        conn.close()
                    # srv is closed automatically by context manager
                    while self.pipe.poll():
                        _ = self.pipe.recv_bytes()
                    self.listen.clear()

        self.logger.debug("[Audio] Process terminating")


class TSkin_Audio(TSkin):
    _TICK = 0.02
    def __init__(self, config: TSkinConfig, debug: bool = False,
                 host: str = '127.0.0.1', port: int = 6543):
        super().__init__(config, debug)
        self._audio_rx, self._audio_tx = Pipe(duplex=False)
        self._audio_stop   = Event()
        self._listen_event = Event()
        self.audio = Audio(self._audio_stop,
                           self._audio_rx,
                           self._listen_event,
                           host, port,
                           debug)

    def start(self):
        self.audio.start()
        super().start()

    def join(self, timeout: Optional[float] = None):
        self.select_sensors()
        if self._listen_event.is_set():
            self.stop_listen()
        self._audio_stop.set()
        self.audio.join(timeout)
        self.audio.terminate()
        super().join(timeout)

    def listen(self):
        logging.debug("[TSkin] Starting listen…")
        self._listen_event.set()
        self.select_audio()
        # block until audio process clears the event
        while self._listen_event.is_set():
            time.sleep(self._TICK)
        logging.debug("[TSkin] Stopped listen")
        self.select_sensors()

    def stop_listen(self):
        logging.debug("[TSkin] Stopping listen…")
        self.select_sensors()
        self._listen_event.clear()
        self.clear_audio_pipe()

    def clear_audio_pipe(self):
        cnt = 0
        while self._audio_rx.poll():
            _ = self._audio_rx.recv_bytes()
            cnt += 1
        logging.debug(f"[Audio] Cleared {cnt} frames from pipe")

def test():
    model_folder = path.join(path.abspath("."), "data", "models", "MODEL_01_R")
    gconfig = GestureConfig(
        path.join(model_folder, "model.pickle"),
        path.join(model_folder, "encoder.pickle"),
        "test",
        datetime.now()
    ) # i think I dont need this for the audio streamer, jsut leave it 
    #there to rememer where to add it later when integrated with gestures

    tskin_cfg = TSkinConfig(
        "C0:83:3D:34:25:38",
        Hand.RIGHT,
        # gesture_config=gconfig
    )

    with TSkin_Audio(tskin_cfg, debug=False, host='127.0.0.1', port=65430) as tskin:
        print("Connecting…")
        while not tskin.connected:
            time.sleep(0.5)
        print("Connected!")

        while True:
            if not tskin.connected:
                print("Disconnected… retrying")
                time.sleep(2)
                continue

            if tskin.selector ==  TBleSelector.AUDIO:
                # waiting for tap to start streaming
                print("Waiting for tap to start streaming…")
                time.sleep(0.1)
                continue

            # handle sensor data…
            touch = ""
            t = tskin.touch
            if t:
                touch = (t.one_finger
                         if t.one_finger != OneFingerGesture.NONE
                         else t.two_finger)

            if touch == OneFingerGesture.SINGLE_TAP:
                # this will record + stream until client sends "STOP"
                tskin.listen()

            # …and any other per-loop logic
            time.sleep(0.02)

def main():
    test()

if __name__ == "__main__":
    test()
