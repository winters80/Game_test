"""
Engine bootstrap — registry loading and AI wiring.

Extracted from ``core/game_engine.py`` so that the engine's ``__init__`` is the
only place that allocates registries, and ``bootstrap.py`` is the only place
that decides *what gets loaded* from config / feature flags / disk.

Both functions take the GameEngine instance as a parameter and assign to its
public attributes. They are not methods on the engine — keeping them free
functions makes the data loading + AI wiring trivially unit-testable (pass a
stub object) and prevents new responsibilities from accreting on the engine.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from config import (
    AI_ENABLED, DATA_DIR, OLLAMA_BASE_URL, OLLAMA_FAST_MODEL, OLLAMA_MODEL,
    OLLAMA_PRELOAD, OLLAMA_TIMEOUT_FAST, feature,
)
from ui import renderer

if TYPE_CHECKING:
    from core.game_engine import GameEngine

logger = logging.getLogger(__name__)


def load_registries(engine: "GameEngine") -> None:
    """Populate all of the engine's registries from JSON on disk.

    Always loads: classes (base + combo), skills, items, scenes, zones,
    lore_fragments, system_messages.

    Feature-gated: species + backgrounds, NPCs, quests, guilds, factions,
    bot templates.
    """
    engine.class_registry.load_from_file(DATA_DIR / "classes" / "base_classes.json")
    engine.class_registry.load_from_file(DATA_DIR / "classes" / "combo_classes.json")
    engine.skill_registry.load_from_dir(DATA_DIR / "skills")
    engine.item_registry.load_from_dir(DATA_DIR / "items")
    # Loaded once so recipes registered at runtime persist for the session.
    from systems.alchemy_system import load_recipes
    engine.recipes = load_recipes(DATA_DIR)
    engine.scene_registry.load_from_dir(
        Path(__file__).parent.parent / "scenes" / "data"
    )

    from systems.world_zones import load_zones
    load_zones(DATA_DIR)

    lore_path = DATA_DIR / "world" / "lore_fragments.json"
    if lore_path.exists():
        engine.lore_data = json.loads(lore_path.read_text(encoding="utf-8"))

    msg_path = DATA_DIR / "world" / "system_messages.json"
    if msg_path.exists():
        engine.system_messages = json.loads(msg_path.read_text(encoding="utf-8"))

    if feature("species_system"):
        from entities.species import SpeciesRegistry
        from systems.species_system import load_backgrounds
        engine.species_registry = SpeciesRegistry()
        engine.species_registry.load_from_file(
            DATA_DIR / "species" / "species_definitions.json"
        )
        engine.backgrounds = load_backgrounds(DATA_DIR)

    if feature("npc_system"):
        from entities.npc import NPCRegistry
        engine.npc_registry = NPCRegistry()
        engine.npc_registry.load_from_dir(DATA_DIR / "npcs")

    if feature("quest_system"):
        from entities.quest import QuestRegistry
        engine.quest_registry = QuestRegistry()
        engine.quest_registry.load_from_file(
            DATA_DIR / "quests" / "quest_templates.json"
        )

    if feature("guild_system"):
        from systems.guilds.guild_loader import load_guild_registry
        engine.guild_registry = load_guild_registry(DATA_DIR)

    if feature("faction_system"):
        from entities.faction import FactionRegistry
        engine.faction_registry = FactionRegistry()
        engine.faction_registry.load_from_file(
            DATA_DIR / "factions" / "faction_definitions.json"
        )

    if feature("bot_system"):
        from systems.bot_system import BotRegistry, BotManager
        bot_registry = BotRegistry()
        bot_path = DATA_DIR / "bots" / "bot_templates.json"
        if bot_path.exists():
            bot_registry.load_from_file(bot_path)
        gen_path = DATA_DIR / "bots" / "bot_generation.json"
        if gen_path.exists():
            bot_registry.load_generation(gen_path)
        engine._bot_registry = bot_registry
        # Filled per save by core/bot_flow.setup_bots (new game / load).
        engine._bot_manager = BotManager()


def setup_ai(engine: "GameEngine") -> None:
    """Wire up Ollama clients, ContentGenerator, BackgroundGenerator,
    WorldDirector, and the AIService facade on the engine.

    Always creates an AIService (empty when AI is disabled) so callers have a
    stable boundary object whose ``.is_available`` reflects reality.
    """
    from ai.ai_service import AIService

    # Empty placeholder — replaced below if Ollama is reachable.
    engine.ai_service = AIService()

    if not AI_ENABLED:
        return

    try:
        from ai.ollama_client import OllamaClient
        from ai.content_generator import ContentGenerator
        client = OllamaClient(model=OLLAMA_MODEL, base_url=OLLAMA_BASE_URL)
        if not client.is_available():
            renderer.console.print(
                "  [dim_text]Ollama not available — AI features disabled.[/dim_text]\n"
                f"  [dim_text]Nothing is answering at {OLLAMA_BASE_URL}. Start Ollama (open the "
                "Ollama app, or run `ollama serve`), or launch with scripts/start.ps1 / "
                "scripts/start.sh, which do it for you.[/dim_text]"
            )
            return
        if not client.has_model():
            renderer.console.print(
                f"  [dim_text]Ollama is running, but the model '{OLLAMA_MODEL}' isn't downloaded "
                f"— AI features disabled.\n  Run: ollama pull {OLLAMA_MODEL}[/dim_text]"
            )
            return

        fast_model = OLLAMA_FAST_MODEL or OLLAMA_MODEL
        if fast_model != OLLAMA_MODEL and not client.has_model(fast_model):
            renderer.console.print(
                f"  [dim_text]Fast model '{fast_model}' isn't downloaded; using '{OLLAMA_MODEL}' "
                f"for quick questions too (slower). Run: ollama pull {fast_model}[/dim_text]"
            )
            fast_model = OLLAMA_MODEL
        fast_client = (
            OllamaClient(
                model=fast_model, base_url=OLLAMA_BASE_URL,
                default_timeout=OLLAMA_TIMEOUT_FAST,
            )
            if fast_model != OLLAMA_MODEL else client
        )
        # Kept local on purpose: everything outside ai/ reaches the generator
        # through engine.ai_service, never directly.
        generator = ContentGenerator(
            client, engine.lore_data, OLLAMA_MODEL, fast_client=fast_client,
        )

        if feature("world_db"):
            from config import BG_GEN_ENABLED
            if BG_GEN_ENABLED:
                from ai.background_generator import BackgroundGenerator
                engine._bg_generator = BackgroundGenerator(generator)
                engine._bg_generator.start()

        if engine._bg_generator:
            from ai.world_director import WorldDirector
            engine._world_director = WorldDirector(
                engine._bg_generator,
                generator.client,
                generator.lore_data,
            )
            logger.info("WorldDirector initialized.")

        engine.ai_service = AIService(
            content_generator=generator,
            background_generator=engine._bg_generator,
        )
        renderer.print_success("AI system online. Ollama connected.")
        if OLLAMA_PRELOAD:
            _preload_models(client, fast_client)
    except Exception as e:
        renderer.console.print(
            f"  [dim_text]AI setup failed: {e} — continuing without AI.[/dim_text]"
        )


def _preload_models(*clients) -> None:
    """Load each distinct model into Ollama's memory on a daemon thread.

    A cold 7 GB model can take longer to load than the fast-call timeout, so
    without this the first "[?]" question after launch tends to fail.
    Non-blocking: the title menu appears immediately.
    """
    import threading

    distinct = list({id(c): c for c in clients if c is not None}.values())

    def _run() -> None:
        for c in distinct:
            if c.warm_up():
                logger.info("Preloaded Ollama model %s", c.model)

    threading.Thread(target=_run, name="ollama-preload", daemon=True).start()
