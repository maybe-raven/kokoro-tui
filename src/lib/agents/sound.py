import asyncio
import time
from abc import ABC
from dataclasses import dataclass
from multiprocessing import Event
from multiprocessing import Process
from multiprocessing import Queue
from queue import Empty
from typing import List, Optional

import soundfile
from numpy import concatenate
from numpy._typing import NDArray
from soundcard import default_speaker
from torch import FloatTensor, Tensor

from lib import SLEEP_TIME

SAMPLE_RATE = 24000
BLOCK_SIZE = 512


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
        self.input_queue: Queue[SoundAgent.Input] = Queue()
        self.output_queue: Queue[SoundAgent.Output] = Queue()
        self._start_index = 0
        self._start_timestamp = None
        self._data: List[NDArray] = []
        self._track_index = -1
        self._is_playing = Event()
        self._is_playing.set()
        self._stop_event = Event()
        self._thread = Process(target=self._run)
        self._thread.start()

    def _run(self):
        while not self._stop_event.is_set():
            if self._should_play():
                self._run_inner()
            else:
                self._process_input(None)

    def _run_inner(self):
        with default_speaker().player(
            samplerate=SAMPLE_RATE, blocksize=BLOCK_SIZE
        ) as player:
            self._seek_and_play(self._start_index, player, False)

            while self._should_play():
                self._process_input(player)
                if not player._queue:
                    assert self._start_timestamp is not None
                    self._start_index = len(self._data[self._track_index])
                    self._start_timestamp = None
                    return

            if self._start_timestamp is not None:
                self._start_index += int(
                    (time.time() - self._start_timestamp) * SAMPLE_RATE
                )
                self._start_timestamp = None

    def _process_input(self, player):
        try:
            input = self.input_queue.get_nowait()
            input.apply(self, player)
        except Empty:
            time.sleep(SLEEP_TIME)

    def _should_play(self) -> bool:
        return (
            self._track_index >= 0
            and self._start_index < len(self._data[self._track_index])
            and self._is_playing.is_set()
        )

    def _seek_and_play(self, start_index: int, player, clear: bool = True):
        self._start_index = 0 if start_index < 0 else start_index
        if player is not None:
            self._start_timestamp = time.time()
            if clear:
                player._queue.clear()
            player.play(self._data[self._track_index][self._start_index :], wait=False)

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
