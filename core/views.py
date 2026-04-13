"""Discord UI views for the yomiage bot."""

from typing import Callable

import discord


class SetvoiceView(discord.ui.View):
    """Paginated dropdown view for selecting a voice speaker and style."""

    OPTIONS_PER_PAGE = 25

    def __init__(
        self,
        layered_speakers_list: list[dict],
        setvoice_callback: Callable,
        timeout: float | None = None,
    ):
        super().__init__(timeout=timeout)
        self._callback = setvoice_callback

        names = [s["name"] for s in layered_speakers_list]
        styles = [s["styles"] for s in layered_speakers_list]

        self._name_pages = [
            names[i : i + self.OPTIONS_PER_PAGE]
            for i in range(0, len(names), self.OPTIONS_PER_PAGE)
        ]
        self._style_pages = [
            styles[i : i + self.OPTIONS_PER_PAGE]
            for i in range(0, len(styles), self.OPTIONS_PER_PAGE)
        ]

        self._page = 1
        self._total_pages = len(self._name_pages)

        self._refresh_name_dropdown()
        self._refresh_style_dropdown(self._name_pages[0][0])
        self._refresh_buttons()

    def _refresh_buttons(self) -> None:
        self.btn_prev.disabled = self._page <= 1
        self.btn_next.disabled = self._page >= self._total_pages
        self.btn_page.label = f"{self._page}/{self._total_pages}"

    def _refresh_name_dropdown(self) -> None:
        page = self._name_pages[self._page - 1]
        self.name_select.options = [
            discord.SelectOption(label=n) for n in page
        ]
        self.name_select.placeholder = page[0]

    def _refresh_style_dropdown(self, name: str) -> None:
        idx = self._page - 1
        name_idx = self._name_pages[idx].index(name)
        styles = self._style_pages[idx][name_idx]
        self.style_select.options = [
            discord.SelectOption(label=s) for s in styles
        ]
        self.style_select.placeholder = "style"

    def _change_page(self, delta: int) -> None:
        self._page += delta
        self._refresh_name_dropdown()
        self._refresh_style_dropdown(self._name_pages[self._page - 1][0])
        self._refresh_buttons()

    def disable_all(self) -> None:
        for item in self.children:
            if isinstance(item, (discord.ui.Button, discord.ui.Select)):
                item.disabled = True

    @discord.ui.button(label="<", style=discord.ButtonStyle.red)
    async def btn_prev(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self._page > 1:
            self._change_page(-1)
            await interaction.response.edit_message(view=self)
        else:
            await interaction.response.defer()

    @discord.ui.button(label="0/0", style=discord.ButtonStyle.gray, disabled=True)
    async def btn_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        pass

    @discord.ui.button(label=">", style=discord.ButtonStyle.green)
    async def btn_next(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self._page < self._total_pages:
            self._change_page(1)
            await interaction.response.edit_message(view=self)
        else:
            await interaction.response.defer()

    @discord.ui.select(cls=discord.ui.Select, placeholder="name", options=[])
    async def name_select(self, interaction: discord.Interaction, select: discord.ui.Select):
        selected = select.values[0]
        self.name_select.placeholder = selected
        self._refresh_style_dropdown(selected)
        await interaction.response.edit_message(view=self)

    @discord.ui.select(cls=discord.ui.Select, placeholder="style", options=[])
    async def style_select(self, interaction: discord.Interaction, select: discord.ui.Select):
        selected = select.values[0]
        self.style_select.placeholder = selected
        await interaction.response.edit_message(view=self)

        speaker_id = int(selected.split(" id:")[1])
        await self._callback(interaction, speaker_id)
