"""Content validation rules."""
from __future__ import annotations

import re
import logging

log = logging.getLogger(__name__)

# Banned patterns (things that should NOT appear in posts)
BANNED_PATTERNS = [
    r"\d+\s*руб",           # Concrete prices
    r"\d+\s*\$",            # Dollar prices
    r"\d+\s*€",             # Euro prices
    r"от\s+\d+\s*тыс",     # "от 500 тыс"
    r"скидк[аи]\s+\d+%",   # "скидка 30%"
    r"гарантир",            # "гарантируем"
    r"100%",                # "100% результат"
    r"лучш[аеий]+\s+на\s+рынке",  # "лучшие на рынке"
    r"только\s+у\s+нас",   # "только у нас"
]

# Required elements
REQUIRED_ELEMENTS = [
    "hashtag",  # Must have hashtags
]


def validate_post(text: str) -> dict:
    """Validate post text. Returns {'passed': bool, 'issues': list}."""
    issues = []
    
    # Check banned patterns
    for pattern in BANNED_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            issues.append(f"Banned pattern found: {pattern}")
    
    # Check length
    if len(text) < 100:
        issues.append(f"Too short: {len(text)} chars (min 100)")
    if len(text) > 3000:
        issues.append(f"Too long: {len(text)} chars (max 3000)")
    
    # Check hashtags
    hashtags = re.findall(r"#\w+", text)
    if len(hashtags) < 2:
        issues.append("Too few hashtags (need at least 2)")
    if len(hashtags) > 10:
        issues.append("Too many hashtags (max 10)")
    
    # Check for CTA (call to action)
    cta_patterns = [
        r"напиш[иу]те",
        r"обращайтесь",
        r"звоните",
        r"подпис[ыа]",
        r"задайте\s+вопрос",
        r"консультаци",
        r"сообщени[яе]",
        r"связ[яь]",
        r"помо[жг]",
    ]
    has_cta = any(re.search(p, text, re.IGNORECASE) for p in cta_patterns)
    if not has_cta:
        issues.append("No CTA (call to action) found")
    
    passed = len(issues) == 0
    if not passed:
        log.warning("Post validation failed: %s", issues)
    
    return {"passed": passed, "issues": issues, "length": len(text)}
