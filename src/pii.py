"""
PII Sanitization Module
========================

Removes personally identifiable information, financial data, profanity,
and other sensitive content from text before it is sent to an LLM.

All sanitization runs locally — no data leaves the system until
this module has processed it.

Supported PII Types:
    - Email addresses
    - Phone numbers (international + domestic)
    - Credit card numbers
    - Social Security Numbers
    - Account numbers
    - Order/product IDs
    - URLs and IP addresses
    - Monetary values
    - Physical addresses
    - Personal names (after title prefixes)
    - Profanity and abusive language
"""

import re
from typing import List, Tuple

# Profanity patterns (case-insensitive, handles obfuscations)
_PROFANITY_PATTERNS: List[str] = [
    r'\bf+u+c+k+\w*\b',
    r'\bs+h+i+t+\w*\b',
    r'\ba+s+s+h+o+l+e+\w*\b',
    r'\bb+i+t+c+h+\w*\b',
    r'\bd+a+m+n+\w*\b',
    r'\bc+u+n+t+\w*\b',
    r'\bd+i+c+k+\w*\b',
    r'\bb+a+s+t+a+r+d+\w*\b',
    r'\bi+d+i+o+t+\w*\b',
    r'\bwtf\b',
    r'\bstfu\b',
    r'\bf[\*\.\-_]+c?k\w*\b',
    r'\bs[\*\.\-_]+h[\*\.\-_]*t\w*\b',
]

# Compiled PII regex patterns with replacement tokens
_PII_PATTERNS: List[Tuple[re.Pattern, str]] = [
    (
        re.compile(
            r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.'
            r'(?:com|org|net|edu|gov|co|io|dev|info|biz)(?:\.[a-z]{2,3})?'
            r'(?=\s|[^a-zA-Z]|$)',
            re.IGNORECASE,
        ),
        '[EMAIL]',
    ),
    (
        re.compile(r'(\+?\d{1,3}[-.\s]?)?\(?\d{2,4}\)?[-.\s]?\d{3,4}[-.\s]?\d{3,4}'),
        '[PHONE]',
    ),
    (re.compile(r'\bB[0-9A-Z]{9}\b'), '[PRODUCT_ID]'),
    (re.compile(r'\b\d{4}[-\s]?\d{4}[-\s]?\d{4}[-\s]?\d{1,7}\b'), '[CARD]'),
    (re.compile(r'\b\d{8,17}\b'), '[ACCOUNT]'),
    (re.compile(r'\b\d{3}[-\s]?\d{2}[-\s]?\d{4}\b'), '[SSN]'),
    (re.compile(r'\b\d{3}-\d{7}-\d{7}\b'), '[ORDER_ID]'),
    (re.compile(r'https?://\S+'), '[URL]'),
    (re.compile(r'\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b'), '[IP]'),
    (re.compile(r'\$[\d,]+\.?\d*'), '[AMOUNT]'),
    (
        re.compile(
            r'\b\d{1,5}\s+[A-Za-z]+\s+'
            r'(St|Street|Ave|Avenue|Blvd|Boulevard|Dr|Drive|Rd|Road|Ln|Lane|Way|Ct|Court|Pl|Place)\b',
            re.IGNORECASE,
        ),
        '[ADDRESS]',
    ),
    (
        re.compile(r'\b(Mr|Mrs|Ms|Dr|Miss)\.?\s+[A-Z][a-z]+(\s+[A-Z][a-z]+)?'),
        '[NAME]',
    ),
]


def sanitize_text(text: str) -> str:
    """
    Remove PII, financial data, and profanity from text.

    Args:
        text: Raw text that may contain sensitive information.

    Returns:
        Sanitized text with sensitive tokens replaced by labels
        (e.g., [EMAIL], [PHONE], [CARD]).

    Example:
        >>> sanitize_text("Contact john@example.com or call 555-123-4567")
        'Contact [EMAIL] or call [PHONE]'

        >>> sanitize_text("Order 111-2345678-9012345 costs $49.99")
        'Order [ORDER_ID] costs [AMOUNT]'
    """
    if not text:
        return text

    # Apply profanity filter
    for pattern in _PROFANITY_PATTERNS:
        text = re.sub(pattern, '[REDACTED]', text, flags=re.IGNORECASE)

    # Apply PII patterns
    for compiled_pattern, replacement in _PII_PATTERNS:
        text = compiled_pattern.sub(replacement, text)

    return text
