"""Brand Guide loader — reads character persona JSON and provides it to all agents."""

import json
import os
from typing import Optional


_CONFIGS_DIR = os.path.join(os.path.dirname(__file__), "config")

# Cache loaded guides in memory
_cache: dict[str, dict] = {}


def load_brand_guide(character_name: str) -> dict:
    """Load a brand guide JSON by character name (e.g. 'lanna_danger').

    Looks for ``app/agents/config/{character_name}.json``.
    """
    if character_name in _cache:
        return _cache[character_name]

    path = os.path.join(_CONFIGS_DIR, f"{character_name}.json")
    if not os.path.exists(path):
        raise FileNotFoundError(f"Brand guide not found: {path}")

    with open(path, "r", encoding="utf-8") as f:
        guide = json.load(f)

    _cache[character_name] = guide
    return guide


def list_brand_guides() -> list[str]:
    """Return names of all available brand guides."""
    guides: list[str] = []
    if not os.path.isdir(_CONFIGS_DIR):
        return guides
    for fname in os.listdir(_CONFIGS_DIR):
        if fname.endswith(".json"):
            guides.append(fname.replace(".json", ""))
    return guides


def get_character_appearance(guide: dict) -> str:
    """Build a text block describing the character's appearance from the guide."""
    char = guide.get("character", {})
    appearance = char.get("appearance", {})
    trigger = char.get("trigger_word", "")

    parts = [f"A {trigger},"]
    if appearance.get("hair"):
        parts.append(f"with {appearance['hair']}.")
    if appearance.get("face"):
        parts.append(f"Her face: {appearance['face']}.")
    if appearance.get("eyes"):
        parts.append(f"Eyes: {appearance['eyes']}.")
    if appearance.get("skin"):
        parts.append(f"Skin: {appearance['skin']}.")
    if appearance.get("makeup"):
        parts.append(f"Makeup: {appearance['makeup']}.")

    return " ".join(parts)


def get_prompt_tips(guide: dict) -> list[str]:
    """Return the prompt-writing tips from the brand guide."""
    template = guide.get("prompt_template", {})
    return template.get("tips", [])


def get_generation_params(guide: dict) -> dict:
    """Return default generation parameters (width, height, lora chain, etc.)."""
    return guide.get("generation", {})


def get_posting_rules(guide: dict) -> dict:
    """Return posting rules (max per day, cooldown, times, etc.)."""
    return guide.get("posting", {})


def get_restrictions(guide: dict) -> list[str]:
    """Return list of content restrictions as readable strings."""
    restrictions = guide.get("restrictions", {})
    lines: list[str] = []
    if restrictions.get("no_nudity"):
        lines.append("No nudity")
    if restrictions.get("no_explicit"):
        lines.append("No explicit content")
    if restrictions.get("cosplay_note"):
        lines.append(restrictions["cosplay_note"])
    if restrictions.get("age_context"):
        lines.append(f"Context: {restrictions['age_context']}")
    return lines
