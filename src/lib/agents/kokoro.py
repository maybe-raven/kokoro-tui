import asyncio
import json
import os
from abc import ABC
from copy import deepcopy
from dataclasses import asdict, dataclass
from json import JSONDecodeError
from queue import Empty, Queue
from threading import Event, Lock, Thread
from typing import Optional, Self

from kokoro import KModel, KPipeline
from textual import log

from lib.agents import SAMPLE_RATE, Audio, Token

CONFIG_FILEPATH = "~/.config/kokoro-tui/config.json"
SLEEP_TIME = 0.3


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
    def load(cls) -> "Config":
        try:
            with open(os.path.expanduser(CONFIG_FILEPATH), "r") as f:
                data = json.load(f)
                return Config(**data)
        except (
            FileNotFoundError,
            IsADirectoryError,
            IOError,
            JSONDecodeError,
            TypeError,
        ):
            return Config()

    def save(self):
        filepath = os.path.expanduser(CONFIG_FILEPATH)
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        with open(filepath, "w") as f:
            json.dump(asdict(self), f)

    def lang_code(self) -> str:
        return self.voice[0]

    def compare_pipeline(self, other: Self) -> bool:
        return (
            self.lang_code() == other.lang_code()
            and self.trf == other.trf
            and self.device == other.device
        )


@dataclass
class DataInput(Input):
    text: str
    reference_text: Optional[str]
    index: int
    generation: int
    overwrite: bool = False


@dataclass
class UpdateConfig(Input):
    config: Config


@dataclass()
class Output:
    audio: Audio
    index: int
    generation: int
    overwrite: bool = False


class KokoroAgent:
    def __init__(self, config: Config):
        super().__init__()
        self.input_queue = Queue[Input]()
        self.output_queue = Queue[Output]()
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
            lang_code=self._config.lang_code(),
            trf=self._config.trf,
            device=self._config.device,
            model=self._model,
        )
        while not self._stop_event.is_set():
            try:
                input = self.input_queue.get(timeout=1)
                if isinstance(input, UpdateConfig):
                    with self._config_lock:
                        if not self._config.compare_pipeline(input.config):
                            self._pipeline = KPipeline(
                                lang_code=input.config.lang_code(),
                                trf=input.config.trf,
                                device=input.config.device,
                                model=self._model,
                            )
                        self._config = input.config
                elif isinstance(input, DataInput):
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
                    input_text_offset = (
                        input.reference_text.rfind(input.text)
                        if input.reference_text
                        else 0
                    )
                    assert input_text_offset >= 0
                    text_index_start = 0
                    first_chunk = True
                    for r in generator:
                        tokens = []
                        if r.tokens is not None:
                            log("got tokens", r.tokens)
                            for token in r.tokens:
                                i = input.text.find(token.text, text_index_start)
                                if i >= 0:
                                    text_index_start = i
                                text_index_end = text_index_start + len(token.text)
                                if (
                                    token.start_ts is not None
                                    and token.end_ts is not None
                                ):
                                    tokens.append(
                                        Token(
                                            text_index_start + input_text_offset,
                                            text_index_end + input_text_offset,
                                            int(token.start_ts * SAMPLE_RATE),
                                            int(token.end_ts * SAMPLE_RATE),
                                        ),
                                    )
                                text_index_start = text_index_end + len(
                                    token.whitespace
                                )
                        audio = r.audio
                        if audio is not None:
                            self.output_queue.put(
                                Output(
                                    Audio(audio.numpy(), tokens),
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

    def feed(self, input: DataInput):
        self.input_queue.put(input)

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
        self.input_queue.put(UpdateConfig(deepcopy(config)))

    def is_processing(self) -> bool:
        return self._is_processing_event.is_set()

    async def get_outputs(self):
        while not self._stop_event.is_set():
            try:
                yield self.output_queue.get_nowait()
            except Empty:
                await asyncio.sleep(SLEEP_TIME)
