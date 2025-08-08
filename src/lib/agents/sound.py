import asyncio
import time
from abc import ABC
from ctypes import Structure, c_int
from dataclasses import dataclass
from multiprocessing import Event, Process, Queue, Value
from queue import Empty
from typing import AsyncGenerator, List, Optional, Tuple

import soundfile
from numpy._typing import NDArray
from soundcard import default_speaker

# from textual import log
from lib.agents import SAMPLE_RATE, Audio

BLOCK_SIZE = 512
DEFAULT_SLEEP_TIME = 0.3
FAST_SLEEP_TIME = 0.1


class RangeStruct(Structure):
    _fields_ = [("start", c_int), ("end", c_int)]


class Input(ABC):
    def apply(self, agent: "SoundAgent", player):
        pass


@dataclass
class DataInput(Input):
    chunk: Audio
    index: int
    overwrite: bool = False

    def apply(self, agent: "SoundAgent", player):
        index = self.index
        n = len(agent._data)
        if n <= index:
            agent._data.append(self.chunk)
            agent._track_index = n
            agent._seek_and_play(0, player)
        elif self.overwrite:
            agent._data[index] = self.chunk
            agent._seek_and_play(0, player)
        else:
            agent._data[index].concat(self.chunk)
            if player is not None:
                player.play(self.chunk.data, wait=False)


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
        if agent._track_index < 0:
            return
        if agent._start_timestamp is None:
            agent._seek_and_play(
                int(self.secs * SAMPLE_RATE) + agent._start_index, player
            )
        else:
            assert player is not None
            agent._seek_and_play(
                int((time.time() - agent._start_timestamp + self.secs) * SAMPLE_RATE)
                + agent._start_index,
                player,
            )


@dataclass
class Save(Input):
    path: str

    def apply(self, agent: "SoundAgent", player):
        if agent._track_index < 0:
            return
        try:
            soundfile.write(
                self.path,
                data=agent._get_data(),
                samplerate=SAMPLE_RATE,
            )
            agent.output_queue.put(Output())
        except Exception as e:
            agent.output_queue.put(Output(e))


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


class SoundAgent:
    def __init__(self) -> None:
        self.input_queue: Queue[Input] = Queue()
        self.output_queue: Queue[Output] = Queue()
        self.text_indices = Value(RangeStruct)
        self.text_indices.start = -1
        self.text_indices.end = -1
        self._token_index = 0
        self._start_index = 0
        self._start_timestamp = None
        self._data: List[Audio] = []
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
                self._process_input()

    def _run_inner(self):
        with default_speaker().player(
            samplerate=SAMPLE_RATE, blocksize=BLOCK_SIZE
        ) as player:
            self._seek_and_play(self._start_index, player, False)

            while self._should_play():
                self._update_text_indices()
                self._process_input(player, FAST_SLEEP_TIME)
                if not player._queue:
                    assert self._start_timestamp is not None
                    self._start_index = len(self._get_data())
                    self._start_timestamp = None
                    self._reset_text_indices()
                    return

            self._start_index = self._current_index()
            self._start_timestamp = None

    def _current_index(self) -> int:
        if self._start_timestamp is None:
            return self._start_index
        else:
            return self._start_index + int(
                (time.time() - self._start_timestamp) * SAMPLE_RATE
            )

    def _reset_text_indices(self):
        self._token_index = 0
        with self.text_indices.get_lock():
            self.text_indices.start = -1
            self.text_indices.end = -1

    def _update_text_indices(self):
        assert self._start_timestamp is not None
        index = self._current_index()
        # log(
        #     "_update_text_indices",
        #     tokens=self._data[self._track_index].tokens[self._token_index :],
        #     current_index=index,
        # )
        for i, token in enumerate(
            self._data[self._track_index].tokens[self._token_index :]
        ):
            if token.start_index <= index and token.end_index >= index:
                with self.text_indices.get_lock():
                    self.text_indices.start = token.text_index_start
                    self.text_indices.end = token.text_index_end
                self._token_index += i
                return

        self._reset_text_indices()

    def _process_input(self, player=None, sleep_time: float = DEFAULT_SLEEP_TIME):
        try:
            input = self.input_queue.get_nowait()
            input.apply(self, player)
        except Empty:
            time.sleep(sleep_time)

    def _should_play(self) -> bool:
        return (
            self._track_index >= 0
            and self._start_index < len(self._get_data())
            and self._is_playing.is_set()
        )

    def _seek_and_play(self, start_index: int, player, clear: bool = True):
        self._start_index = 0 if start_index < 0 else start_index
        n = len(self._get_data())
        if self._start_index >= n:
            self._start_index = n
            return
        if player is not None:
            self._start_timestamp = time.time()
            if clear:
                player._queue.clear()
            player.play(self._get_data()[self._start_index :], wait=False)

    def _get_data(self) -> NDArray:
        return self._data[self._track_index].data

    def save(self, path: str):
        self.input_queue.put(Save(path))

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

    def feed(self, chunk: Audio, index: int, overwrite: bool = False):
        self.input_queue.put(DataInput(chunk, index, overwrite))

    def change_track(self, index: int):
        self.input_queue.put(ChangeTrack(index))

    def seek_secs(self, secs: float):
        self.input_queue.put(SeekSecs(secs))

    def clear_history(self):
        self.input_queue.put(ClearHistory())

    async def get_output(self) -> Optional[Output]:
        while not self._stop_event.is_set():
            try:
                return self.output_queue.get_nowait()
            except Empty:
                await asyncio.sleep(DEFAULT_SLEEP_TIME)

    async def get_text_indices(self) -> AsyncGenerator[Optional[Tuple[int, int]], None]:
        last_yielded = None
        while not self._stop_event.is_set():
            with self.text_indices.get_lock():
                start = self.text_indices.start
                end = self.text_indices.end
            ret = (start, end) if start >= 0 and end >= 0 else None
            if ret != last_yielded:
                last_yielded = ret
                yield ret
            await asyncio.sleep(FAST_SLEEP_TIME)
