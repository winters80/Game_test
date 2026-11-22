"""
Bot small talk that sounds like another player, not a combat log.

Pure functions, no AI and no I/O. ``core/bot_flow.talk_to_bot`` gathers the
facts (the player's level, Gates that suit them, whether the bot would come
along) and either asks the fast model for a line (``AIService.generate_bot_reply``)
or uses the replies here.

- ``summarize_memory`` folds a bot's raw memory ("Defeated a dungeon slime on
  Floor 1 (turn 12)" ×3 …) into what a person would say: "been grinding Floor
  1: three fights, mostly crystal spiders".
- ``reply`` answers a topic (catch_up / tips / build / team_up / guild /
  greeting) in the bot's archetype voice, varied per visit but deterministic
  for a given ``seed``.
"""
from __future__ import annotations

import random
import re
from collections import Counter, OrderedDict
from dataclasses import dataclass, field

TOPICS = ("greeting", "catch_up", "tips", "build", "team_up", "guild")

_NUM = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six",
        7: "seven", 8: "eight", 9: "nine", 10: "ten"}


def _n(k: int) -> str:
    return _NUM.get(k, str(k))


def _strip_article(name: str) -> str:
    return re.sub(r"^(a|an|the)\s+", "", name.strip(), flags=re.I)


def _plural(name: str) -> str:
    name = _strip_article(name)
    for sing, plur in (("nest of ", "nests of "), ("pack of ", "packs of "), ("swarm of ", "swarms of ")):
        if name.lower().startswith(sing):
            return plur + name[len(sing):]
    if name.endswith(("s", "x", "z", "sh", "ch")):
        return name + "es"
    if name.endswith("y") and not name.endswith(("ay", "ey", "oy", "uy")):
        return name[:-1] + "ies"
    return name + "s"


# ── Memory → what happened ───────────────────────────────────────────────────

_PATTERNS = [
    ("won",      re.compile(r"^Defeated (?P<what>.+?) on (?P<where>.+)$")),
    ("lost",     re.compile(r"^Beaten by (?P<what>.+?) (?:on|in) (?P<where>.+)$")),
    ("slew",     re.compile(r"^Slew (?P<what>.+?) in (?P<where>.+)$")),
    ("boss",     re.compile(r"^Fought (?P<what>.+?) in (?P<where>.+?) with (?P<who>.+)$")),
    ("gate",     re.compile(r"^Ran (?P<where>.+?)(?: with (?P<who>.+))?$")),
    ("cleared",  re.compile(r"^Cleared (?P<where>.+?) with (?P<who>.+)$")),
    ("badrun",   re.compile(r"^Went into (?P<where>.+?) with (?P<who>.+?); it went badly$")),
    ("guild",    re.compile(r"^Joined (?P<what>.+)$")),
    ("won_auction", re.compile(r"^Won (?P<what>.+?) at auction$")),
    ("listed",   re.compile(r"^Listed (?P<what>.+?) at auction$")),
    ("auction",  re.compile(r"^Sold (?P<what>.+?) at auction to (?P<who>.+)$")),
    ("soldnpc",  re.compile(r"^Sold (?P<count>\d+) items? to (?P<who>.+)$")),
    ("soldyou",  re.compile(r"^Sold (?P<what>.+?) to (?P<who>.+)$")),
    ("agreed",   re.compile(r"^Agreed to run a Gate with (?P<who>.+)$")),
    ("boughtyou", re.compile(r"^Bought (?P<count>\d+)× (?P<what>.+?) from (?P<who>.+)$")),
]


@dataclass
class Happening:
    kind: str
    what: str = ""
    where: str = ""
    who: str = ""
    count: int = 1
    turn: int = 0
    raw: str = ""


def parse_memory(line: str) -> Happening:
    turn = 0
    m = re.search(r"\s*\(turn (\d+)\)\s*$", line)
    if m:
        turn = int(m.group(1))
        line = line[:m.start()]
    line = line.strip().rstrip(".")
    for kind, pat in _PATTERNS:
        hit = pat.match(line)
        if hit:
            g = {k: (v or "") for k, v in hit.groupdict().items()}
            return Happening(kind, g.get("what", ""), g.get("where", ""), g.get("who", ""),
                             int(g.get("count") or 1), turn, line)
    return Happening("other", raw=line, turn=turn)


_PRIORITY = {"slew": 5, "cleared": 5, "badrun": 4, "boss": 4, "gate": 4, "lost": 3, "won": 3,
             "agreed": 3, "guild": 2, "won_auction": 2, "auction": 2, "listed": 1, "soldyou": 2, "boughtyou": 2,
             "soldnpc": 0, "other": 1}


def summarize_memory(memory: list[str], player_name: str = "", limit: int = 4) -> list[str]:
    """Recent events folded into casual first-person phrases, in the order
    they happened. When there are more than ``limit``, the dull ones (selling
    to a trader) give way to fights, Gates and bosses."""
    events = [parse_memory(m) for m in memory]
    found: list[tuple[int, int, str]] = []          # (last index, priority, phrase)

    wins: "OrderedDict[str, Counter]" = OrderedDict()
    last_win: dict[str, int] = {}
    for i, e in enumerate(events):
        if e.kind == "won":
            wins.setdefault(e.where, Counter())[_strip_article(e.what)] += 1
            last_win[e.where] = i
    for where, foes in wins.items():
        total = sum(foes.values())
        top, top_n = foes.most_common(1)[0]
        if total == 1:
            text = f"took out a {top} on {where}"
        elif len(foes) == 1:
            text = f"been grinding {where}: {_n(total)} {_plural(top)}"
        else:
            others = [f for f in foes if f != top]
            tail = f" and a {others[0]}" if len(others) == 1 else " and a few other things"
            lead = f"mostly {_plural(top)}" if top_n > 1 else f"a {top}"
            text = f"been grinding {where}: {_n(total)} fights, {lead}{tail}"
        found.append((last_win[where], _PRIORITY["won"] + (total > 2), text))

    losses: "OrderedDict[str, list[int]]" = OrderedDict()
    for i, e in enumerate(events):
        if e.kind == "lost":
            losses.setdefault(e.what, []).append(i)
    for what, idx in losses.items():
        k = len(idx)
        found.append((idx[-1], _PRIORITY["lost"],
                      f"got flattened by {what}" + (f" {_n(k)} times, which stings" if k > 1 else "")))

    for i, e in enumerate(events):
        you = bool(player_name) and player_name in e.who
        text = ""
        if e.kind == "slew":
            text = f"brought down {e.what} in {e.where}"
        elif e.kind == "boss":
            text = f"went at {e.what} in {e.where} with {e.who}"
        elif e.kind == "gate":
            text = f"ran {e.where}" + (f" with {e.who}" if e.who else " solo")
        elif e.kind == "cleared":
            text = "cleared " + e.where + (" with you, which was a good run" if you else f" with {e.who}")
        elif e.kind == "badrun":
            text = f"went into {e.where}" + (" with you, and we both know how that went" if you
                                             else f" with {e.who}, and it went badly")
        elif e.kind == "guild":
            text = f"signed up with {e.what}"
        elif e.kind == "won_auction":
            text = f"won {e.what} at the auction house"
        elif e.kind == "listed":
            text = f"stuck {e.what} up at the auction house"
        elif e.kind == "auction":
            text = f"sold {e.what} at auction to {e.who}"
        elif e.kind == "soldnpc":
            text = f"offloaded a bag of loot on {e.who}"
        elif e.kind == "soldyou" and you:
            text = f"sold you {e.what}"
        elif e.kind == "agreed" and you:
            text = "said I'd run a Gate with you"
        elif e.kind == "boughtyou" and you:
            text = f"bought some {e.what} off you"
        elif e.kind == "other" and e.raw:
            text = e.raw[0].lower() + e.raw[1:]
        if text:
            found.append((i, _PRIORITY.get(e.kind, 1), text))

    # one entry per phrase (its latest occurrence)
    latest: dict[str, tuple[int, int]] = {}
    for i, pr, text in found:
        if text not in latest or i > latest[text][0]:
            latest[text] = (i, pr)
    ranked = sorted(latest.items(), key=lambda kv: (-kv[1][1], -kv[1][0]))[:limit]
    return [text for text, _ in sorted(ranked, key=lambda kv: kv[1][0])]


def _join(phrases: list[str], word: str = "and") -> str:
    if not phrases:
        return ""
    if len(phrases) == 1:
        return phrases[0]
    if len(phrases) == 2:
        return f"{phrases[0]} {word} {phrases[1]}"
    return ", ".join(phrases[:-1]) + f", {word} " + phrases[-1]


# ── Voices ───────────────────────────────────────────────────────────────────

@dataclass
class ChatContext:
    """Facts ``bot_flow`` hands in. Everything is optional."""
    player_name: str = "you"
    player_level: int = 1
    player_class: str = ""
    bot_class: str = ""                      # display name of the bot's class
    guild_name: str = ""
    gate_picks: list[str] = field(default_factory=list)   # "Hollow Orchard (Lv 3)"
    lair_note: str = ""                      # "the Shardmind holds the Crystal Heart now"
    can_party: bool = False
    party_reason: str = ""                   # why not, in the bot's words
    seed: int = 0


_OPEN = {
    "fighter":  ["Ha. ", "Oi. ", "Heh. ", ""],
    "explorer": ["Oh, hey. ", "Hm? Oh, it's you. ", "Hey. ", ""],
    "gatherer": ["Oh, hello. ", "Hey there. ", "", "Mm. "],
    "trader":   ["Friend! ", "Ah, a customer. ", "Hey, hey. ", ""],
    "crafter":  ["Hm. ", "Oh, hi. ", "", "Hey. "],
}
_HELLO = {
    "fighter":  ["Floor's busy today. Good, I need the XP.",
                 "You look like you've been in a scrap. Same.",
                 "Still alive, then? Nice."],
    "explorer": ["I swear the tunnels moved again since yesterday.",
                 "Just got back from poking around. Weird stuff down there.",
                 "Mapping's slow going, but it's going."],
    "gatherer": ["Mind where you step, there's good stuff growing around here.",
                 "Herbs are coming up nicely this week.",
                 "Bag's half full. Not bad for the morning."],
    "trader":   ["Buying, selling, or just looking? All three are fine.",
                 "Prices are all over the place since the last rift.",
                 "Got a few bits you might like, if you've got the coin."],
    "crafter":  ["Got a batch going back at camp. What's up?",
                 "If you need potions, I'm your best bet.",
                 "Busy, but I've got a minute."],
}
_CLOSE = {
    "fighter":  ["Anyway. Gonna go hit something.", "", "You?", "Not bad for a day."],
    "explorer": ["", "Still more to map.", "What about you?"],
    "gatherer": ["", "Slow and steady.", "How's your haul?"],
    "trader":   ["", "Business is business.", "Need anything?"],
    "crafter":  ["", "Keeps me in reagents.", "You need anything brewed?"],
}
_QUIET = {
    "fighter":  "Honestly? Not much. Waiting for something worth fighting.",
    "explorer": "Not a lot yet. Getting my bearings, same as everyone.",
    "gatherer": "Quiet so far. Picking at whatever grows near the entrance.",
    "trader":   "Slow day. Haven't moved much stock.",
    "crafter":  "Mostly waiting on reagents. Not exciting.",
}
_TIPS = {
    "fighter":  ["Stun the big ones and they lose a turn. Rank C and up shrug off half of it, though.",
                 "Bosses wind up before a heavy hit. When you see it coming, guard or heal, don't swing.",
                 "Below a quarter HP a lot of bosses enrage. Save your burst for when they do."],
    "explorer": ["If you've got the stat for it, study a boss before you fight. You get a free round.",
                 "Gates give you a breather between rooms. Don't burn potions you don't need.",
                 "The dungeon remembers who you killed. Kill a boss with magic and something different moves in."],
    "gatherer": ["Forage between fights. Gate materials sell, and they're half the crafting recipes.",
                 "Anything you dig up with your hands keeps paying out if you go back to it every so often.",
                 "Trophies from Gate masters? Lady Vos in Verath pays well for those."],
    "trader":   ["Never sell your last copy of something you've got equipped. The traders won't even take it.",
                 "The auction house takes a 10% cut, but it's still better than dumping stuff on an NPC.",
                 "Buy potions off crafters, not shops. Cheaper, and we actually make them."],
    "crafter":  ["The salve recipe's the first one worth learning. Cheap reagents, keeps you alive.",
                 "Hold onto Gate materials. Every one of them goes into something at a crafting bench.",
                 "If [C] shows up in your menu, you're carrying something craftable."],
}


def _pick(rng: random.Random, options: list[str]) -> str:
    return options[rng.randrange(len(options))] if options else ""


def _rng(bot, ctx: ChatContext, topic: str) -> random.Random:
    return random.Random(f"{bot.bot_id}:{topic}:{ctx.seed}")


def _say(*parts: str) -> str:
    return " ".join(p.strip() for p in parts if p and p.strip())


def reply(bot, topic: str, ctx: ChatContext | None = None) -> str:
    """What the bot says on ``topic`` (see TOPICS)."""
    ctx = ctx or ChatContext()
    arch = bot.archetype if bot.archetype in _HELLO else "explorer"
    rng = _rng(bot, ctx, topic)
    news = summarize_memory(bot.memory, ctx.player_name)

    if topic == "greeting":
        line = _pick(rng, _OPEN[arch]) + _pick(rng, _HELLO[arch])
        if news and rng.random() < 0.6:
            line = _say(line, f"Just {news[-1]}.")
        return line

    if topic == "catch_up":
        if not news:
            return _QUIET[arch]
        picked = summarize_memory(bot.memory, ctx.player_name, limit=3)
        first = picked[0][0].upper() + picked[0][1:] + "."
        rest = (_pick(rng, ["Then ", "After that I ", "Oh, and I "]) + _join(picked[1:]) + ".") if picked[1:] else ""
        if rest.startswith("After that I ") or rest.startswith("Oh, and I "):
            rest = rest.replace(" I been ", " I've been ")
        lead = _pick(rng, ["Oh, you know. ", "Let's see. ", "Busy. ", "Not bad, actually. ", ""])
        return _say(lead + first, rest, _pick(rng, _CLOSE[arch]))

    if topic == "tips":
        tip = _pick(rng, _TIPS[arch])
        extra = ""
        if ctx.gate_picks:
            extra = f"At your level? I'd try {_join(ctx.gate_picks[:2], 'or')}."
        if ctx.lair_note and rng.random() < 0.5:
            extra = _say(extra, f"Heads up, {ctx.lair_note}.")
        return _say(tip, extra)

    if topic == "build":
        cls = ctx.bot_class or bot.class_id.replace("_", " ")
        s = bot.stats
        stats = {"STR": s.STR, "AGI": s.AGI, "INT": s.INT, "VIT": s.VIT, "WIS": getattr(s, "WIS", 0)}
        main = max(stats, key=stats.get)
        style = {"STR": "hit hard and soak the rest", "AGI": "stay quick and don't get hit",
                 "INT": "blow things up from range", "VIT": "outlast whatever's in front of me",
                 "WIS": "keep the MP flowing and never run dry"}[main]
        line = f"Level {bot.level} {cls}. Mostly {main}, so I {style}."
        if ctx.player_class:
            line = _say(line, f"{ctx.player_class}, right? " + _pick(rng, [
                "Solid pick.", "Didn't think I'd see one of those today.", "Could use one in a party."]))
        return line

    if topic == "team_up":
        if ctx.can_party:
            return _pick(rng, [
                "Yeah, I'm in. Head into a Gate from here and pick me when it asks who's coming.",
                "Sure, why not. I'll hang around. Grab me on your way into a Gate.",
                "Count me in. Just don't leave me tanking the whole master on my own.",
            ])
        return ctx.party_reason or "Not right now, sorry."

    if topic == "guild":
        if ctx.guild_name:
            rank = f" I'm {bot.guild_rank} now." if bot.guild_rank else ""
            return _say(f"I run with {ctx.guild_name}.{rank}", _pick(rng, [
                "Good people. Mostly.", "Beats going it alone.", "Dues are steep but the contacts are worth it."]))
        return _pick(rng, ["No guild yet. Haven't found one that fits.",
                           "Solo for now. Guilds want too much of my loot.",
                           "Thinking about it. Got any recommendations?"])
    return _pick(rng, _HELLO[arch])


def party_refusal(bot, gate_min_level: int | None, player_level: int) -> str:
    """Why a bot won't join (in its own words), or "" if it would."""
    if bot.archetype in ("trader", "crafter"):
        return {"trader": "Me? In a Gate? I sell the swords, I don't swing them.",
                "crafter": "I'm more use to you back at the bench than in a Gate, honestly."}[bot.archetype]
    if bot.hp <= bot.max_hp * 0.5:
        return "Not like this. Let me patch up first, ask me again in a bit."
    if bot.level < player_level - 3:
        return "You'd be carrying me. Let me get a few more levels in first."
    return ""


def looks_robotic(text: str, memory: list[str]) -> bool:
    """True if a model's line just recites the memory log."""
    raw = [parse_memory(m).raw.lower() for m in memory if m]
    t = (text or "").lower()
    hits = sum(1 for r in raw if r and len(r) > 12 and r in t)
    return hits >= 2 or "(turn" in t
