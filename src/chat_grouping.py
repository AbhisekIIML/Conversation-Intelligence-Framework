"""
Chat Grouping Module
=====================

Groups raw turn-level rows into complete chats with metadata.
Each output row represents one full chat with:
    - Concatenated turn text
    - Helpfulness assessment
    - Detected language
    - Domain-specific acronyms found

This module sits between raw data ingestion and LLM summarization.
"""

import re
from typing import List, Set

import pandas as pd

from .language_detector import detect_language

# Strings indicating the bot could not help (customize for your domain)
UNHELPFUL_STRINGS: List[str] = [
    "I don't have enough information",
    "I don't have access",
    "I don't have specific information",
    "I'm not able to provide specific information",
    "I'm unable to retrieve information",
    "Invalid Input",
    "cannot find any specific information",
    "couldn't find specific information",
    "having difficulty accessing",
    "wasn't able to find",
    "I'm unable to access your specific",
    "I don't have direct access",
    "I cannot directly retrieve",
    "Our system detected a potentially unhelpful response",
    "An error has occurred while generating a response",
    "Sorry, I'm not fluent in all languages yet",
    "Something went wrong on my end, and I'm not sure what caused it",
]

# Fast word-boundary tokenizer for acronym matching
_WORD_SPLIT = re.compile(r'[^A-Za-z0-9]+')


def load_acronyms(csv_path: str) -> Set[str]:
    """
    Load acronym mapping from a CSV file on S3.

    The CSV should contain a column named 'acronym_shrt' with
    domain-specific acronyms to detect (case-sensitive, whole-word).

    Args:
        csv_path: S3 path to the acronym CSV file.

    Returns:
        Set of acronym strings for fast lookup.

    Example:
        >>> acronyms = load_acronyms("s3://bucket/config/acronyms.csv")
        >>> len(acronyms)
        342
    """
    try:
        df = pd.read_csv(csv_path, storage_options={"anon": False}, encoding="latin-1")
        acronyms = set(df["acronym_shrt"].dropna().unique())
        print(f"Loaded {len(acronyms)} acronyms from {csv_path}")
        return acronyms
    except Exception as e:
        print(f"Warning: Could not load acronym mapping: {e}")
        return set()


def find_acronyms(text: str, acronym_set: Set[str]) -> List[str]:
    """
    Find domain-specific acronyms using fast set intersection.

    Uses word-boundary splitting for O(1) per-word lookup rather
    than iterating through all known acronyms.

    Args:
        text: Input text to scan.
        acronym_set: Known acronyms to match against.

    Returns:
        Sorted list of matched acronyms.
    """
    if not text or not acronym_set:
        return []
    words = set(_WORD_SPLIT.split(text))
    found = words & acronym_set
    return sorted(found) if found else []


def group_turns_into_chats(df: pd.DataFrame, acronym_set: Set[str]) -> pd.DataFrame:
    """
    Group raw turn-level rows into one row per chat.

    Processing steps per chat:
        1. Sort turns chronologically by turn_rank
        2. Concatenate into formatted text (User/Bot pairs)
        3. Check helpfulness (any unhelpful bot responses → N)
        4. Detect language from user messages
        5. Extract domain-specific acronyms

    Args:
        df: DataFrame with columns: chat_id, turn_rank, question, response,
            chat_date, question_raw, response_raw.
        acronym_set: Known acronyms for detection.

    Returns:
        DataFrame with one row per chat:
            - chat_id: Unique identifier
            - chat_text: Full formatted chat
            - total_turns: Number of turns
            - helpfulness: "Y" or "N"
            - chat_date: Date of chat
            - acronym_count: Number of acronyms found
            - acronyms_found: Comma-separated list
            - user_language: Detected language

    Example:
        >>> grouped = group_turns_into_chats(raw_df, acronym_set)
        >>> grouped.columns.tolist()
        ['chat_id', 'chat_text', 'total_turns', 'helpfulness', ...]
    """
    df = df.sort_values(["chat_id", "turn_rank"])

    # Build formatted turn text
    df["qa_pair"] = (
        "Turn " + df["turn_rank"].astype(str) + ":\n"
        "User: " + df["question"].fillna("") +
        "\nBot: " + df["response"].fillna("")
    )

    # Check for unhelpful responses
    response_lower = (
        df["response"].fillna("")
        .str.replace("\u2018", "'").str.replace("\u2019", "'")
        .str.replace("\u201C", '"').str.replace("\u201D", '"')
        .str.lower()
    )
    df["is_unhelpful"] = response_lower.apply(
        lambda r: any(s.lower() in r for s in UNHELPFUL_STRINGS)
    )

    # Detect acronyms
    combined_text = df["question"].fillna("") + " " + df["response"].fillna("")
    df["row_acronyms"] = combined_text.apply(lambda t: find_acronyms(t, acronym_set))

    # Detect language per row
    df["detected_lang"] = df.apply(
        lambda r: detect_language(r["question_raw"], r["response_raw"]), axis=1
    )

    # Aggregate by chat
    grouped = df.groupby("chat_id").agg(
        chat_text=("qa_pair", lambda x: "\n\n".join(x)),
        total_turns=("turn_rank", "count"),
        helpfulness=("is_unhelpful", lambda x: "N" if x.any() else "Y"),
        chat_date=("chat_date", "first"),
        _all_acronyms=("row_acronyms", lambda x: sorted(set(a for lst in x for a in lst))),
        user_language=("detected_lang", lambda x: x.mode().iloc[0] if len(x.mode()) > 0 else "English"),
    ).reset_index()

    grouped["acronym_count"] = grouped["_all_acronyms"].apply(len)
    grouped["acronyms_found"] = grouped["_all_acronyms"].apply(lambda x: ", ".join(x) if x else "")
    grouped.drop(columns=["_all_acronyms"], inplace=True)

    return grouped
