"""Tiny keyword-based "vibe" detector.

No API and no machine learning -- just word matching against titles/artists,
used to (a) label the mood of what is playing, (b) keep autoplay in the same
mood when YouTube's own "mix" data is not available.

    detect_mood("some sad heartbreak song", "Lofi Girl")  ->  "sad"
"""

from __future__ import annotations

import random
import re

# Order matters: the first mood with a keyword hit wins.
MOOD_KEYWORDS: dict[str, list[str]] = {
    "sad": [
        "sad", "sadness", "cry", "crying", "tears", "tear", "heartbreak", "heartbroken",
        "broken heart", "broken", "lonely", "loneliness", "alone", "pain", "painful",
        "emotional", "miss you", "missing you", "goodbye", "farewell", "memories",
        "regret", "depressing", "depressed", "depression", "hurt", "hurtful", "lost",
        "empty", "sorrow", "sorrowful", "tragic", "unhappy", "melancholy",
    ],
    "love": [
        "love", "loving", "romantic", "romance", "valentine", "crush", "kiss", "kissing",
        "baby", "darling", "sweetheart", "wedding", "anniversary", "lover", "beautiful you",
    ],
    "chill": [
        "lofi", "lo-fi", "chill", "chillout", "relax", "relaxing", "calm", "ambient",
        "sleep", "sleepy", "rainy", "coffee", "study", "soft", "slow", "acoustic",
        "night", "midnight", "dreamy", "dream", "peaceful", "serenity", "cozy",
    ],
    "hype": [
        "party", "dance", "edm", "remix", "bass", "bass boosted", "drop", "hype",
        "club", "trap", "phonk", "hardstyle", "hardcore", "banger", "drill",
        "rap", "hip hop", "hip-hop", "sped up", "energetic", "energy",
    ],
    "workout": [
        "workout", "gym", "pump", "motivation", "motivational", "training",
        "cardio", "beast", "grind", "never give up",
    ],
    "happy": [
        "happy", "joy", "joyful", "sunshine", "sunny", "fun", "upbeat", "feel good",
        "good vibes", "smile", "celebrate", "celebration", "bright", "cheerful",
    ],
    "focus": [
        "focus", "concentration", "instrumental", "piano", "classical", "meditation",
        "meditate", "brain", "productivity", "exam",
    ],
}

# Search fallbacks per mood, used when YouTube's mix is unavailable.
MOOD_QUERIES: dict[str, list[str]] = {
    "sad": ["sad songs", "sad emotional songs playlist", "heartbreak songs", "sad lofi songs"],
    "love": ["romantic love songs", "love songs playlist", "romantic music mix"],
    "chill": ["lofi chill beats", "chill relaxing music", "calm night playlist"],
    "hype": ["edm party mix", "trap hits mix", "dance party songs", "phonk mix"],
    "workout": ["workout motivation music", "gym hype music mix", "gym phonk"],
    "happy": ["feel good songs", "happy upbeat playlist", "good vibes music mix"],
    "focus": ["instrumental focus music", "study music playlist", "classical study music"],
}

MOOD_LABELS = {
    "sad": "sad",
    "love": "romantic",
    "chill": "chill",
    "hype": "hype",
    "workout": "workout",
    "happy": "happy",
    "focus": "focus",
}


def _pattern(word: str) -> re.Pattern:
    return re.compile(rf"(?<![a-z]){re.escape(word)}(?![a-z])")


def detect_mood(*parts: str | None) -> str | None:
    """Return the detected mood key for some text parts, or None."""
    text = " ".join(p for p in parts if p).lower()
    if not text:
        return None
    for mood, words in MOOD_KEYWORDS.items():
        for word in words:
            if _pattern(word).search(text):
                return mood
    return None


def random_query(mood: str | None, fallback: str = "popular music mix") -> str:
    """A search query for *mood* (randomly picked, so it feels fresh)."""
    if mood and mood in MOOD_QUERIES:
        return random.choice(MOOD_QUERIES[mood])
    return fallback
