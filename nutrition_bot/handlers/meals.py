"""Meal logging and menu handlers."""

import logging
from datetime import date

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery

from nutrition_bot.database.models import (
    get_user, get_schedule, get_meals_for_date, add_meal,
)
from nutrition_bot.services.calculator import (
    calculate_daily_target, calculate_schedule_calories,
    get_today_day_of_week,
)
from nutrition_bot.services.ai_service import (
    get_ai_response, build_user_context, build_schedule_context,
)
from nutrition_bot.utils.keyboards import meal_type_keyboard
from nutrition_bot.utils.formatters import format_day_stats

logger = logging.getLogger(__name__)

router = Router()


class LogMeal(StatesGroup):
    meal_type = State()
    description = State()


@router.message(F.text == "🍽 Меню на день")
async def menu_for_day(message: Message) -> None:
    user = get_user(message.from_user.id)
    if not user:
        await message.answer("Сначала создайте профиль: /start")
        return

    await message.answer("⏳ Составляю меню на день с учётом вашего профиля и расписания...")

    daily_target = calculate_daily_target(
        user["gender"], user["weight_kg"], user["height_cm"],
        user["age"], user["activity_level"], user["goal"],
    )

    day = get_today_day_of_week()
    schedule = get_schedule(message.from_user.id, day)
    schedule_cal = calculate_schedule_calories(schedule)
    today = date.today().isoformat()
    meals_today = get_meals_for_date(message.from_user.id, today)

    eaten_cal = sum(m["calories"] for m in meals_today)
    eaten_protein = sum(m["protein"] for m in meals_today)
    eaten_fat = sum(m["fat"] for m in meals_today)
    eaten_carbs = sum(m["carbs"] for m in meals_today)

    adjusted_calories = daily_target["target_calories"] + schedule_cal["total"]

    user_ctx = build_user_context(user, daily_target)
    schedule_ctx = build_schedule_context(schedule, schedule_cal)

    eaten_info = ""
    if meals_today:
        eaten_info = (
            f"\n\nУже съедено сегодня: {round(eaten_cal)} ккал "
            f"(Б {round(eaten_protein)}г / Ж {round(eaten_fat)}г / У {round(eaten_carbs)}г)"
        )

    prompt = (
        f"Составь подробное меню на день. "
        f"Целевые калории с учётом активностей: {adjusted_calories} ккал. "
        f"Целевые БЖУ: Б {daily_target['protein_g']}г / Ж {daily_target['fat_g']}г / У {daily_target['carbs_g']}г. "
        f"Дополнительный расход от активностей: {schedule_cal['total']} ккал."
        f"{eaten_info}\n\n"
        "Требования:\n"
        "1. Учти продукты российского рынка (магазины: Пятёрочка, Магнит, Перекрёсток)\n"
        "2. Распиши завтрак, обед, перекус, ужин\n"
        "3. Для каждого блюда укажи точные граммы и КБЖУ\n"
        "4. Укажи итого за день\n"
        "5. Если есть спортивные тренировки — предложи перекус до/после тренировки\n"
        "6. Блюда должны быть простыми в приготовлении"
    )

    response = await get_ai_response(prompt, user_ctx, schedule_ctx)
    await message.answer(response, parse_mode="HTML")


@router.message(F.text == "📈 Статистика дня")
async def day_stats(message: Message) -> None:
    user = get_user(message.from_user.id)
    if not user:
        await message.answer("Сначала создайте профиль: /start")
        return

    daily_target = calculate_daily_target(
        user["gender"], user["weight_kg"], user["height_cm"],
        user["age"], user["activity_level"], user["goal"],
    )

    day = get_today_day_of_week()
    schedule = get_schedule(message.from_user.id, day)
    schedule_cal = calculate_schedule_calories(schedule)
    today = date.today().isoformat()
    meals_today = get_meals_for_date(message.from_user.id, today)

    text = format_day_stats(daily_target, meals_today, schedule_cal)
    await message.answer(text, parse_mode="HTML")


@router.message(F.text == "🎯 Мои цели")
async def my_goals(message: Message) -> None:
    user = get_user(message.from_user.id)
    if not user:
        await message.answer("Сначала создайте профиль: /start")
        return

    daily_target = calculate_daily_target(
        user["gender"], user["weight_kg"], user["height_cm"],
        user["age"], user["activity_level"], user["goal"],
    )

    goal_names = {"lose": "🔥 Похудение", "maintain": "⚖️ Поддержание формы", "gain": "🏋️ Набор массы"}
    goal_descriptions = {
        "lose": "Дефицит ~400 ккал от нормы. Повышенное содержание белка для сохранения мышц.",
        "maintain": "Сбалансированное питание для поддержания текущей формы и веса.",
        "gain": "Профицит ~400 ккал от нормы. Высокое содержание белка и углеводов для роста мышц.",
    }

    text = (
        f"🎯 <b>Ваша цель: {goal_names.get(user['goal'], user['goal'])}</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"{goal_descriptions.get(user['goal'], '')}\n\n"
        f"📊 <b>Дневные нормы:</b>\n"
        f"🔥 Калории: <b>{daily_target['target_calories']} ккал</b>\n"
        f"🥩 Белки: {daily_target['protein_g']}г ({daily_target['protein_pct']}%)\n"
        f"🧈 Жиры: {daily_target['fat_g']}г ({daily_target['fat_pct']}%)\n"
        f"🍞 Углеводы: {daily_target['carbs_g']}г ({daily_target['carbs_pct']}%)\n\n"
        f"💡 <i>Для изменения цели используйте ⚙️ Настройки</i>"
    )
    await message.answer(text, parse_mode="HTML")


@router.message(F.text == "📊 Мой профиль")
async def my_profile(message: Message) -> None:
    user = get_user(message.from_user.id)
    if not user:
        await message.answer("Профиль не найден. Создайте его: /start")
        return

    daily_target = calculate_daily_target(
        user["gender"], user["weight_kg"], user["height_cm"],
        user["age"], user["activity_level"], user["goal"],
    )

    from nutrition_bot.utils.formatters import format_profile
    text = format_profile(user, daily_target)
    await message.answer(text, parse_mode="HTML")


@router.callback_query(F.data.startswith("meal_"))
async def meal_type_selected(callback: CallbackQuery, state: FSMContext) -> None:
    meal_type = callback.data.replace("meal_", "")
    await state.update_data(meal_type=meal_type)

    type_names = {
        "breakfast": "завтрак", "lunch": "обед",
        "snack": "перекус", "dinner": "ужин",
    }

    await callback.message.edit_text(
        f"Опишите что вы съели на {type_names.get(meal_type, meal_type)}:\n\n"
        "Например: Куриная грудка 200г, рис 150г, салат из огурцов и помидоров 100г"
    )
    await state.set_state(LogMeal.description)
    await callback.answer()


@router.message(LogMeal.description)
async def process_meal_description(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    meal_type = data["meal_type"]
    description = message.text.strip()

    await message.answer("⏳ Анализирую приём пищи...")

    user = get_user(message.from_user.id)
    daily_target = calculate_daily_target(
        user["gender"], user["weight_kg"], user["height_cm"],
        user["age"], user["activity_level"], user["goal"],
    )
    user_ctx = build_user_context(user, daily_target)

    prompt = (
        f"Пользователь записал приём пищи ({meal_type}): {description}\n\n"
        "Проанализируй и верни ТОЛЬКО JSON в формате (без markdown, без ```json):\n"
        '{"calories": число, "protein": число, "fat": число, "carbs": число, "summary": "краткое описание"}\n\n'
        "Рассчитай калории и БЖУ максимально точно на основе указанных продуктов и порций."
    )

    response = await get_ai_response(prompt, user_ctx)

    import json
    try:
        clean_response = response.strip()
        if clean_response.startswith("```"):
            lines = clean_response.split("\n")
            clean_response = "\n".join(lines[1:-1])
        nutrition_data = json.loads(clean_response)
        calories = float(nutrition_data.get("calories", 0))
        protein = float(nutrition_data.get("protein", 0))
        fat = float(nutrition_data.get("fat", 0))
        carbs = float(nutrition_data.get("carbs", 0))
        summary = nutrition_data.get("summary", description)
    except (json.JSONDecodeError, ValueError, TypeError):
        calories = 0
        protein = 0
        fat = 0
        carbs = 0
        summary = description

    today = date.today().isoformat()
    add_meal(
        user_id=message.from_user.id,
        date=today,
        meal_type=meal_type,
        description=summary,
        calories=calories,
        protein=protein,
        fat=fat,
        carbs=carbs,
    )

    type_names = {
        "breakfast": "🌅 Завтрак", "lunch": "🍲 Обед",
        "snack": "🍎 Перекус", "dinner": "🌙 Ужин",
    }

    text = (
        f"✅ {type_names.get(meal_type, meal_type)} записан!\n\n"
        f"📝 {summary}\n"
        f"🔥 {round(calories)} ккал\n"
        f"🥩 Белки: {round(protein)}г\n"
        f"🧈 Жиры: {round(fat)}г\n"
        f"🍞 Углеводы: {round(carbs)}г\n\n"
        "Записать ещё приём пищи?",
    )
    await message.answer(
        text[0] if isinstance(text, tuple) else text,
        reply_markup=meal_type_keyboard(),
        parse_mode="HTML",
    )
    await state.clear()
