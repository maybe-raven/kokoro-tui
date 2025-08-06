import asyncio
import json
import os
import sys
from argparse import ArgumentParser, Namespace
from dataclasses import dataclass
from typing import ClassVar, Optional, Type

from pyperclip import paste
from textual import log, on, work
from textual._path import CSSPathType
from textual.actions import SkipAction
from textual.app import App, ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal
from textual.driver import Driver
from textual.widgets import (
    Footer,
    ListView,
    LoadingIndicator,
)

from lib.agents.kokoro import KokoroAgent
from lib.agents.sound import SoundAgent
from lib.screens import ConfigScreen, ConfirmationScreen, FilepathInput
from lib.widgets import AudioList, SourceView


def get_text_from_paste():
    text = paste()
    if not text.endswith("\n"):
        text += "\n"
    return text


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
        self.screen_stack[0].query_one(LoadingIndicator).display = (
            visibility or self.kokoro.is_processing()
        )

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
            self.screen_stack[0].query_one(SourceView).overwrite(text)
            await self.screen_stack[0].query_one(AudioList).append(text)
        else:
            self.kokoro.feed(text=text, index=self.index, generation=self.generation)
            self.texts[self.index] += "\n" + text
            self.screen_stack[0].query_one(SourceView).write_str(text)
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
