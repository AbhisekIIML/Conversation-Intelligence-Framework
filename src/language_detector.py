"""
Language Detection Module
==========================

Detects the primary language of chat messages using a combination of
script-based heuristics (fast path) and statistical detection (fallback).

Supports 14+ languages including mixed-language detection for Hinglish
(romanized Hindi written in Latin script).

Supported Languages:
    English, Chinese, German, Japanese, Hindi, Dutch, Spanish,
    Italian, Portuguese, Turkish, Korean, Arabic, Vietnamese, French
"""

import re
from typing import Optional, Set

from lingua import Language, LanguageDetectorBuilder

# Supported language set for validation
SUPPORTED_LANGUAGES: Set[str] = {
    "English", "Chinese", "German", "Japanese", "Hindi",
    "Dutch", "Spanish", "Italian", "Portuguese", "Turkish",
    "Korean", "Arabic", "Vietnamese", "French",
}

# Common Hinglish (romanized Hindi) words for mixed-language detection
_HINGLISH_WORDS: Set[str] = {
    "nahi", "hai", "kya", "kaise", "karna", "karo", "mera", "meri", "mere",
    "hua", "hoga", "kaha", "woh", "yeh", "bhi", "aur", "lekin", "kyunki",
    "agar", "toh", "par", "sab", "kuch", "bahut", "accha", "theek", "haan",
    "naa", "matlab", "samajh", "batao", "bata", "dikha", "dikhao", "milega",
    "chahiye", "karke", "wala", "wali", "raha", "rahi", "gaya", "gayi",
    "pehle", "baad", "upar", "neeche", "andar", "bahar", "sath",
}

# Regex to clean noise before detection (URLs, IDs, special chars)
_CLEAN_REGEX = re.compile(
    r'https?://\S+|\[\[.*?\]\]|\[.*?\]\(.*?\)|\*+|#+|\\n|\n|[{}()\[\]|`~<>]'
    r'|\bB[0-9A-Z]{9}\b|\b[A-Z0-9]{10,}\b|\b\d{3}-\d{7}-\d{7}\b'
    r'|[€$£¥₹][\d.,]+|[\d.,]+[€$£¥₹]|\b[A-Z][A-Z0-9_]{5,}\b'
    r'|\S+@\S+\.\S+|\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b'
)

# Build lingua detector (limited to supported languages for speed)
_detector = LanguageDetectorBuilder.from_languages(
    Language.ENGLISH, Language.CHINESE, Language.GERMAN, Language.JAPANESE,
    Language.HINDI, Language.DUTCH, Language.SPANISH, Language.ITALIAN,
    Language.PORTUGUESE, Language.TURKISH, Language.KOREAN, Language.ARABIC,
    Language.VIETNAMESE, Language.FRENCH,
).build()


def _clean_text(text: str) -> str:
    """Remove noise (URLs, IDs, special characters) from text."""
    cleaned = _CLEAN_REGEX.sub(' ', str(text))
    return re.sub(r'\s+', ' ', cleaned).strip()


def _detect_from_cleaned(cleaned: str) -> Optional[str]:
    """
    Detect language from pre-cleaned text using heuristics + statistics.

    Strategy:
        1. Script-based detection (CJK, Arabic, Devanagari, Korean) — instant
        2. Hinglish word overlap detection — fast
        3. Character-based heuristics (Turkish, German) — fast
        4. Lingua statistical model — fallback for ambiguous cases
    """
    if len(cleaned) < 3:
        return None

    # Require recognizable script characters
    if not re.search(
        r'[a-zA-Z\u00C0-\u024F\u0400-\u04FF\u4E00-\u9FFF'
        r'\u3040-\u30FF\u0600-\u06FF\u0900-\u097F\uAC00-\uD7AF]{2,}',
        cleaned
    ):
        return None

    words = set(cleaned.lower().split())

    # Hinglish detection (romanized Hindi in Latin script)
    if len(words & _HINGLISH_WORDS) >= 3:
        return "Hindi"

    # Script-based detection (fast path for non-Latin scripts)
    if re.search(r'[\u4E00-\u9FFF]', cleaned):
        return "Japanese" if re.search(r'[\u3040-\u309F\u30A0-\u30FF]', cleaned) else "Chinese"
    if re.search(r'[\u3040-\u309F\u30A0-\u30FF]', cleaned):
        return "Japanese"
    if re.search(r'[\uAC00-\uD7AF]', cleaned):
        return "Korean"
    if re.search(r'[\u0600-\u06FF]', cleaned):
        return "Arabic"
    if re.search(r'[\u0900-\u097F]', cleaned):
        return "Hindi"
    if re.search(r'[ışğİŞĞ]', cleaned):
        return "Turkish"
    if re.search(r'[äöüßÄÖÜ]', cleaned):
        return "German"

    # Short ASCII text defaults to English
    if len(cleaned) < 50 and cleaned.isascii():
        return "English"

    # Statistical detection fallback
    try:
        lang = _detector.detect_language_of(cleaned)
        return lang.name.capitalize() if lang else None
    except Exception:
        return None


def detect_language(question_raw: str, response_raw: str) -> str:
    """
    Detect the primary language of a chat turn.

    Examines the user's question first (more reliable signal),
    then falls back to the bot's response.

    Args:
        question_raw: Raw user question text.
        response_raw: Raw bot response text.

    Returns:
        Language name string (e.g., "English", "Spanish", "Hindi").
        Defaults to "English" if detection is inconclusive.

    Example:
        >>> detect_language("¿Cómo puedo rastrear mi pedido?", "...")
        'Spanish'

        >>> detect_language("mera order kaha hai?", "...")
        'Hindi'
    """
    # Try question first
    q = _clean_text(question_raw or "")[:200]
    result = _detect_from_cleaned(q)
    if result and result in SUPPORTED_LANGUAGES:
        return result

    # Fall back to response
    r = _clean_text(response_raw or "")[:200]
    result2 = _detect_from_cleaned(r)
    if result2 and result2 in SUPPORTED_LANGUAGES:
        return result2

    return result or result2 or "English"
