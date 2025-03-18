import asyncio
import time
from abc import ABC
from copy import deepcopy
from dataclasses import dataclass
from multiprocessing import Event as MEvent
from multiprocessing import Process
from multiprocessing import Queue as MQueue
from queue import Empty, Queue
from threading import Event, Lock, Thread
from typing import Callable, List, Optional, Self, Union

import soundfile
from kokoro import KModel, KPipeline
from soundcard import default_speaker
from textual import log
from torch import FloatTensor, Tensor, cat

SAMPLE_RATE = 24000
BLOCK_SIZE = 512
SLEEP_TIME = 0.2


class SoundAgent:
    class Input(ABC):
        pass

    @dataclass
    class DataInput(Input):
        data: Tensor
        index: Optional[int] = None
        overwrite: bool = False

    @dataclass
    class ChangeTrack(Input):
        index: int

    @dataclass
    class SeekSecs(Input):
        secs: float

    @dataclass
    class Save(Input):
        path: str

    @dataclass
    class Output:
        error: Optional[Exception] = None

    def __init__(self) -> None:
        self.input_queue: MQueue[SoundAgent.Input] = MQueue()
        self._start = 0
        self._end = BLOCK_SIZE
        self._data: List[Tensor] = []
        self._track_index = None
        self._is_playing = MEvent()
        self._is_playing.set()
        self.output_queue: MQueue[SoundAgent.Output] = MQueue()
        self._stop_event = MEvent()
        self._thread = Process(target=self._run)
        self._thread.start()

    def _run(self):
        with default_speaker().player(
            samplerate=SAMPLE_RATE, blocksize=BLOCK_SIZE
        ) as player:
            while not self._stop_event.is_set():
                self._process_input()
                if self._is_playing.is_set():
                    block = self._get_block()
                    if block is None:
                        time.sleep(SLEEP_TIME)
                    else:
                        player.play(block)
                        self._seek(self._start + BLOCK_SIZE)
                else:
                    time.sleep(SLEEP_TIME)

    def _get_block(self) -> Optional[Tensor]:
        """Get block to play."""
        if self._track_index is None or len(self._data) <= self._track_index:
            return None
        track_data = self._data[self._track_index]
        n = len(track_data)
        if n <= self._start:
            self._seek(n)
            return None
        if n < self._end:
            self._end = n
        return track_data[self._start : self._end]

    def _process_input(self):
        try:
            input = self.input_queue.get_nowait()
            if isinstance(input, SoundAgent.DataInput):
                self._add_data(input)
            elif isinstance(input, SoundAgent.ChangeTrack):
                if len(self._data) <= input.index:
                    self._track_index = len(self._data) - 1
                else:
                    self._track_index = input.index
                self._seek(0)
            elif isinstance(input, SoundAgent.SeekSecs):
                self._seek(self._start + int(SAMPLE_RATE * input.secs))
            elif isinstance(input, SoundAgent.Save):
                self._save(input.path)
        except Empty:
            pass

    def _add_data(self, input: DataInput):
        index = input.index or self._track_index
        n = len(self._data)
        if index is None or n <= index:
            self._data.append(input.data)
            self._track_index = n
            self._seek(0)
        elif input.overwrite:
            self._data[index] = input.data
            self._seek(0)
        else:
            self._data[index] = cat((self._data[index], input.data))

    def _seek(self, target: int):
        self._start = 0 if target < 0 else target
        self._end = self._start + BLOCK_SIZE

    def _save(self, path: str):
        if self._track_index is None:
            return
        try:
            soundfile.write(
                path, data=self._data[self._track_index], samplerate=SAMPLE_RATE
            )
            self.output_queue.put(SoundAgent.Output())
        except Exception as e:
            self.output_queue.put(SoundAgent.Output(e))

    def save(self, path: str):
        self.input_queue.put(SoundAgent.Save(path))

    def stop(self):
        self._stop_event.set()

    def join(self):
        self._thread.join()

    def pause(self):
        self._is_playing.clear()

    def play(self):
        self._is_playing.set()

    def toggle_pp(self):
        if self._is_playing.is_set():
            self._is_playing.clear()
        else:
            self._is_playing.set()

    def feed(
        self, data: FloatTensor, index: Optional[int] = None, overwrite: bool = False
    ):
        self.input_queue.put(SoundAgent.DataInput(data, index, overwrite))

    def change_track(self, index: int):
        self.input_queue.put(SoundAgent.ChangeTrack(index))

    def seek_secs(self, secs: float):
        self.input_queue.put(SoundAgent.SeekSecs(secs))

    async def get_output(self) -> Optional[Output]:
        while not self._stop_event.is_set():
            try:
                return self.output_queue.get_nowait()
            except Empty:
                await asyncio.sleep(SLEEP_TIME)


class KokoroAgent:
    class Input(ABC):
        pass

    @dataclass
    class Config:
        voice: str = "af_heart"
        speed: Union[float, Callable[[int], float]] = 1.3
        split_pattern: str = ""
        trf: bool = False
        device: Optional[str] = None

        def __post_init__(self):
            self.lang_code = self.voice[0]

        def compare_pipeline(self, other: Self) -> bool:
            return (
                self.lang_code == other.lang_code
                and self.trf == other.trf
                and self.device == other.device
            )

    @dataclass
    class DataInput(Input):
        text: str
        index: Optional[int] = None
        overwrite: bool = False

    @dataclass
    class UpdateConfig(Input):
        config: "KokoroAgent.Config"

    @dataclass()
    class Output:
        data: FloatTensor
        index: Optional[int] = None
        overwrite: bool = False

    def __init__(self, config: Optional[Config] = None):
        super().__init__()
        self.input_queue = Queue[KokoroAgent.Input]()
        self.output_queue = Queue[KokoroAgent.Output]()
        self._config = config or KokoroAgent.Config()
        self._config_lock = Lock()
        self._pipeline: Optional[KPipeline] = None
        self._stop_event = Event()
        self._cancel_event = Event()
        self._is_processing_event = Event()
        self._thread = Thread(target=self._run)
        self._thread.start()

    def _run(self):
        self._model = KModel()
        if self._pipeline is None:
            self._pipeline = KPipeline(lang_code="a", model=self._model)
        while not self._stop_event.is_set():
            try:
                log("retrieving input...")
                input = self.input_queue.get(timeout=1)
                if isinstance(input, KokoroAgent.UpdateConfig):
                    with self._config_lock:
                        if self._config.compare_pipeline(input.config):
                            self._pipeline = KPipeline(
                                lang_code=input.config.lang_code,
                                trf=input.config.trf,
                                device=input.config.device,
                                model=self._model,
                            )
                        self._config = input.config
                elif isinstance(input, KokoroAgent.DataInput):
                    self._cancel_event.clear()
                    self._is_processing_event.set()
                    log("processing input", input=input)
                    with self._config_lock:
                        generator = self._pipeline(
                            input.text,
                            voice=self._config.voice,
                            speed=self._config.speed,
                            split_pattern=self._config.split_pattern,
                        )
                    first_chunk = True
                    for r in generator:
                        audio = r.audio
                        if audio is not None:
                            log("got chunk", len=len(audio))
                            self.output_queue.put(
                                KokoroAgent.Output(
                                    audio, input.index, input.overwrite and first_chunk
                                )
                            )
                            first_chunk = False
                        if self._cancel_event.is_set():
                            log("cancelling task")
                            self._cancel_event.clear()
                            break
                    self._is_processing_event.clear()
            except Empty:
                continue

    def feed(self, text: str, index: Optional[int] = None, overwrite: bool = False):
        self.input_queue.put(KokoroAgent.DataInput(text, index, overwrite))

    def stop(self):
        self._cancel_event.set()
        self._stop_event.set()

    def cancel(self):
        self._cancel_event.set()

    def join(self, timeout: Optional[float] = None):
        self._thread.join(timeout)

    def get_config(self):
        with self._config_lock:
            # cloning here because I think python cannot enforce reference immutability
            return deepcopy(self._config)

    def set_config(self, config: Config):
        self.input_queue.put(KokoroAgent.UpdateConfig(deepcopy(config)))

    def is_processing(self) -> bool:
        return self._is_processing_event.is_set()

    async def get_outputs(self):
        while not self._stop_event.is_set():
            try:
                yield self.output_queue.get_nowait()
            except Empty:
                await asyncio.sleep(SLEEP_TIME)
