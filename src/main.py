from typing import ClassVar, Type

from pyperclip import paste
from soundfile import SoundFile
from textual import log, work
from textual._path import CSSPathType
from textual.app import App, ComposeResult
from textual.binding import Binding, BindingType
from textual.driver import Driver
from textual.widgets import Footer, Label

from lib import KokoroAgent, SoundAgent


class KokoroApp(App):
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding(
            "q",
            "quit",
            "Quit",
            tooltip="Quit the app and return to the command prompt.",
            show=False,
            priority=True,
        ),
        Binding(
            "n",
            "new",
            "New",
            tooltip="Generate a new audio from clipboard.",
            show=True,
        ),
        Binding(
            "a",
            "append",
            "Append",
            tooltip="Append clipboard text to the current audio.",
            show=True,
        ),
        Binding(
            "space",
            "toggle_pp",
            "Play/Pause",
            tooltip="Toggle play / pause.",
            show=True,
        ),
        Binding(
            "t",
            "test",
            "Test Sound",
            tooltip="Read a sample audio file to test the SoundAgent.",
            show=False,
        ),
        Binding(
            "h",
            "seek_left",
            "-5s",
            tooltip="Seek backward 5 seconds.",
            show=True,
        ),
        Binding(
            "l",
            "seek_right",
            "+5s",
            tooltip="Seek forward 5 seconds.",
            show=True,
        ),
    ]

    def __init__(
        self,
        sound: SoundAgent,
        driver_class: Type[Driver] | None = None,
        css_path: CSSPathType | None = None,
        watch_css: bool = False,
        ansi_color: bool = False,
    ):
        super().__init__(driver_class, css_path, watch_css, ansi_color)
        self.sound = sound
        self.audio_index = -1

    def on_mount(self):
        self.kokoro = KokoroAgent()
        self.kokoro_listener()

    def compose(self) -> ComposeResult:
        yield Label("LMAO")
        yield Footer()

    @work(exclusive=True, group="kokoro")
    async def kokoro_listener(self):
        async for chunk in self.kokoro.get_outputs():
            self.sound.feed(chunk.data, chunk.index, chunk.overwrite)

    def action_new(self):
        self.kokoro.cancel()
        self.audio_index += 1
        text = paste()
        self.kokoro.feed(text=text, index=self.audio_index)

    def action_append(self):
        if self.audio_index < 0:
            self.audio_index = 0
        text = paste()
        self.kokoro.feed(text)

    def action_test(self):
        with SoundFile("test-data/test.wav") as sf:
            data = sf.read()
            log(samplerate=sf.samplerate, len=len(data))
            self.sound.feed(data, overwrite=True)  # type: ignore

    def action_toggle_pp(self):
        self.sound.toggle_pp()

    def action_seek_left(self):
        self.sound.seek_secs(-5)

    def action_seek_right(self):
        self.sound.seek_secs(5)

    async def action_quit(self) -> None:
        self.kokoro.stop()
        self.sound.stop()
        self.kokoro.join()
        self.sound.join()
        return await super().action_quit()


if __name__ == "__main__":
    sound = SoundAgent()
    app = KokoroApp(sound)
    app.run()
