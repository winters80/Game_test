#!/usr/bin/env python3
"""
Stage sounds from the purchased SFX packs for the game, laid out like
``assets/audio``, so ``scripts/master_audio.py`` can master them.

    python scripts/stage_packs.py --zips C:/Users/me/Downloads --out D:/Stuff/Sounds/packs_staged
    python scripts/master_audio.py --src D:/Stuff/Sounds/packs_staged --out assets_local/audio

The packs (Urban Cityscape, Sounds of the Metaverse, Cinematic Ambiences,
Drones, Whooshes, Sound Design Essentials) are licensed for use, not for
publishing raw, so the result belongs in ``assets_local/`` (git-ignored).
``PACK_SOUNDS`` says which pack file becomes which game sound; each file is
trimmed to its length here (effects get a short fade-out) and the WAV copy is
used when a pack has both WAV and MP3. Needs ffmpeg.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

# game file (under audio/) : (pack file stem, max seconds)
PACK_SOUNDS = {
    # Gate ambience, one per Gate
    "ambience/gate.rootdeep_caverns": ("PlanetCaveAmbCave EXD011701", 90),
    "ambience/gate.cinder_forge": ("Drone_Planet_Rumble", 90),
    "ambience/gate.tidecall_grotto": ("Backgrounds-Surf-Ext-MCU-Cr_B02-00261", 90),
    "ambience/gate.rime_tomb": ("AiryDroneWDistSub EXD010801", 90),
    "ambience/gate.stormcrown_eyrie": ("Thunderstorm THR02_17", 120),
    "ambience/gate.hollow_of_unmaking": ("SoundDesign-EvilDemonAmbian_B01-07390", 120),
    "ambience/gate.bronze_labyrinth": ("DroneMetallicHar SDT2031702", 90),
    "ambience/gate.fae_hollow": ("AmbienceFantasy DSL01_01", 90),
    "ambience/gate.hydra_fen": ("Thunderstorm PS04_61", 120),
    "ambience/gate.gorgon_garden": ("AmbienceFantasy DSL01_59_4", 90),
    "ambience/gate.drowned_bell": ("WaterOcean 6110_22", 90),
    "ambience/gate.phoenix_ashery": ("Drone_Popcorn_Rumble", 90),
    "ambience/gate.wyrm_hoard": ("Drone BMT01_45", 90),
    "ambience/gate.redcap_fortress": ("AmbienceDrama DSS01_12", 90),
    "ambience/gate.mycelial_kingdom": ("Drone TBD02_13", 90),
    "ambience/gate.chitin_hive": ("CricketsCicadas-Crickets-Ex_B04-02484", 90),
    "ambience/gate.starfall_crater": ("Moon_Surface_03", 90),
    "ambience/gate.stopped_clocktower": ("Drone TBD01_26", 90),
    "ambience/gate.dawnspire_observatory": ("DroneSpaceAmbChim HYP027301", 90),
    "ambience/gate.dreamers_abyss": ("Drone_Cosmic_Swells", 90),
    "ambience/gate.ashgrove_manor": ("AmbienceOminous MET02_50_1", 90),
    "ambience/gate.witchs_bog": ("Drones-DesignAmbiance-Hol-6_B04-03747", 90),
    "ambience/gate.dollmaker_workshop": ("Drone TBD02_27", 90),
    "ambience/gate.veras_asylum": ("DroneUpDownScary SDT2031601", 90),
    "ambience/gate.hall_of_mirrors": ("MusicalEffectAli ELE01_47_1", 90),
    "ambience/gate.bellfall_cathedral": ("Drone TBD01_17", 90),
    "ambience/gate.clockwork_foundry": ("Drones-LowEndRumble-BigIndu_B04-03838", 90),
    "ambience/gate.golem_assembly": ("AmbienceCargoBayA HYP016401", 90),
    "ambience/gate.derelict_skyship": ("PlanetAtmosphere HI054501", 90),
    "ambience/gate.archive_vault": ("Drones-Telemetry-LowKey_B02-00989", 90),
    "ambience/gate.storm_engine": ("MagneticDroneBras HYP037301", 90),
    "ambience/gate.thaumic_reactor": ("SCI-FI-DRONE_GEN-HDF-20635", 90),
    # Scenes and spots that had no ambience of their own
    "ambience/prologue": ("AmbienceDrama DSS01_07", 90),
    "ambience/character_creation": ("ScifiAmbienceblips AC044501", 90),
    "ambience/ending_rule": ("Afterlife_Drone_01", 90),
    "ambience/ending_destroy": ("AmbienceSciFi 8005_35", 90),
    "ambience/verath_city.palace_district": ("AmbienceFantasy DSL01_36", 90),
    "ambience/verath_city.mages_spire": ("MysticMatrixDrone TFX2975", 90),
    "ambience/dungeon_floor1.crystal_chamber": ("AmbienceCrystalPa HYP016501", 90),
    "ambience/dungeon_floor2.crystal_formations": ("AmbCrystalPalaceH HYP016601", 90),
    "ambience/dungeon_floor2.boss_chamber": ("DroneLowEndSubPa SDT2031301", 90),
    "ambience/dungeon_floor3.drowned_sanctum": ("WaterUnderwater PS04_75", 90),
    "ambience/dungeon_floor3.system_terminal": ("SCI-FI-AMBIENCE_GEN-HDF-20485", 90),
    # New effects
    "sfx/boss_reveal": ("Impact-Dark_Dark-Impact-With-Deep-Airy-Long-Tail-Low-Buzz_SDT3-0471", 4.5),
    "sfx/boss_warning": ("Riser-Buzzing-Cut-Off_TFX03-374", 3),
    "sfx/enrage": ("AnimalStingerBull SDT037002", 3),
    "sfx/summon": ("ShortApproachAnd WHO02_77_2", 3),
    "sfx/life_lost": ("Sci-Fi-Power-Down_GEN-HD2-31613", 4),
    # Extra takes of existing effects (played at random alongside the originals)
    "sfx/swing_2": ("WhooshLngWhoosh SFT085302", 0.8),
    "sfx/swing_3": ("Whooshes EC07_83_1", 0.8),
    "sfx/dodge_2": ("WhooshShortFlash SDT030301", 0.8),
    "sfx/spell_2": ("Zaps WHO05_86_1", 1.4),
    "sfx/spell_3": ("ThunderWhooshes WHO06_45_1", 1.8),
    "sfx/gate_enter_2": ("SoundDesign-SuckingWhoosh-1_B03-06450", 2.5),
    "sfx/rift_open_2": ("StingerSwirlingApp TFX10136", 3.5),
    "sfx/warden_whistle_2": ("TrafficCopWhistleC CT042901", 2),
}



def pack_files(zips: Path) -> dict[str, tuple[Path, str]]:
    """{file stem: (zip, member)} over every .zip in ``zips``; WAV wins over MP3."""
    found: dict[str, tuple[Path, str]] = {}
    for z in sorted(zips.glob("*.zip")):
        try:
            names = zipfile.ZipFile(z).namelist()
        except zipfile.BadZipFile:
            continue
        for n in names:
            stem, _, ext = Path(n).name.rpartition(".")
            if ext.lower() == "wav" or (ext.lower() == "mp3" and stem not in found):
                found[stem] = (z, n)
    return found


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[1])
    ap.add_argument("--zips", required=True, help="folder holding the pack .zip files")
    ap.add_argument("--out", required=True, help="where to write the staged originals")
    args = ap.parse_args(argv)
    files = pack_files(Path(args.zips))
    missing = sorted({s for s, _ in PACK_SOUNDS.values()} - set(files))
    if missing:
        print("Not found in any zip:", *missing, sep="\n  ")
        return 1
    out = Path(args.out)
    with tempfile.TemporaryDirectory() as tmp:
        for rel, (stem, secs) in PACK_SOUNDS.items():
            z, member = files[stem]
            raw = Path(tmp) / Path(member).name
            raw.write_bytes(zipfile.ZipFile(z).read(member))
            dest = out / f"{rel}.wav"
            dest.parent.mkdir(parents=True, exist_ok=True)
            fade = min(0.25, secs / 6) if rel.startswith("sfx/") else 0
            af = f"afade=t=out:st={secs - fade:.3f}:d={fade:.3f}" if fade else "anull"
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(raw), "-t", str(secs), "-af", af, str(dest)],
                           check=True)
            raw.unlink()
            print(f"  {rel:46} <- {Path(member).name}")
    print(f"{len(PACK_SOUNDS)} sounds staged in {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
