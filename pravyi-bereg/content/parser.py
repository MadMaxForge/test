"""Competitor parser: scrape VK groups for content ideas."""
from __future__ import annotations

import logging
import re
from datetime import datetime

import aiohttp

from config import VK_USER_TOKEN, OPENROUTER_API_KEY
from db import execute_insert, execute
from content.topics import add_parsed_topic

log = logging.getLogger(__name__)

VK_API_VERSION = "5.199"
VK_API_BASE = "https://api.vk.com/method"

# Competitor groups to parse
COMPETITOR_GROUPS = {
    "tierra_moscow": -193826980,
    "zagorodnyezemli": -44445527,
}

# Category detection keywords
CATEGORY_KEYWORDS = {
    "purchase": ["покуп", "купить", "подбор", "выбрать", "выбор", "подобр"],
    "sale": ["продаж", "продать", "оцен", "прода"],
    "land": ["участ", "земл", "межев", "кадастр", "ижс", "лпх", "снт"],
    "documents": ["документ", "оформл", "регистр", "договор", "право собств"],
    "legal": ["наслед", "налог", "приватиз", "материнск", "капитал", "доверен"],
    "advice": ["совет", "ошибк", "лайфхак", "секрет", "важно знать", "переезд"],
    "construction": ["строител", "фундамент", "дом под ключ", "проект дома"],
    "investment": ["инвестиц", "вложен", "доход", "окупаем", "рент"],
}


async def _vk_api(method: str, params: dict) -> dict | None:
    """Call VK API."""
    params["access_token"] = VK_USER_TOKEN
    params["v"] = VK_API_VERSION

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{VK_API_BASE}/{method}",
                data=params,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                data = await resp.json()
                if "error" in data:
                    log.error("VK API error in %s: %s", method, data["error"])
                    return None
                return data.get("response")
    except Exception as e:
        log.error("VK API call %s failed: %s", method, e)
        return None


def _detect_category(text: str) -> str:
    """Detect topic category from text content."""
    text_lower = text.lower()
    scores = {}
    for category, keywords in CATEGORY_KEYWORDS.items():
        score = sum(1 for kw in keywords if kw in text_lower)
        if score > 0:
            scores[category] = score

    if scores:
        return max(scores, key=scores.get)
    return "advice"


def _extract_title(text: str) -> str:
    """Extract a short title from post text."""
    # Take first line, clean up
    first_line = text.strip().split("\n")[0]
    # Remove emoji
    first_line = re.sub(r'[\U00010000-\U0010ffff]', '', first_line).strip()
    # Remove leading hashtags, symbols
    first_line = re.sub(r'^[#@\-•\d.]+\s*', '', first_line).strip()

    if len(first_line) > 80:
        first_line = first_line[:77] + "..."
    if len(first_line) < 10:
        return ""

    return first_line


async def parse_competitor_posts(group_name: str, count: int = 50) -> list[dict]:
    """Parse recent posts from a competitor VK group.

    Returns list of {'title': str, 'text': str, 'category': str, 'source': str}.
    """
    owner_id = COMPETITOR_GROUPS.get(group_name)
    if not owner_id:
        log.error("Unknown competitor group: %s", group_name)
        return []

    result = await _vk_api("wall.get", {
        "owner_id": owner_id,
        "count": min(count, 100),
        "filter": "owner",
    })

    if not result or "items" not in result:
        log.error("Failed to get posts from %s", group_name)
        return []

    posts = []
    for item in result["items"]:
        text = item.get("text", "")
        if not text or len(text) < 100:
            continue

        title = _extract_title(text)
        if not title:
            continue

        category = _detect_category(text)

        posts.append({
            "title": title,
            "text": text,
            "category": category,
            "source": group_name,
            "source_url": f"https://vk.com/wall{item['owner_id']}_{item['id']}",
        })

    log.info("Parsed %d posts from %s", len(posts), group_name)
    return posts


async def parse_and_save_topics(group_name: str, count: int = 50) -> int:
    """Parse competitor posts and save unique topics to bank.

    Returns number of new topics added.
    """
    posts = await parse_competitor_posts(group_name, count)
    added = 0

    for post in posts:
        # Save full article for reference
        try:
            execute_insert(
                "INSERT INTO parsed_articles (source, source_url, title, body, topic_category, parsed_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (post["source"], post["source_url"], post["title"],
                 post["text"][:5000], post["category"], datetime.now().isoformat()),
            )
        except Exception:
            pass

        # Add topic to bank
        add_parsed_topic(post["title"], post["category"], f"parsed_{post['source']}")
        added += 1

    log.info("Added %d topics from %s", added, group_name)
    return added


async def parse_all_competitors() -> dict:
    """Parse all configured competitor groups.

    Returns {'group_name': count_added, ...}.
    """
    results = {}
    for group_name in COMPETITOR_GROUPS:
        count = await parse_and_save_topics(group_name, count=50)
        results[group_name] = count
    return results


async def generate_topics_from_parsed(count: int = 10) -> list[str]:
    """Use AI to generate new unique topics inspired by parsed articles.

    Returns list of topic strings.
    """
    if not OPENROUTER_API_KEY:
        return []

    # Get some parsed articles for inspiration
    articles = execute(
        "SELECT title, topic_category FROM parsed_articles ORDER BY RANDOM() LIMIT 20"
    )

    if not articles:
        return []

    existing = execute("SELECT topic FROM topics_bank")
    existing_topics = {r["topic"] for r in existing}

    titles_text = "\n".join(f"- [{r['topic_category']}] {r['title']}" for r in articles)

    from content.generator import ask_ai

    prompt = f"""На основе этих тем-вдохновений из конкурентных групп VK:

{titles_text}

Придумай {count} НОВЫХ уникальных тем для постов о недвижимости и земельных участках в Нижегородской области.

Требования:
- Темы должны быть оригинальные, НЕ копировать конкурентов
- Практическая польза для читателя
- Адаптировать под региональную специфику (Городецкий, Сокольский, Чкаловский районы)
- Формат: одна тема на строку, без нумерации

Ответ — только список тем, ничего больше."""

    result = await ask_ai(prompt)
    if not result:
        return []

    new_topics = []
    for line in result.strip().split("\n"):
        line = line.strip().lstrip("-•").strip()
        if line and len(line) > 10 and line not in existing_topics:
            category = _detect_category(line)
            add_parsed_topic(line, category, "ai_generated")
            new_topics.append(line)

    log.info("Generated %d new topics from parsed content", len(new_topics))
    return new_topics
