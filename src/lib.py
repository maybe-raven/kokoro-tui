import asyncio
import json
import os
import time
from abc import ABC
from copy import deepcopy
from dataclasses import asdict, dataclass
from json import JSONDecodeError
from multiprocessing import Event as MEvent
from multiprocessing import Process
from multiprocessing import Queue as MQueue
from queue import Empty, Queue
from threading import Event, Lock, Thread
from typing import List, Optional, Self

import soundfile
from kokoro import KModel, KPipeline
from numpy import concatenate
from numpy._typing import NDArray
from soundcard import default_speaker
from textual import log
from torch import FloatTensor, Tensor

SAMPLE_RATE = 24000
BLOCK_SIZE = 512
SLEEP_TIME = 0.3

CONFIG_FILEPATH = "~/.config/kokoro-tui/config.json"


class SoundAgent:
    class Input(ABC):
        def apply(self, agent: "SoundAgent", player):
            pass

    @dataclass
    class DataInput(Input):
        data: Tensor
        index: int
        overwrite: bool = False

        def apply(self, agent: "SoundAgent", player):
            index = self.index
            n = len(agent._data)
            input_data = self.data.numpy()
            if n <= index:
                agent._data.append(input_data)
                agent._track_index = n
                agent._seek_and_play(0, player)
            elif self.overwrite:
                agent._data[index] = input_data
                agent._seek_and_play(0, player)
            else:
                agent._data[index] = concatenate((agent._data[index], input_data))
                if player is not None:
                    player.play(input_data, wait=False)

    @dataclass
    class ChangeTrack(Input):
        index: int

        def apply(self, agent: "SoundAgent", player):
            if len(agent._data) <= self.index:
                agent._track_index = len(agent._data) - 1
            else:
                agent._track_index = self.index
            agent._seek_and_play(0, player)

    @dataclass
    class SeekSecs(Input):
        secs: float

        def apply(self, agent: "SoundAgent", player):
            if agent._start_timestamp is None:
                agent._seek_and_play(
                    int(self.secs * SAMPLE_RATE) + agent._start_index, player
                )
            else:
                assert player is not None
                agent._seek_and_play(
                    int(
                        (time.time() - agent._start_timestamp + self.secs) * SAMPLE_RATE
                    )
                    + agent._start_index,
                    player,
                )

    @dataclass
    class Save(Input):
        path: str

        def apply(self, agent: "SoundAgent", player):
            if agent._track_index is None:
                return
            try:
                soundfile.write(
                    self.path,
                    data=agent._data[agent._track_index],
                    samplerate=SAMPLE_RATE,
                )
                agent.output_queue.put(SoundAgent.Output())
            except Exception as e:
                agent.output_queue.put(SoundAgent.Output(e))

    @dataclass
    class ClearHistory(Input):
        pass

        def apply(self, agent: "SoundAgent", player):
            agent._data.clear()
            agent._start_index = 0
            agent._start_timestamp = None
            agent._track_index = -1

    @dataclass
    class Output:
        error: Optional[Exception] = None

    def __init__(self) -> None:
        self.input_queue: MQueue[SoundAgent.Input] = MQueue()
        self.output_queue: MQueue[SoundAgent.Output] = MQueue()
        self._start_index = 0
        self._start_timestamp = None
        self._data: List[NDArray] = []
        self._track_index = -1
        self._is_playing = MEvent()
        self._is_playing.set()
        self._stop_event = MEvent()
        self._thread = Process(target=self._run)
        self._thread.start()

    def _run(self):
        while not self._stop_event.is_set():
            if self._should_play():
                with default_speaker().player(
                    samplerate=SAMPLE_RATE, blocksize=BLOCK_SIZE
                ) as player:
                    self._seek_and_play(self._start_index, player, False)

                    while self._should_play():
                        self._process_input(player)
                        if not player._queue and self._start_timestamp is not None:
                            self._start_index = len(self._data[self._track_index])
                            self._start_timestamp = None

                    self._sync_start_index()
            else:
                self._process_input(None)

    def _process_input(self, player):
        try:
            input = self.input_queue.get_nowait()
            input.apply(self, player)
        except Empty:
            time.sleep(SLEEP_TIME)

    def _should_play(self) -> bool:
        return self._track_index >= 0 and self._is_playing.is_set()

    def _seek_and_play(self, start_index: int, player, clear: bool = True):
        self._start_index = 0 if start_index < 0 else start_index
        if player is not None:
            self._start_timestamp = time.time()
            if clear:
                player._queue.clear()
            player.play(self._data[self._track_index][self._start_index :], wait=False)

    def _sync_start_index(self):
        if self._start_timestamp is not None:
            self._start_index += int(
                (time.time() - self._start_timestamp) * SAMPLE_RATE
            )
            self._start_timestamp = None

    def save(self, path: str):
        self.input_queue.put(SoundAgent.Save(path))

    def stop(self):
        self._stop_event.set()
        self._is_playing.clear()

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

    def feed(self, data: FloatTensor, index: int, overwrite: bool = False):
        self.input_queue.put(SoundAgent.DataInput(data, index, overwrite))

    def change_track(self, index: int):
        self.input_queue.put(SoundAgent.ChangeTrack(index))

    def seek_secs(self, secs: float):
        self.input_queue.put(SoundAgent.SeekSecs(secs))

    def clear_history(self):
        self.input_queue.put(SoundAgent.ClearHistory())

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
        speed: float = 1.3
        split_pattern: str = "\n"
        trf: bool = False
        device: Optional[str] = None

        @classmethod
        def load(cls):
            try:
                with open(os.path.expanduser(CONFIG_FILEPATH), "r") as f:
                    data = json.load(f)
                    return KokoroAgent.Config(**data)
            except (
                FileNotFoundError,
                IsADirectoryError,
                IOError,
                JSONDecodeError,
                TypeError,
            ):
                return KokoroAgent.Config()

        def save(self):
            filepath = os.path.expanduser(CONFIG_FILEPATH)
            os.makedirs(os.path.dirname(filepath), exist_ok=True)
            with open(filepath, "w") as f:
                json.dump(asdict(self), f)

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
        index: int
        generation: int
        overwrite: bool = False

    @dataclass
    class UpdateConfig(Input):
        config: "KokoroAgent.Config"

    @dataclass()
    class Output:
        data: FloatTensor
        index: int
        generation: int
        overwrite: bool = False

    def __init__(self, config: Config):
        super().__init__()
        self.input_queue = Queue[KokoroAgent.Input]()
        self.output_queue = Queue[KokoroAgent.Output]()
        self._config = config
        self._config_lock = Lock()
        self._stop_event = Event()
        self._cancel_event = Event()
        self._is_processing_event = Event()
        self._thread = Thread(target=self._run)
        self._thread.start()

    def _run(self):
        self._model = KModel()
        self._pipeline = KPipeline(
            lang_code=self._config.lang_code,
            trf=self._config.trf,
            device=self._config.device,
            model=self._model,
        )
        while not self._stop_event.is_set():
            try:
                input = self.input_queue.get(timeout=1)
                if isinstance(input, KokoroAgent.UpdateConfig):
                    with self._config_lock:
                        if not self._config.compare_pipeline(input.config):
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
                                    audio,
                                    input.index,
                                    input.generation,
                                    input.overwrite and first_chunk,
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

    def feed(self, text: str, index: int, generation: int, overwrite: bool = False):
        self.input_queue.put(KokoroAgent.DataInput(text, index, generation, overwrite))

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
