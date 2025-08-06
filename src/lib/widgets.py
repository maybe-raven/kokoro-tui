import asyncio
from datetime import datetime
from typing import Self

from textual import work
from textual.app import ComposeResult
from textual.containers import VerticalGroup
from textual.events import Resize
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import (
    Label,
    ListItem,
    ListView,
    RichLog,
)


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
