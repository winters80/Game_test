from rich.theme import Theme

GAME_THEME = Theme({
    # Rarity colors
    "common": "white",
    "uncommon": "green",
    "rare": "bright_blue",
    "epic": "magenta",
    "legendary": "bright_yellow",

    # UI elements
    "title": "bold bright_yellow",
    "subtitle": "bright_cyan",
    "system_msg": "bold bright_cyan",
    "system_warning": "bold bright_red",
    "system_anomaly": "bold magenta",

    # Stats
    "stat_name": "bright_white",
    "stat_value": "bright_green",
    "stat_boost": "bright_green",
    "stat_penalty": "bright_red",

    # Combat
    "damage": "bright_red",
    "heal": "bright_green",
    "miss": "dim white",
    "critical": "bold bright_yellow",

    # Scene
    "scene_title": "bold bright_white",
    "scene_text": "white",
    "option_label": "bright_cyan",
    "option_number": "bold bright_white",
    "option_locked": "dim white",

    # General
    "gold": "bright_yellow",
    "level": "bright_green",
    "xp": "cyan",
    "hp_high": "bright_green",
    "hp_mid": "bright_yellow",
    "hp_low": "bright_red",
    "mp": "bright_blue",
    "border": "bright_black",
    "highlight": "bold bright_white",
    "dim_text": "dim white",
    "error": "bold red",
    "success": "bold bright_green",
})
