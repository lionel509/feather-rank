
def bold(t:str)->str: return f"**{t}**"
def code(t:str)->str: return f"`{t}`"
def block(t:str, lang:str|None=None)->str: return f"```{lang or ''}\n{t}\n```"
def mention(uid:int)->str: return f"<@{uid}>"
def score_sets(sets:list[dict])->str: return " | ".join(f"{s.get('A',0)}–{s.get('B',0)}" for s in sets if s)
