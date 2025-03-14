import asyncio
import time
from abc import ABC
from dataclasses import dataclass
from queue import Empty, Queue
from threading import Event, Thread
from typing import List, Literal, Optional, Union

from kokoro import KModel, KPipeline
from soundcard import default_speaker

# from textual import log
from torch import FloatTensor, Tensor, cat

SAMPLE_RATE = 24000
BLOCK_SIZE = 512
SLEEP_TIME = 0.2


def log(*args, **kwargs):
    pass


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

    def __init__(self) -> None:
        self.input_queue = Queue()
        self._start = 0
        self._end = BLOCK_SIZE
        self._data: List[Tensor] = []
        self._track_index = None
        self._is_playing = Event()
        self._is_playing.set()
        self._stop_event = Event()
        self._thread = Thread(target=self._run)
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
        log("getting block", i=self._track_index, N=len(self._data))
        if self._track_index is None or len(self._data) <= self._track_index:
            return None
        track_data = self._data[self._track_index]
        n = len(track_data)
        log(n=n, range=f"({self._start}..{self._end})")
        if n <= self._start:
            self._seek(n)
            return None
        if n < self._end:
            self._end = n
        return track_data[self._start : self._end]

    def _process_input(self):
        log("processing input: ")
        try:
            input = self.input_queue.get_nowait()
            log(input=input)
            if isinstance(input, SoundAgent.DataInput):
                self._add_data(input)
            elif isinstance(input, SoundAgent.ChangeTrack):
                if len(self._data) <= input.index:
                    self._track_index = len(self._data) - 1
                else:
                    self._track_index = input.index
            elif isinstance(input, SoundAgent.SeekSecs):
                self._seek(self._start + int(SAMPLE_RATE * input.secs))
        except Empty:
            log("queue empty")
            pass

    def _add_data(self, input: DataInput):
        log(
            "adding data",
            index=input.index,
            overwrite=input.overwrite,
            len=len(input.data),
        )
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


class KokoroAgent:
    @dataclass()
    class Output:
        def __init__(
            self,
            type: Union[Literal["chunk"], Literal["end"]],
            data: Optional[FloatTensor] = None,
        ) -> None:
            self.type = type
            self.data = data

    def __init__(self):
        super().__init__()
        self.input_queue = Queue()
        self.output_queue = Queue()
        self._stop_event = Event()
        self._cancel_event = Event()
        self._thread = Thread(target=self._run)
        self._thread.start()

    def _run(self):
        self._model = KModel()
        self._pipeline_a = KPipeline(lang_code="a", model=self._model)
        while not self._stop_event.is_set():
            try:
                text = self.input_queue.get(timeout=1)
                self._process_task(text)
                self.output_queue.put(KokoroAgent.Output("end"))
            except Empty:
                continue

    def _process_task(self, text: str):
        log("processing input", text=text)
        generator = self._pipeline_a(
            text,
            voice="af_heart",
            speed=1.3,  # type:ignore
            split_pattern="",
        )
        for r in generator:
            audio = r.audio
            if audio is not None:
                log("got chunk", len=len(audio))
                self.output_queue.put(KokoroAgent.Output("chunk", audio))
            if self._cancel_event.is_set():
                log("cancelling task")
                self._cancel_event.clear()
                return

    def stop(self):
        self._cancel_event.set()
        self._stop_event.set()

    def cancel(self):
        self._cancel_event.set()

    def join(self, timeout: Optional[float] = None):
        self._thread.join(timeout)

    async def get_outputs(self):
        while not self._stop_event.is_set():
            try:
                yield self.output_queue.get_nowait()
            except Empty:
                await asyncio.sleep(SLEEP_TIME)
