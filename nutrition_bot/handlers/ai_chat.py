"""AI chat handler for free-form nutrition questions."""

import logging
from datetime import date

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message

from nutrition_bot.database.models import (
    get_user, get_schedule, get_meals_for_date,
)
from nutrition_bot.services.calculator import (
    calculate_daily_target, calculate_schedule_calories,
    get_today_day_of_week,
)
from nutrition_bot.services.ai_service import (
    get_ai_response, build_user_context, build_schedule_context,
)

logger = logging.getLogger(__name__)

router = Router()


class AIChatState(StatesGroup):
    chatting = State()


@router.message(F.text == "🤖 Спросить AI")
async def start_ai_chat(message: Message, state: FSMContext) -> None:
    user = get_user(message.from_user.id)
    if not user:
        await message.answer("Сначала создайте профиль: /start")
        return

    await message.answer(
        "🤖 <b>AI-диетолог</b>\n\n"
        "Задайте любой вопрос о питании. Например:\n"
        "• Что поесть перед тренировкой?\n"
        "• Чем заменить курицу?\n"
        "• Какие продукты богаты белком?\n"
        "• Составь список покупок на неделю\n"
        "• Рецепт протеинового завтрака\n\n"
        "Для выхода нажмите любую кнопку меню.",
        parse_mode="HTML",
    )
    await state.set_state(AIChatState.chatting)


@router.message(AIChatState.chatting)
async def process_ai_question(message: Message, state: FSMContext) -> None:
    menu_buttons = {
        "📊 Мой профиль", "🎯 Мои цели", "📅 Расписание",
        "🍽 Меню на день", "📈 Статистика дня", "🤖 Спросить AI",
        "⚙️ Настройки",
    }
    if message.text in menu_buttons:
        await state.clear()
        return

    user = get_user(message.from_user.id)
    if not user:
        await message.answer("Сначала создайте профиль: /start")
        await state.clear()
        return

    await message.answer("⏳ Думаю...")

    daily_target = calculate_daily_target(
        user["gender"], user["weight_kg"], user["height_cm"],
        user["age"], user["activity_level"], user["goal"],
    )

    day = get_today_day_of_week()
    schedule = get_schedule(message.from_user.id, day)
    schedule_cal = calculate_schedule_calories(schedule)
    today = date.today().isoformat()
    meals_today = get_meals_for_date(message.from_user.id, today)

    eaten_info = ""
    if meals_today:
        total_cal = sum(m["calories"] for m in meals_today)
        total_p = sum(m["protein"] for m in meals_today)
        total_f = sum(m["fat"] for m in meals_today)
        total_c = sum(m["carbs"] for m in meals_today)
        eaten_info = (
            f"\n\nСегодня уже съедено: {round(total_cal)} ккал "
            f"(Б {round(total_p)}г / Ж {round(total_f)}г / У {round(total_c)}г)"
        )

    user_ctx = build_user_context(user, daily_target)
    schedule_ctx = build_schedule_context(schedule, schedule_cal)

    full_question = message.text + eaten_info

    response = await get_ai_response(full_question, user_ctx, schedule_ctx)

    if len(response) > 4000:
        for i in range(0, len(response), 4000):
            await message.answer(response[i:i + 4000])
    else:
        await message.answer(response)
