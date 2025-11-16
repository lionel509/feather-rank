import discord

def _point_options(target: int, cap: int | None) -> list[discord.SelectOption]:
    """Generate point options for a given target and cap."""
    hi = cap or (30 if target >= 21 else 15)
    return [discord.SelectOption(label=str(i), value=str(i)) for i in range(0, hi + 1)]

def point_options(target:int, cap:int|None) -> list[discord.SelectOption]:
    """Generate point options for a given target and cap (legacy wrapper)."""
    return _point_options(target, cap)

class PointsSelect(discord.ui.Select):
    def __init__(self, set_idx:int, side:str, target:int, cap:int|None):
        self.set_idx, self.side = set_idx, side  # side: "A" or "B"
        opts = point_options(target, cap)
        ph = f"Set {set_idx} — {('A' if side=='A' else 'B')} points"
        super().__init__(placeholder=ph, min_values=1, max_values=1, options=opts)

    async def callback(self, interaction: discord.Interaction):
        self.view.choices.setdefault(self.set_idx, {"A": None, "B": None})
        self.view.choices[self.set_idx][self.side] = int(self.values[0])
        await interaction.response.defer()

class PointsScoreView(discord.ui.View):
    def __init__(self, target:int, cap:int|None, on_submit):
        super().__init__(timeout=120)
        self.target, self.cap, self.on_submit = target, cap, on_submit
        self.choices: dict[int, dict[str,int|None]] = {}
        # 6 boxes: (S1A,S1B, S2A,S2B, S3A,S3B)
        for s in (1,2,3):
            self.add_item(PointsSelect(s, "A", target, cap))
            self.add_item(PointsSelect(s, "B", target, cap))

    def _sets_filled_min2(self) -> bool:
        done = 0
        for s in (1,2,3):
            v = self.choices.get(s)
            if v and v.get("A") is not None and v.get("B") is not None:
                done += 1
        return done >= 2

    @discord.ui.button(label="Submit", style=discord.ButtonStyle.success)
    async def submit(self, _, interaction: discord.Interaction):
        if not self._sets_filled_min2():
            return await interaction.response.send_message("Please select scores for at least **two** sets.", ephemeral=True)
        sets = []
        for i in (1,2,3):
            v = self.choices.get(i)
            if v and v.get("A") is not None and v.get("B") is not None:
                sets.append({"A": int(v["A"]), "B": int(v["B"])});
        await self.on_submit(interaction, sets)

# --- New pager-based scoring UI with two-tier number picker ---

def _ranges_for_cap(cap:int, max_display:int|None=None) -> list[tuple[int,int]]:
    """
    Build compact ranges for the number picker without exceeding the 25-option
    limit of Discord selects.

    Previously this always included the deuce band (22..cap) when target=21,
    which made sense for full-range entry but was confusing when we only want
    to allow values up to the target in the per-side picker. To better fit the
    “scores can range 0–21” expectation, we cap the visible ranges at
    `max_display` when provided.

    Examples:
      - cap=30, max_display=21 -> [(0,10), (11,21)]
      - cap=15, max_display=11 -> [(0,10), (11,11)]
      - cap=15, max_display=None -> [(0,10), (11,15)]
    """
    m = min(cap, max_display) if max_display is not None else cap
    if m <= 15:
        return [(0,10), (11,m)]
    if m <= 21:
        return [(0,10), (11,m)]
    # If the maximum exceeds 21, split off the deuce/high band
    return [(0,10), (11,21), (22,m)]

class NumberPicker(discord.ui.Select):
    def __init__(self, set_idx:int, side:str, target:int, cap:int|None, value:int|None=None, row:int|None=None):
        self.set_idx, self.side = set_idx, side
        self.cap = cap or (30 if target >= 21 else 15)
        # Limit the UI picker to the target by default (e.g., 0–21), to avoid
        # showing the 22–30 band in the first step. Users can still submit
        # deuce scores via the dedicated paired-score selector.
        self.max_display = min(self.cap, target)
        self._value = value
        self.mode = "range"  # or "exact"
        self.current_range: tuple[int,int] | None = None
        super().__init__(placeholder=self._ph(), min_values=1, max_values=1, options=self._range_options(), row=row)

    def _ph(self) -> str:
        suffix = f" (picked {self._value})" if self._value is not None else ""
        who = "A" if self.side == "A" else "B"
        return f"Set {self.set_idx} — {who} points{suffix}"

    def _range_options(self):
        opts = [discord.SelectOption(label=f"{lo}–{hi}", value=f"R:{lo}:{hi}") for lo,hi in _ranges_for_cap(self.cap, self.max_display)]
        if self._value is not None:
            opts.append(discord.SelectOption(label=f"Clear (was {self._value})", value="CLR"))
        return opts

    def _exact_options(self, lo:int, hi:int):
        return [discord.SelectOption(label=str(i), value=f"N:{i}") for i in range(lo, hi+1)]

    async def _to_exact(self, interaction: discord.Interaction, lo:int, hi:int):
        self.mode = "exact"
        self.current_range = (lo, hi)
        self.options = self._exact_options(lo, hi)
        self.placeholder = self._ph()
        await interaction.response.edit_message(view=self.view)

    async def _to_range(self, interaction: discord.Interaction):
        self.mode = "range"
        self.current_range = None
        self.options = self._range_options()
        self.placeholder = self._ph()
        await interaction.response.edit_message(view=self.view)

    async def callback(self, interaction: discord.Interaction):
        v = self.values[0]
        if v == "CLR":
            self._value = None
            view = getattr(self, "view", None)
            if view is not None:
                view.choices.setdefault(self.set_idx, {"A": None, "B": None})
                view.choices[self.set_idx][self.side] = None
            await self._to_range(interaction)
            return
        if v.startswith("R:"):
            _, lo, hi = v.split(":")
            await self._to_exact(interaction, int(lo), int(hi))
            return
        if v.startswith("N:"):
            num = int(v.split(":")[1])
            self._value = num
            view = getattr(self, "view", None)
            if view is not None:
                view.choices.setdefault(self.set_idx, {"A": None, "B": None})
                view.choices[self.set_idx][self.side] = num
            await self._to_range(interaction)
            return

class PointsScorePagerView(discord.ui.View):
    def __init__(self, target:int, cap:int|None, on_submit):
        super().__init__(timeout=180)
        self.target, self.cap, self.on_submit = target, cap, on_submit
        self.choices = {1: {"A": None, "B": None}, 2: {"A": None, "B": None}, 3: {"A": None, "B": None}}
        self.page = 1
        self._render()

    def _complete_sets_count(self) -> int:
        return sum(1 for i in (1,2,3) if self.choices[i]["A"] is not None and self.choices[i]["B"] is not None)

    def _render(self):
        self.clear_items()
        s = self.page
        self.add_item(NumberPicker(s, "A", self.target, self.cap, value=self.choices[s]["A"], row=0))
        self.add_item(NumberPicker(s, "B", self.target, self.cap, value=self.choices[s]["B"], row=1))
        # nav row
        if self.page > 1:
            back = discord.ui.Button(label="◀ Back", style=discord.ButtonStyle.secondary, row=2)
            async def _back(interaction:discord.Interaction):
                self.page -= 1
                self._render()
                await interaction.response.edit_message(view=self)
            back.callback = _back
            self.add_item(back)
        if self.page < 3:
            nxt = discord.ui.Button(label="Next ▶", style=discord.ButtonStyle.primary, row=2)
            async def _next(interaction:discord.Interaction):
                self.page += 1
                self._render()
                await interaction.response.edit_message(view=self)
            nxt.callback = _next
            self.add_item(nxt)
        else:
            submit = discord.ui.Button(label="Submit", style=discord.ButtonStyle.success, row=2)
            async def _submit(interaction:discord.Interaction):
                if self._complete_sets_count() < 2:
                    return await interaction.response.send_message("Please enter scores for at least **two** sets.", ephemeral=True)
                sets = []
                for idx in (1,2,3):
                    a, b = self.choices[idx]["A"], self.choices[idx]["B"]
                    if a is not None and b is not None:
                        sets.append({"A": int(a), "B": int(b)})
                await self.on_submit(interaction, sets)
            submit.callback = _submit
            self.add_item(submit)
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
            view = getattr(self, "view", None)
            if view is not None and hasattr(view, "show_deuce_for"):
                await view.show_deuce_for(self.set_idx, interaction)
            else:
                await interaction.response.defer()
            return
        view = getattr(self, "view", None)
        if view is not None and hasattr(view, "store_choice"):
            view.store_choice(v)
            await interaction.response.edit_message(view=view)
        else:
            await interaction.response.defer()


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
        view = getattr(self, "view", None)
        if view is not None and hasattr(view, "store_choice") and hasattr(view, "show_standard"):
            view.store_choice(self.values[0])
            await view.show_standard(interaction)  # go back to main view
        else:
            await interaction.response.defer()


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
