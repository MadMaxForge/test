"""OpenRouter AI service for nutrition recommendations."""

import json
import logging
from typing import Optional

import aiohttp

from nutrition_bot.config import OPENROUTER_API_KEY, OPENROUTER_BASE_URL, OPENROUTER_MODEL

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """Ты — профессиональный диетолог-нутрициолог и персональный AI-ассистент по питанию.
Специализируешься на российском рынке продуктов. Общаешься в свободном текстовом формате.

Твои знания:
- Продукты, доступные в российских магазинах (Пятёрочка, Магнит, Перекрёсток, Лента, Ашан)
- Российские бренды и производители продуктов
- Цены на продукты в России (примерные)
- Традиционные и популярные блюда российской кухни
- Спортивное питание, доступное в России
- Гликемический индекс (ГИ) продуктов — всегда указывай ГИ рядом с продуктами
- Пищевая ценность: КБЖУ, клетчатка, витамины, минералы
- Влияние ГИ на инсулин, энергию и жиросжигание

Правила ответов:
- Отвечай ТОЛЬКО на русском языке
- Общайся свободно текстом, как живой диетолог в чате
- Будь конкретным: указывай точные граммы, калории, БЖУ
- ВСЕГДА указывай гликемический индекс (ГИ) для продуктов: низкий (до 35), средний (36-69), высокий (70+)
- Учитывай сезонность продуктов в России
- Предлагай доступные по цене варианты
- Учитывай реальные размеры порций
- Если просят меню на день — распиши завтрак, обед, перекус, ужин с точными граммами
- Формат для каждого продукта: название, граммы, КБЖУ, ГИ
- При похудении рекомендуй продукты с низким ГИ
- При наборе массы — средний/высокий ГИ после тренировок, низкий в остальное время
- Учитывай расписание пользователя из Google Календаря, если оно предоставлено
- Если пользователь записывает еду (например "съел то-то") — проанализируй и верни оценку КБЖУ и ГИ
- Если пользователь хочет изменить профиль — помоги ему, спроси что изменить
- Будь проактивным: давай советы, предупреждай о проблемах в рационе

Ты умеешь:
1. Составлять меню на день/неделю с учётом целей, расписания и ГИ
2. Анализировать съеденную еду и считать КБЖУ + ГИ
3. Давать рекомендации по питанию перед/после тренировок
4. Предлагать замены продуктов (по аллергиям, предпочтениям, бюджету)
5. Объяснять влияние продуктов на здоровье и форму
6. Составлять списки покупок
7. Учитывать расписание дня при рекомендациях
"""


async def get_ai_response(
    user_message: str,
    user_context: Optional[str] = None,
    schedule_context: Optional[str] = None,
    history: Optional[list[dict]] = None,
    eaten_context: Optional[str] = None,
    calendar_context: Optional[str] = None,
) -> str:
    """Get AI response from OpenRouter with conversation history."""
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]

    if user_context:
        messages.append({
            "role": "system",
            "content": f"Данные пользователя:\n{user_context}",
        })

    if schedule_context:
        messages.append({
            "role": "system",
            "content": f"Расписание активностей:\n{schedule_context}",
        })

    if calendar_context:
        messages.append({
            "role": "system",
            "content": f"Google Календарь на сегодня:\n{calendar_context}",
        })

    if eaten_context:
        messages.append({
            "role": "system",
            "content": f"Съедено сегодня:\n{eaten_context}",
        })

    # Add conversation history (last N messages for context)
    if history:
        for msg in history[-10:]:
            messages.append(msg)

    messages.append({"role": "user", "content": user_message})

    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/MadMaxForge/test",
        "X-Title": "NutritionBot",
    }

    payload = {
        "model": OPENROUTER_MODEL,
        "messages": messages,
        "max_tokens": 3000,
        "temperature": 0.7,
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                OPENROUTER_BASE_URL,
                headers=headers,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=60),
            ) as resp:
                if resp.status != 200:
                    error_text = await resp.text()
                    logger.error("OpenRouter API error %d: %s", resp.status, error_text)
                    return "Извините, произошла ошибка при обращении к AI. Попробуйте позже."

                data = await resp.json()
                return data["choices"][0]["message"]["content"]
    except aiohttp.ClientError as e:
        logger.error("OpenRouter connection error: %s", e)
        return "Ошибка соединения с AI сервисом. Попробуйте позже."
    except (KeyError, IndexError) as e:
        logger.error("OpenRouter response parse error: %s", e)
        return "Ошибка обработки ответа AI. Попробуйте позже."


async def analyze_food_entry(description: str, user_context: Optional[str] = None) -> dict:
    """Ask AI to analyze a food entry and return nutrition data as dict."""
    prompt = (
        f"Пользователь съел: {description}\n\n"
        "Проанализируй и верни ТОЛЬКО JSON (без markdown, без ```json):\n"
        '{"calories": число, "protein": число, "fat": число, "carbs": число, '
        '"gi": число_или_null, "summary": "краткое описание"}\n\n'
        "Рассчитай КБЖУ максимально точно. gi — средний гликемический индекс блюда."
    )

    response = await get_ai_response(prompt, user_context)

    try:
        clean = response.strip()
        if clean.startswith("```"):
            lines = clean.split("\n")
            clean = "\n".join(lines[1:-1])
        return json.loads(clean)
    except (json.JSONDecodeError, ValueError):
        logger.warning("Failed to parse food analysis JSON: %s", response[:200])
        return {"calories": 0, "protein": 0, "fat": 0, "carbs": 0, "gi": None, "summary": description}


def build_user_context(user: dict, daily_target: dict) -> str:
    goal_names = {"lose": "Похудение", "maintain": "Поддержание формы", "gain": "Набор массы"}
    gender_names = {"male": "Мужской", "female": "Женский"}
    activity_names = {
        "sedentary": "Сидячий",
        "light": "Лёгкая активность",
        "moderate": "Умеренная активность",
        "active": "Высокая активность",
        "very_active": "Очень высокая активность",
    }

    return (
        f"Пол: {gender_names.get(user['gender'], user['gender'])}\n"
        f"Возраст: {user['age']} лет\n"
        f"Рост: {user['height_cm']} см\n"
        f"Вес: {user['weight_kg']} кг\n"
        f"Уровень активности: {activity_names.get(user['activity_level'], user['activity_level'])}\n"
        f"Цель: {goal_names.get(user['goal'], user['goal'])}\n"
        f"Базовый метаболизм (BMR): {daily_target['bmr']} ккал\n"
        f"Суточная норма (TDEE): {daily_target['tdee']} ккал\n"
        f"Целевые калории: {daily_target['target_calories']} ккал\n"
        f"Целевые БЖУ: Б {daily_target['protein_g']}г / "
        f"Ж {daily_target['fat_g']}г / У {daily_target['carbs_g']}г"
    )


def build_schedule_context(schedule: list[dict], schedule_calories: dict) -> str:
    if not schedule:
        return "Расписание на сегодня не задано."

    lines = ["Расписание на сегодня:"]
    for entry in schedule:
        lines.append(
            f"- {entry['time_start']}-{entry['time_end']}: "
            f"{entry['activity_name']} ({entry['activity_type']}, {entry['intensity']})"
        )

    lines.append(f"\nОжидаемый расход от активностей: {schedule_calories['total']} ккал")
    lines.append(f"  Спорт: {schedule_calories['sport']} ккал")
    lines.append(f"  Умственная работа: {schedule_calories['mental']} ккал")

    return "\n".join(lines)
