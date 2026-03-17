"""OpenRouter AI service for nutrition recommendations."""

import logging
from typing import Optional

import aiohttp

from nutrition_bot.config import OPENROUTER_API_KEY, OPENROUTER_BASE_URL, OPENROUTER_MODEL

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """Ты — профессиональный диетолог-нутрициолог, специализирующийся на российском рынке продуктов.

Твои знания:
- Продукты, доступные в российских магазинах (Пятёрочка, Магнит, Перекрёсток, Лента, Ашан)
- Российские бренды и производители продуктов
- Цены на продукты в России (примерные)
- Традиционные и популярные блюда российской кухни
- Спортивное питание, доступное в России

Правила ответов:
- Отвечай ТОЛЬКО на русском языке
- Будь конкретным: указывай точные граммы, калории, БЖУ
- Учитывай сезонность продуктов в России
- Предлагай доступные по цене варианты
- Учитывай реальные размеры порций
- Если просят меню на день — распиши завтрак, обед, перекус, ужин с точными граммами
- Формат КБЖУ: Калории / Белки (г) / Жиры (г) / Углеводы (г)
"""


async def get_ai_response(
    user_message: str,
    user_context: Optional[str] = None,
    schedule_context: Optional[str] = None,
) -> str:
    """Get AI response from OpenRouter."""
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]

    if user_context:
        messages.append({
            "role": "system",
            "content": f"Данные пользователя:\n{user_context}",
        })

    if schedule_context:
        messages.append({
            "role": "system",
            "content": f"Расписание на сегодня:\n{schedule_context}",
        })

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
        "max_tokens": 2000,
        "temperature": 0.7,
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                OPENROUTER_BASE_URL,
                headers=headers,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=30),
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
