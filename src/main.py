import asyncio
import json
import os
import sys
from argparse import ArgumentParser, Namespace
from dataclasses import dataclass
from datetime import datetime
from typing import ClassVar, Optional, Self, Type, cast

from pyperclip import paste
from textual import log, on, work
from textual._path import CSSPathType
from textual.actions import SkipAction
from textual.app import App, ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Grid, Horizontal, HorizontalGroup, VerticalGroup
from textual.css.query import NoMatches
from textual.driver import Driver
from textual.events import Resize
from textual.reactive import reactive
from textual.screen import ModalScreen
from textual.validation import ValidationResult, Validator
from textual.widget import Widget
from textual.widgets import (
    Button,
    Footer,
    Input,
    Label,
    ListItem,
    ListView,
    LoadingIndicator,
    RichLog,
    Select,
    Switch,
)
from textual.widgets._select import NoSelection

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
        min_width: int = 0,
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
        self.text = None

    def write_str(self, content: str) -> Self:
        if self.text is None:
            self.text = content
        else:
            self.text += content
        return self.write(content)

    def clear(self) -> Self:
        self.text = None
        return super().clear()

    def overwrite(self, content) -> Self:
        self.clear()
        self.text = content
        return self.write(content)

    def on_resize(self, event: Resize):
        super().on_resize(event)
        if self.text is not None:
            self.overwrite(self.text)


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


class PositiveNumberValidator(Validator):
    def validate(self, value: str) -> ValidationResult:
        try:
            x = float(value)
            if x > 0:
                return self.success()
            else:
                return self.failure("speed must be positive")
        except ValueError:
            return self.failure("not a number")


class ConfigScreen(ModalScreen[KokoroAgent.Config]):
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding(
            "ctrl+enter",
            "confirm",
            "Confirm",
            show=True,
        ),
        Binding("escape", "cancel", "Cancel", show=True, key_display="esc"),
    ]

    def __init__(
        self,
        config: KokoroAgent.Config,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        super().__init__(name, id, classes)
        self.config = config

    def compose(self) -> ComposeResult:
        with Grid(id="config-grid"):
            yield Label("voice")
            yield Select(
                [
                    ("af_heart", "af_heart"),
                    ("af_alloy", "af_alloy"),
                    ("af_aoede", "af_aoede"),
                    ("af_bella", "af_bella"),
                    ("af_jessica", "af_jessica"),
                    ("af_kore", "af_kore"),
                    ("af_nicole", "af_nicole"),
                    ("af_nova", "af_nova"),
                    ("af_river", "af_river"),
                    ("af_sarah", "af_sarah"),
                    ("af_sky", "af_sky"),
                    ("am_adam", "am_adam"),
                    ("am_echo", "am_echo"),
                    ("am_eric", "am_eric"),
                    ("am_fenrir", "am_fenrir"),
                    ("am_liam", "am_liam"),
                    ("am_michael", "am_michael"),
                    ("am_onyx", "am_onyx"),
                    ("am_puck", "am_puck"),
                    ("am_santa", "am_santa"),
                    ("bf_alice", "bf_alice"),
                    ("bf_emma", "bf_emma"),
                    ("bf_isabella", "bf_isabella"),
                    ("bf_lily", "bf_lily"),
                    ("bm_daniel", "bm_daniel"),
                    ("bm_fable", "bm_fable"),
                    ("bm_george", "bm_george"),
                    ("bm_lewis", "bm_lewis"),
                    ("jf_alpha", "jf_alpha"),
                    ("jf_gongitsune", "jf_gongitsune"),
                    ("jf_nezumi", "jf_nezumi"),
                    ("jf_tebukuro", "jf_tebukuro"),
                    ("jm_kumo", "jm_kumo"),
                    ("zf_xiaobei", "zf_xiaobei"),
                    ("zf_xiaoni", "zf_xiaoni"),
                    ("zf_xiaoxiao", "zf_xiaoxiao"),
                    ("zf_xiaoyi", "zf_xiaoyi"),
                    ("zm_yunjian", "zm_yunjian"),
                    ("zm_yunxi", "zm_yunxi"),
                    ("zm_yunxia", "zm_yunxia"),
                    ("zm_yunyang", "zm_yunyang"),
                    ("ef_dora", "ef_dora"),
                    ("em_alex", "em_alex"),
                    ("em_santa", "em_santa"),
                    ("ff_siwis", "ff_siwis"),
                    ("hf_alpha", "hf_alpha"),
                    ("hf_beta", "hf_beta"),
                    ("hm_omega", "hm_omega"),
                    ("hm_psi", "hm_psi"),
                    ("if_sara", "if_sara"),
                    ("im_nicola", "im_nicola"),
                    ("pf_dora", "pf_dora"),
                    ("pm_alex", "pm_alex"),
                    ("pm_santa", "pm_santa"),
                ],
                value=self.config.voice,
                id="input-voice",
            )
            yield Label("speed")
            yield Input(
                value=str(self.config.speed),
                type="number",
                restrict=r"^\d*\.?\d*",
                max_length=8,
                validators=PositiveNumberValidator(),
                id="input-speed",
            )
            yield Label("split pattern")
            yield Input(
                value=self.config.split_pattern.replace("\n", "\\n"),
                id="input-pattern",
            )
            yield Label("device")
            yield Select(
                [("cpu", "cpu"), ("cuda", "cuda"), ("mps", "mps")],
                value=Select.BLANK
                if self.config.device is None
                else self.config.device,
                id="input-device",
            )
            yield Label("trf")
            yield Switch(value=self.config.trf, id="input-trf")
            yield HorizontalGroup(
                Button("Confirm", variant="primary", id="confirm"),
                Button("Cancel", variant="default", id="cancel"),
                id="button-group",
            )

    def action_confirm(self):
        self.config.voice = cast(Input, self.query_one("#input-voice")).value
        self.config.speed = float(cast(Input, self.query_one("#input-speed")).value)
        pattern = cast(Input, self.query_one("#input-pattern")).value
        self.config.split_pattern = pattern.replace("\\n", "\n")
        log(escaped_pattern=pattern, pattern=self.config.split_pattern)
        selection = cast(Select[str], self.query_one("#input-device")).value
        self.config.device = None if isinstance(selection, NoSelection) else selection
        self.config.trf = cast(Switch, self.query_one("#input-trf")).value
        try:
            self.config.save()
        except (PermissionError, IOError) as e:
            self.notify(f"Error: failed to write config file: {e}", severity="error")
        self.dismiss(self.config)

    def action_cancel(self):
        self.dismiss()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "confirm":
            self.action_confirm()
        else:
            self.dismiss()


class KokoroApp(App):
    CSS_PATH = "app.tcss"
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding(
            "?",
            "toggle_help_panel",
            "Help",
            show=True,
        ),
        Binding(
            "o",
            "open",
            "Open File",
            tooltip="Open a text file and generate an audio from it.",
            show=True,
        ),
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
            "r",
            "regenerate",
            "Regenerate",
            tooltip="Regenerate an audio.",
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
            "J",
            "cursor_down",
            "Down",
            tooltip="Move the cursor down in the history list.",
            show=False,
        ),
        Binding(
            "K",
            "cursor_up",
            "Up",
            tooltip="Move the cursor up in the history list.",
            show=False,
        ),
        Binding(
            "j",
            "scroll_down",
            "Down",
            tooltip="Scroll down one line in the text view.",
            show=False,
        ),
        Binding(
            "k",
            "scroll_up",
            "Up",
            tooltip="Scroll up one line in the text view.",
            show=False,
        ),
        Binding(
            "ctrl+f",
            "page_down",
            "Page Down",
            tooltip="Scroll down one page in the text view.",
            show=False,
        ),
        Binding(
            "ctrl+b",
            "page_up",
            "Page Up",
            tooltip="Scroll up one page in the text view.",
            show=False,
        ),
        Binding(
            "ctrl+d",
            "half_page_down",
            "Half Page Down",
            tooltip="Scroll down half page in the text view.",
            show=False,
        ),
        Binding(
            "ctrl+u",
            "half_page_up",
            "Half Page Up",
            tooltip="Scroll up half page in the text view.",
            show=False,
        ),
        Binding(
            "ctrl+g",
            "clear_history",
            "Clear History",
            tooltip="Clear all audio history.",
            show=True,
        ),
        Binding(
            "c",
            "config",
            "Update Config",
            tooltip="Update Kokoro TTS configurations.",
            show=True,
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
        Binding(
            "space",
            "toggle_pp",
            "Play/Pause",
            tooltip="Toggle play / pause.",
            show=True,
        ),
        Binding(
            "m",
            "toggle_side_panel",
            "Toggle Side Panel",
            tooltip="Toggle side panel.",
            show=True,
        ),
    ]

    def __init__(
        self,
        sound: SoundAgent,
        args: Namespace,
        driver_class: Type[Driver] | None = None,
        css_path: CSSPathType | None = None,
        watch_css: bool = False,
        ansi_color: bool = False,
    ):
        super().__init__(driver_class, css_path, watch_css, ansi_color)
        self.sound = sound
        self.index = -1
        self.texts = []
        self.generation = 0
        self.args = args

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        if self.index < 0 and action in ["append", "save", "regenerate"]:
            return None
        return super().check_action(action, parameters)

    async def on_mount(self):
        try:
            config = KokoroAgent.Config.load()
        except PermissionError as e:
            self.notify(f"Error: failed to read config file: {e}.")
            config = KokoroAgent.Config()
        self.kokoro = KokoroAgent(config)
        self.kokoro_listener()
        if self.args.new:
            await self.make_audio(get_text_from_paste(), False)
        self.run_server()

    def compose(self) -> ComposeResult:
        horizontal = Horizontal()
        horizontal.can_focus_children = False
        with horizontal:
            yield AudioList()
            yield SourceView()
        yield Footer()
        loading = LoadingIndicator()
        loading.display = False
        yield loading

    def update_loading_indicator(self, visibility: Optional[bool] = None):
        try:
            self.query_one(LoadingIndicator).display = (
                visibility or self.kokoro.is_processing()
            )
        except NoMatches:
            pass

    @work(exclusive=True, group="server")
    async def run_server(self):
        if not callable(asyncio.start_unix_server):
            return

        server = await asyncio.start_unix_server(self.handle_client, "/tmp/kokoro-tui")
        async with server:
            await server.serve_forever()

    async def handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ):
        data = await reader.read()
        message = json.loads(data)
        try:
            message = SocketMsg(**message)
            await self.make_audio(message.content, message.append)
        except TypeError:
            pass

        writer.close()

    @work(exclusive=True, group="kokoro")
    async def kokoro_listener(self):
        async for chunk in self.kokoro.get_outputs():
            log(
                "got audio chunk",
                chunk_generation=chunk.generation,
                app_generation=self.generation,
                chunk_index=chunk.index,
                app_len=len(self.texts),
            )
            if chunk.generation == self.generation and chunk.index < len(self.texts):
                self.sound.feed(chunk.data, chunk.index, chunk.overwrite)
            self.update_loading_indicator()

    async def action_new(self):
        await self.make_audio(get_text_from_paste(), False)

    async def action_append(self):
        await self.make_audio(get_text_from_paste(), True)

    def action_toggle_side_panel(self):
        panel = self.query_one(AudioList)
        panel.display = not panel.display

    def action_toggle_pp(self):
        self.sound.toggle_pp()

    def action_seek_left(self):
        self.sound.seek_secs(-5)

    def action_seek_right(self):
        self.sound.seek_secs(5)

    def action_regenerate(self):
        if self.index < 0:
            return
        self.kokoro.cancel()
        text = self.texts[self.index]
        log("regenerating", text=text, index=self.index)
        self.kokoro.feed(
            text=text, index=self.index, generation=self.generation, overwrite=True
        )
        self.update_loading_indicator(True)

    def action_config(self):
        self.update_config()

    def action_clear_history(self):
        self.kokoro.cancel()
        self.update_loading_indicator(False)
        self.generation += 1
        self.index = -1
        self.texts.clear()
        self.sound.clear_history()
        self.query_one(SourceView).clear()
        self.query_one(ListView).clear()

    @work(exclusive=True, group="update_config")
    async def update_config(self):
        config = await self.push_screen(
            ConfigScreen(self.kokoro.get_config()), wait_for_dismiss=True
        )
        if config is None:
            return
        log("updating config: got config: ", config=config)
        self.kokoro.set_config(config)

    def action_save(self):
        if self.index < 0:
            self.notify("No audio to save.")
            return
        self.save_audio()

    @work(exclusive=True, group="save_audio")
    async def save_audio(self):
        filepath = await self.push_screen(FilepathInput(), wait_for_dismiss=True)
        if not filepath:
            return
        log("saving audio: got filepath: ", filepath=filepath)
        filepath = os.path.expanduser(filepath)
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
            self.update_loading_indicator(False)
            self.index = i
            self.query_one(SourceView).overwrite(self.texts[self.index])

    def action_cursor_down(self):
        self.query_one(ListView).action_cursor_down()

    def action_cursor_up(self):
        self.query_one(ListView).action_cursor_up()

    def action_scroll_down(self):
        self.query_one(SourceView).action_scroll_down()

    def action_scroll_up(self):
        self.query_one(SourceView).action_scroll_up()

    def action_page_down(self):
        self.query_one(SourceView).action_page_down()

    def action_page_up(self):
        self.query_one(SourceView).action_page_up()

    def action_half_page_down(self):
        view = self.query_one(SourceView)
        if not view.allow_vertical_scroll:
            raise SkipAction()
        view._user_scroll_interrupt = True
        view._clear_anchor()
        view.scroll_to(
            y=view.scroll_y + view.scrollable_content_region.height // 2,
        )

    def action_half_page_up(self):
        view = self.query_one(SourceView)
        if not view.allow_vertical_scroll:
            raise SkipAction()
        view._user_scroll_interrupt = True
        view._clear_anchor()
        view.scroll_to(
            y=view.scroll_y - view.scrollable_content_region.height // 2,
        )

    def action_toggle_help_panel(self):
        if self.screen.query("HelpPanel"):
            self.action_hide_help_panel()
        else:
            self.action_show_help_panel()

    def action_open(self):
        self.audio_from_file()

    @work(exclusive=True, group="audio_from_file")
    async def audio_from_file(self):
        filepath = await self.push_screen(FilepathInput(), wait_for_dismiss=True)
        if not filepath:
            return
        filepath = os.path.expanduser(filepath)
        try:
            with open(filepath, mode="r", encoding="utf-8") as f:
                text = f.read()
            await self.make_audio(text, False)
        except FileNotFoundError:
            self.notify(
                f"Error: The file '{filepath}' does not exist.", severity="error"
            )
        except PermissionError:
            self.notify(
                f"Error: You do not have the necessary permissions to access '{filepath}'.",
                severity="error",
            )
        except IsADirectoryError:
            self.notify(
                f"Error: '{filepath}' is a directory, not a file.",
                severity="error",
            )
        except UnicodeDecodeError:
            self.notify(
                f"Error: file '{filepath}' contains invalid unicode.",
                severity="error",
            )
        except IOError as e:
            self.notify(
                f"An I/O error occurred: {e.strerror}",
                severity="error",
            )

    async def make_audio(self, text: str, append: bool):
        if not append or self.index < 0:
            self.kokoro.cancel()
            self.index = len(self.texts)
            self.kokoro.feed(text=text, index=self.index, generation=self.generation)
            self.texts.append(text)
            self.refresh_bindings()
            self.query_one(SourceView).overwrite(text)
            await self.query_one(AudioList).append(text)
        else:
            self.kokoro.feed(text=text, index=self.index, generation=self.generation)
            self.texts[self.index] += "\n" + text
            self.query_one(SourceView).write_str(text)
        self.update_loading_indicator(True)

    async def action_quit(self) -> None:
        self.kokoro.stop()
        self.sound.stop()
        self.kokoro.join()
        self.sound.join()
        return await super().action_quit()


@dataclass
class SocketMsg:
    append: bool
    content: str


if __name__ == "__main__":
    parser = ArgumentParser()
    parser.add_argument(
        "-n",
        "--new",
        action="store_true",
        help="Immediately start generating a new audio from clipboard.",
    )
    sound = SoundAgent()
    app = KokoroApp(sound, parser.parse_args(sys.argv[1:]))
    app.run()
