# Art and audio

Drop files here and the game uses them; leave them out and it stays text-only.
Nothing is required. `ASSET_LIST.md` lists every file the game can use, with a
ready-made prompt for each (regenerate it with `python scripts/asset_list.py`;
`--missing` lists only what you don't have yet).

| Folder | File name | Shown / played |
|---|---|---|
| `images/scenes/` | `<scene_id>.png`, or `<scene_id>.<node_id>.png` for one spot | when you're there |
| `images/npcs/` | `<npc_id>.png` | when you talk to them (`ledger_warden`, `ledger_hunter` when the Ledger stops you) |
| `images/bosses/` | `<occupant_id>.png` (or the enemy template id) | when you enter a boss's lair |
| `images/items/` | `<item_id>.png` | when you inspect an item |
| `audio/sfx/` | `level_up.ogg`, `hit.ogg`, `warden_whistle.ogg`, `gate_enter.ogg`, `crystal_shatter.ogg`, `ledger_seal.ogg`, `potion.ogg`, … | on game events |
| `audio/ambience/` | `<scene_id>.ogg` (or `<scene_id>.<node_id>.ogg`), `gate.ogg` (or `gate.<gate_id>.ogg`) | looped while you're there / inside a Gate |
| `audio/music/` | `explore.ogg`, `combat.ogg`, `boss.ogg`, `verath.ogg`, `ledger.ogg`, … | by place and in fights |
| `audio/voice/system/` | `classification_complete.ogg`, `level_up.ogg`, … | the System speaking |
| `audio/voice/<npc_id>/` | `greeting.ogg` | when you start talking to them |
| `audio/voice/bots/<archetype>/` | `greeting.ogg` (explorer, fighter, crafter, trader; others use trader) | when you talk to a bot adventurer |

Images: `.png` `.jpg` `.jpeg` `.webp`. Sound: `.ogg` `.mp3` `.wav` (`.ogg` is
smallest for loops). Pictures appear in the panel and browser versions
(`python main.py --tui` / `--web`); sound plays on the computer running the game
and needs `pygame-ce` (in requirements.txt). Turn each layer on or off, and set
volumes, in the game under Admin → Art & audio.

## Files you may not publish: `assets_local/`

`assets_local/` has the same layout and is git-ignored; the game looks there
first. Put Pixabay, Sonniss or other files there whose license allows them in
a game but not as raw files in a public repository. Only CC0 / your own files go
in `assets/`. `scripts/fetch_sounds.py` builds a pack from Kenney's CC0 packs plus
any folders you add (`--extra FOLDER:LICENSE`) and records each file's license
in `sounds_manifest.json`.

## What's here already

56 sounds (OGG): five music tracks (`explore`, `combat`, `ledger`, `boss`, `camp`;
other moods borrow one, see `media.MUSIC_FALLBACK`), 17 effects, six ambience
loops, three System lines, three takes each for six people
(`voice/<npc_id>/greeting_1..3`) and four bot types (`voice/bots/<archetype>/greeting_1..3`).


20 pictures (WebP), made from `art_and_audio_direction.md`. Some spots share a
picture (`media.ART_ALIASES`): both Gate Halls show `scenes/gate`, and the rest of
the Fracture Core shows `dungeon_floor3.core_chamber`. A Gate shows
`scenes/gate.<gate_id>` if you add one, else `scenes/gate`.

`audio_prompts/` is the audio prompt pack: 32 specs with `manifest.json` /
`manifest.csv`. `scripts/asset_list.py` (`PACK_TARGETS`) maps each spec to the
file the game plays, and `ASSET_LIST.md` shows it with its id in brackets.

In the browser version (`python main.py --web`) pictures are shown full size
beside the game. In a terminal they look sharpest with Kitty or Sixel graphics;
elsewhere they're drawn with coloured half-blocks.
