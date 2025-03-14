import asyncio
from datetime import datetime
from typing import ClassVar, Type

from pyperclip import paste
from soundfile import SoundFile
from textual import log, on, work
from textual._path import CSSPathType
from textual.app import App, ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, VerticalGroup
from textual.driver import Driver
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Footer, Label, ListItem, ListView, RichLog

from lib import KokoroAgent, SoundAgent


def get_text_from_paste():
    text = paste()
    if not text.endswith("\n"):
        text += "\n"
    return text


def time_ago(timestamp):
    now = datetime.now()
    diff = now - timestamp

    seconds = diff.total_seconds()
    if seconds < 60:
        return f"{int(seconds)} seconds ago"
    elif seconds < 3600:
        minutes = seconds // 60
        return f"{int(minutes)} minutes ago"
    elif seconds < 86400:
        hours = seconds // 3600
        return f"{int(hours)} hours ago"
    else:
        days = seconds // 86400
        return f"{int(days)} days ago"


class SourceView(RichLog):
    def __init__(
        self,
        *,
        min_width: int = 78,
        auto_scroll: bool = True,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
        disabled: bool = False,
    ) -> None:
        super().__init__(
            max_lines=None,
            min_width=min_width,
            wrap=True,
            highlight=False,
            markup=False,
            auto_scroll=auto_scroll,
            name=name,
            id=id,
            classes=classes,
            disabled=disabled,
        )


class HumanizedTimeLabel(Widget):
    def __init__(
        self,
        timestamp: datetime,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
        disabled: bool = False,
        markup: bool = True,
    ) -> None:
        super().__init__(
            name=name, id=id, classes=classes, disabled=disabled, markup=markup
        )
        self.timestamp = timestamp

    @work()
    async def periodic_update(self):
        while True:
            now = datetime.now()
            diff = now - self.timestamp
            seconds = diff.total_seconds()
            if seconds < 60:
                await asyncio.sleep(1)
            elif seconds < 3600:
                await asyncio.sleep(60)
            elif seconds < 86400:
                await asyncio.sleep(3600)
            else:
                await self.recompose()
                return
            await self.recompose()

    def on_mount(self):
        self.periodic_update()

    def compose(self) -> ComposeResult:
        yield Label(time_ago(self.timestamp))


class AudioListItem(ListItem):
    text: reactive[str] = reactive("")
    max_width: reactive[int] = reactive(40)

    def __init__(
        self,
        text: str,
        max_width: int = 40,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
        disabled: bool = False,
        markup: bool = True,
    ) -> None:
        super().__init__(
            name=name,
            id=id,
            classes=classes,
            disabled=disabled,
            markup=markup,
        )
        self.timestamp = datetime.now()
        self.text = text.split("\n")[0]
        self.max_width = max_width

    def compose(self) -> ComposeResult:
        with VerticalGroup():
            yield HumanizedTimeLabel(self.timestamp)
            yield Label(self.text, markup=False, classes="ellipsis")


class AudioList(Widget):
    def __init__(
        self,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
        disabled: bool = False,
    ) -> None:
        super().__init__(
            name=name,
            id=id,
            classes=classes,
            disabled=disabled,
        )

    def compose(self) -> ComposeResult:
        yield ListView()

    async def append(self, text: str):
        listview = self.query_one(ListView)
        n = len(listview.children)
        await listview.append(AudioListItem(text))
        listview.index = n


class KokoroApp(App):
    CSS_PATH = "app.tcss"
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
        self.index = -1
        self.texts = []

    def on_mount(self):
        self.kokoro = KokoroAgent()
        self.kokoro_listener()

    def compose(self) -> ComposeResult:
        with Horizontal():
            yield AudioList()
            yield SourceView()
        yield Footer()

    @work(exclusive=True, group="kokoro")
    async def kokoro_listener(self):
        async for chunk in self.kokoro.get_outputs():
            self.sound.feed(chunk.data, chunk.index, chunk.overwrite)

    async def action_new(self):
        self.kokoro.cancel()
        self.index = len(self.texts)
        text = get_text_from_paste()
        self.texts.append(text)
        self.kokoro.feed(text=text, index=self.index)
        self.query_one(SourceView).clear().write(text)
        await self.query_one(AudioList).append(text)

    async def action_append(self):
        if self.index < 0:
            await self.action_new()
            return
        text = get_text_from_paste()
        self.texts[self.index] += text
        self.kokoro.feed(text=text, index=self.index)
        self.query_one(SourceView).write(text)

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

    @on(ListView.Highlighted)
    def update_selection(self, event: ListView.Highlighted):
        i = event.control.index
        if i is not None and i != self.index:
            self.kokoro.cancel()
            self.sound.change_track(i)
            self.index = i
            self.query_one(SourceView).clear().write(self.texts[self.index])

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
