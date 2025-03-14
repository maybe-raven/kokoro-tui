import asyncio
import os
from datetime import datetime
from typing import ClassVar, Type

from pyperclip import paste
from soundfile import SoundFile
from textual import log, on, work
from textual._path import CSSPathType
from textual.app import App, ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Grid, Horizontal, VerticalGroup
from textual.driver import Driver
from textual.reactive import reactive
from textual.screen import ModalScreen
from textual.widget import Widget
from textual.widgets import Button, Footer, Input, Label, ListItem, ListView, RichLog

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


class FilepathInput(ModalScreen[str]):
    BINDINGS = [
        Binding("escape", "cancel", "Cancel", key_display="esc"),
    ]

    def compose(self) -> ComposeResult:
        title_input = Input(placeholder="Enter a file path.")
        title_input.border_subtitle = (
            r"\[[white]enter[/]] Save  \[[white]esc[/]] Cancel"
        )
        yield title_input

    @on(Input.Submitted)
    def close_screen(self, event: Input.Submitted) -> None:
        self.dismiss(event.value)

    def action_cancel(self):
        self.dismiss()


class ConfirmationScreen(ModalScreen[bool]):
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding(
            "y",
            "confirm(True)",
            "Yes",
            show=False,
        ),
        Binding(
            "n",
            "confirm(False)",
            "No",
            show=False,
        ),
    ]

    def __init__(
        self,
        filename: str,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        super().__init__(name, id, classes)
        self.filename = filename

    def compose(self) -> ComposeResult:
        yield Grid(
            Label(
                f"'{self.filename}' already exists. Are you sure you want to overwrite it?",
                id="question",
            ),
            Button("[Y]es", variant="error", id="overwrite"),
            Button("[N]o", variant="primary", id="cancel"),
            id="dialog",
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "overwrite":
            self.dismiss(True)
        else:
            self.dismiss(False)

    def action_confirm(self, v: bool):
        self.dismiss(v)


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
            "s",
            "save",
            "Save",
            tooltip="Save the selected audio to file.",
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

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        if self.index < 0 and action in ["append", "save"]:
            return None
        return super().check_action(action, parameters)

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
        self.kokoro.feed(text=text, index=self.index)
        self.texts.append(text)
        self.refresh_bindings()
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

    def action_save(self):
        if self.index < 0:
            self.notify("No audio to save.")
            return
        self.save_audio()

    @work(exclusive=True, group="save_audio")
    async def save_audio(self):
        filepath = await self.push_screen(FilepathInput(), wait_for_dismiss=True)
        if filepath is None:
            return
        log("saving audio: got filepath: ", filepath=filepath)
        if os.path.isfile(filepath):
            confirmation = await self.push_screen(
                ConfirmationScreen(filepath), wait_for_dismiss=True
            )
            if not confirmation:
                return
        self.sound.save(filepath)
        result = await self.sound.get_output()
        if result is None:
            return
        elif result.error is None:
            self.notify(f"Success: Audio saved to '{filepath}'.")
        else:
            self.notify(f"Error: {result.error}", severity="error")

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
