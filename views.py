def gen_standard_scores(target: int):
    # A wins normal: target–0 .. target–(target-11)
    std = []
    for x in range(0, max(0, target - 10)):
        std.append(("A", target, x))   # A target–x
    for x in range(0, max(0, target - 10)):
        std.append(("B", x, target))   # B x–target
    return std  # <= 22 entries at target=21, <= 20 at target=11


def gen_deuce_scores(target: int, win_by: int = 2, cap: int | None = None):
    scores = []
    # e.g., 22–20, 23–21, ... up to cap or until it stops making sense
    top = cap if cap else target + 9  # default 30 for 21, 15 for 11 via caller
    for m in range(target + 1, top + 1):
        a, b = m, m - win_by
        scores.append(("A", a, b))
        scores.append(("B", b, a))
    return scores


import discord


class SetScoreSelect(discord.ui.Select):
    def __init__(self, set_idx: int, target: int, cap: int | None):
        self.set_idx = set_idx
        self.target = target
        self.cap = cap
        opts = []
        # Standard page (<=25 options total)
        for side, a, b in gen_standard_scores(target):
            label = f"Set {set_idx}: {a}–{b}" if side == "A" else f"Set {set_idx}: {a}–{b}"
            value = f"{set_idx}:{a}:{b}"
            opts.append(discord.SelectOption(label=label, value=value))
            if len(opts) >= 24:
                break
        opts.append(discord.SelectOption(label="More (deuce & high scores)…", value=f"DEUCE:{set_idx}"))
        super().__init__(placeholder=f"Set {set_idx} score", min_values=1, max_values=1, options=opts)

    async def callback(self, interaction: discord.Interaction):
        v = self.values[0]
        if v.startswith("DEUCE:"):
            await self.view.show_deuce_for(self.set_idx, interaction)
            return
        self.view.store_choice(v)
        await interaction.response.edit_message(view=self.view)


class DeuceScoreSelect(discord.ui.Select):
    def __init__(self, set_idx: int, target: int, cap: int | None):
        self.set_idx = set_idx
        opts = []
        for side, a, b in gen_deuce_scores(target, 2, cap):
            label = f"Set {set_idx}: {a}–{b}"
            opts.append(discord.SelectOption(label=label, value=f"{set_idx}:{a}:{b}"))
            if len(opts) >= 25:
                break
        super().__init__(placeholder=f"Set {set_idx} deuce score", min_values=1, max_values=1, options=opts)

    async def callback(self, interaction: discord.Interaction):
        self.view.store_choice(self.values[0])
        await self.view.show_standard(interaction)  # go back to main view


class ScoreSelectView(discord.ui.View):
    def __init__(self, target: int, cap: int | None, on_submit):
        super().__init__(timeout=120)
        self.target = target
        self.cap = cap
        self.on_submit = on_submit
        self.choices = {}  # key set_idx -> (A,B)
        # 3 set selectors
        self.set1 = SetScoreSelect(1, target, cap)
        self.set2 = SetScoreSelect(2, target, cap)
        self.set3 = SetScoreSelect(3, target, cap)
        self.add_item(self.set1)
        self.add_item(self.set2)
        self.add_item(self.set3)
        # Submit button is added by decorator, but we need to find it for re-adding
        self.submit_button = None
        for item in self.children:
            if isinstance(item, discord.ui.Button) and item.label == "Submit":
                self.submit_button = item
                break

    def _update_submit_button(self):
        """Update submit button state based on whether Set 1 and Set 2 are filled."""
        if self.submit_button:
            # Disable if Set 1 or Set 2 is missing
            self.submit_button.disabled = not (1 in self.choices and 2 in self.choices)

    def store_choice(self, value: str):
        _, a, b = value.split(":")  # "set:a:b"
        s = int(value.split(":")[0])
        self.choices[s] = (int(a), int(b))
        self._update_submit_button()

    async def show_deuce_for(self, set_idx: int, interaction: discord.Interaction):
        self.clear_items()
        self.add_item(DeuceScoreSelect(set_idx, self.target, self.cap))
        await interaction.response.edit_message(view=self)

    async def show_standard(self, interaction: discord.Interaction):
        self.clear_items()
        for s in (self.set1, self.set2, self.set3):
            self.add_item(s)
        if self.submit_button:
            self.add_item(self.submit_button)
        self._update_submit_button()  # Update button state
        await interaction.response.edit_message(view=self)

    @discord.ui.button(label="Submit", style=discord.ButtonStyle.success, disabled=True)
    async def submit(self, interaction: discord.Interaction, button: discord.ui.Button):
        sets = []
        for i in (1, 2, 3):
            if i in self.choices:
                a, b = self.choices[i]
                sets.append({"A": a, "B": b})
        await self.on_submit(interaction, sets)
