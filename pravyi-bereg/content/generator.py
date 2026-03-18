"""Content generation via OpenRouter API."""
from __future__ import annotations

import hashlib
import logging
from typing import Any

import aiohttp

from config import (
    OPENROUTER_API_KEY, OPENROUTER_MODEL, OPENROUTER_BASE_URL,
    BRAND_NAME, BRAND_REGION, BRAND_EXPERIENCE,
    MAX_POST_LENGTH, MIN_POST_LENGTH, HASHTAGS_COUNT,
)

log = logging.getLogger(__name__)

SYSTEM_PROMPT = f"""Ты — копирайтер для VK-сообщества "{BRAND_NAME}".
Направление: недвижимость, земельные участки, загородные дома.
Регион: {BRAND_REGION}.
Опыт компании: {BRAND_EXPERIENCE}.

Правила:
- Пиши простым, понятным языком для обычных людей
- Без конкретных цен (можно "рассчитаем индивидуально")
- Без выдуманных фактов и цифр
- Используй эмодзи умеренно и по делу (2-4 на пост)
- Учитывай местную специфику (Нижегородская область)
- Стиль: экспертный, но дружелюбный
"""


async def ask_ai(prompt: str, system_prompt: str = SYSTEM_PROMPT, max_tokens: int = 2000) -> str | None:
    """Call OpenRouter API and return text response."""
    if not OPENROUTER_API_KEY:
        log.error("OPENROUTER_API_KEY not configured")
        return None

    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://vk.com/club236779093",
    }

    payload = {
        "model": OPENROUTER_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ],
        "max_tokens": max_tokens,
        "temperature": 0.8,
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{OPENROUTER_BASE_URL}/chat/completions",
                headers=headers,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=60),
            ) as resp:
                if resp.status != 200:
                    error_text = await resp.text()
                    log.error("OpenRouter API error %d: %s", resp.status, error_text[:200])
                    return None
                data = await resp.json()
                return data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        log.error("OpenRouter API call failed: %s", e)
        return None


async def generate_post(topic: str, context: str = "") -> dict[str, Any]:
    """Generate a VK post on the given topic.
    
    Returns {'text': str, 'hook': str, 'topic': str, 'content_hash': str} or {'error': str}.
    """
    prompt = f"""Напиши пост для VK-группы "{BRAND_NAME}".

Тема: {topic}
{f'Контекст/вдохновение: {context}' if context else ''}

Структура поста:
1. Первая строка — цепляющий заголовок (эмодзи + короткая фраза)
2. Пустая строка
3. Основной текст — полезная информация по теме (3-5 абзацев)
4. Пустая строка
5. Мягкий призыв к действию (написать в сообщения / задать вопрос)
6. Пустая строка
7. {HASHTAGS_COUNT} хештегов через пробел

Требования:
- Длина: {MIN_POST_LENGTH}-{MAX_POST_LENGTH} символов
- Пост должен быть полезным и конкретным
- Используй эмодзи умеренно (2-4 штуки, в заголовке и ключевых местах)
- Без конкретных цен
- Закончи мягким CTA

Формат ответа — только текст поста, ничего больше."""

    text = await ask_ai(prompt)
    if not text:
        return {"error": "AI generation failed"}

    # Validate length
    if len(text) < 100:
        return {"error": f"Post too short: {len(text)} chars"}

    # Truncate if too long
    if len(text) > MAX_POST_LENGTH:
        # Try to cut at last paragraph before limit
        cut_point = text[:MAX_POST_LENGTH].rfind("\n\n")
        if cut_point > MIN_POST_LENGTH:
            text = text[:cut_point]

    content_hash = hashlib.md5(text.strip().lower().encode()).hexdigest()
    hook = text.split("\n")[0].strip()[:100]

    return {
        "text": text,
        "hook": hook,
        "topic": topic,
        "content_hash": content_hash,
    }


async def generate_reel_script(topic: str) -> dict[str, Any]:
    """Generate a short script for a video reel (voiceover text).
    
    Returns {'voiceover_text': str, 'scenes': list, 'topic': str} or {'error': str}.
    """
    prompt = f"""Напиши короткий сценарий для видео-рилса (30-45 секунд) для VK-группы "{BRAND_NAME}".

Тема: {topic}

Структура:
1. Хук (первые 3 секунды — цепляющая фраза)
2. Основная часть (3-4 коротких тезиса по теме)
3. Заключение с призывом (подписаться / написать)

Требования:
- Текст для ОЗВУЧКИ — пиши как разговорную речь
- Длина: 80-150 слов (30-45 секунд при озвучке)
- Короткие предложения (лёгко воспринимать на слух)
- Без сложных терминов
- Конкретика без выдуманных цифр

Формат ответа:
ОЗВУЧКА:
(текст для озвучки, сплошной текст)

СЦЕНЫ:
1. (описание что показать, 5-8 сек)
2. (описание что показать, 5-8 сек)
3. (описание что показать, 5-8 сек)
4. (описание что показать, 5-8 сек)
"""

    text = await ask_ai(prompt, max_tokens=1000)
    if not text:
        return {"error": "AI generation failed"}

    # Parse voiceover and scenes
    voiceover = ""
    scenes = []
    
    parts = text.split("СЦЕНЫ:")
    if len(parts) == 2:
        voiceover_part = parts[0].replace("ОЗВУЧКА:", "").strip()
        voiceover = voiceover_part.strip()
        
        scenes_text = parts[1].strip()
        for line in scenes_text.split("\n"):
            line = line.strip()
            if line and line[0].isdigit():
                # Remove leading number and dot
                scene_desc = line.lstrip("0123456789.").strip()
                if scene_desc:
                    scenes.append({"description": scene_desc, "duration": 7})
    else:
        # Fallback: treat entire text as voiceover
        voiceover = text.replace("ОЗВУЧКА:", "").strip()

    if not voiceover:
        return {"error": "Could not parse voiceover text"}

    return {
        "voiceover_text": voiceover,
        "scenes": scenes if scenes else [{"description": "default", "duration": 7}] * 4,
        "topic": topic,
    }


async def generate_post_title(topic: str) -> str:
    """Generate a short title for photo overlay (max 6 words)."""
    prompt = f"""Напиши ОЧЕНЬ короткий заголовок для картинки-обложки поста.

Тема: {topic}

Требования:
- Максимум 5-6 слов
- Без эмодзи
- Цепляющий, конкретный
- Как заголовок газетной статьи

Примеры хороших заголовков:
- Как выбрать участок мечты
- 5 ошибок при покупке дома
- Земля у реки: выгодная инвестиция
- Что проверить перед сделкой

Ответ — ТОЛЬКО заголовок, ничего больше."""

    title = await ask_ai(prompt, max_tokens=50)
    if not title:
        return topic[:40]
    
    # Clean up
    title = title.strip().strip('"').strip("'").strip("«»")
    if len(title) > 50:
        title = title[:50]
    
    return title
