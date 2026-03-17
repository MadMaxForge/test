"""Unified conversational handler — all text goes through AI."""

import logging
from datetime import date

from aiogram import Router, F
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery, ReplyKeyboardRemove

from nutrition_bot.database.models import (
    get_user, save_user, get_schedule, get_meals_for_date,
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

# In-memory conversation history per user
_conversation_history: dict[int, list[dict]] = {}
MAX_HISTORY = 20


class ProfileSetup(StatesGroup):
    gender = State()
    age = State()
    height = State()
    weight = State()
    activity_level = State()
    goal = State()


def _get_history(user_id: int) -> list[dict]:
    return _conversation_history.setdefault(user_id, [])


def _add_to_history(user_id: int, role: str, content: str) -> None:
    hist = _conversation_history.setdefault(user_id, [])
    hist.append({"role": role, "content": content})
    if len(hist) > MAX_HISTORY:
        _conversation_history[user_id] = hist[-MAX_HISTORY:]


def _build_eaten_context(meals: list[dict]) -> str:
    if not meals:
        return ""
    total_cal = sum(m["calories"] for m in meals)
    total_p = sum(m["protein"] for m in meals)
    total_f = sum(m["fat"] for m in meals)
    total_c = sum(m["carbs"] for m in meals)
    lines = [
        f"Итого: {round(total_cal)} ккал "
        f"(Б {round(total_p)}г / Ж {round(total_f)}г / У {round(total_c)}г)"
    ]
    for m in meals:
        lines.append(
            f"  - {m['meal_type']}: {m['description']} "
            f"({round(m['calories'])} ккал)"
        )
    return "\n".join(lines)


# -- /start -- profile setup --

@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    user = get_user(message.from_user.id)
    if user and user.get("age"):
        daily_target = calculate_daily_target(
            user["gender"], user["weight_kg"], user["height_cm"],
            user["age"], user["activity_level"], user["goal"],
        )
        from nutrition_bot.utils.formatters import format_profile
        text = (
            f"С возвращением, {message.from_user.first_name}!\n\n"
            + format_profile(user, daily_target)
            + "\n\nПросто пиши мне текстом — я твой AI-диетолог. "
            "Могу составить меню, посчитать КБЖУ, предложить продукты. "
            "Спрашивай что угодно!"
        )
        await message.answer(
            text, reply_markup=ReplyKeyboardRemove(), parse_mode="HTML",
        )
    else:
        from nutrition_bot.utils.keyboards import gender_keyboard
        await message.answer(
            f"Привет, {message.from_user.first_name}!\n\n"
            "Я — твой персональный AI-диетолог.\n"
            "Помогу подобрать оптимальное питание с учётом:\n"
            "- Твоего расписания (спорт, работа, учёба)\n"
            "- Целей (похудение, набор массы, поддержание)\n"
            "- Продуктов российского рынка\n"
            "- Гликемического индекса продуктов\n\n"
            "Давай настроим профиль! Выбери пол:",
            reply_markup=gender_keyboard(),
        )
        await state.set_state(ProfileSetup.gender)


@router.message(Command("profile"))
async def cmd_profile(message: Message) -> None:
    user = get_user(message.from_user.id)
    if not user:
        await message.answer("Профиль не найден. /start для создания.")
        return
    daily_target = calculate_daily_target(
        user["gender"], user["weight_kg"], user["height_cm"],
        user["age"], user["activity_level"], user["goal"],
    )
    from nutrition_bot.utils.formatters import format_profile
    await message.answer(format_profile(user, daily_target), parse_mode="HTML")


@router.message(Command("stats"))
async def cmd_stats(message: Message) -> None:
    user = get_user(message.from_user.id)
    if not user:
        await message.answer("Профиль не найден. /start для создания.")
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
    from nutrition_bot.utils.formatters import format_day_stats
    await message.answer(
        format_day_stats(daily_target, meals_today, schedule_cal),
        parse_mode="HTML",
    )


@router.message(Command("reset"))
async def cmd_reset(message: Message, state: FSMContext) -> None:
    await state.clear()
    _conversation_history.pop(message.from_user.id, None)
    from nutrition_bot.utils.keyboards import gender_keyboard
    await message.answer(
        "Профиль сброшен. Давай создадим новый!\nВыбери пол:",
        reply_markup=gender_keyboard(),
    )
    await state.set_state(ProfileSetup.gender)


@router.message(Command("clear"))
async def cmd_clear(message: Message) -> None:
    _conversation_history.pop(message.from_user.id, None)
    await message.answer("История диалога очищена.")


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(
        "Я — AI-диетолог. Просто пиши мне текстом!\n\n"
        "Примеры:\n"
        "- Составь меню на день\n"
        "- Съел куриную грудку 200г с рисом 150г\n"
        "- Что поесть перед тренировкой?\n"
        "- Какой ГИ у гречки?\n"
        "- Список покупок на неделю\n"
        "- Сколько я сегодня съел?\n\n"
        "Команды:\n"
        "/start — перезапуск\n"
        "/profile — показать профиль\n"
        "/stats — статистика дня\n"
        "/reset — пересоздать профиль\n"
        "/clear — очистить историю диалога\n"
        "/help — эта справка",
    )


# -- Profile setup callbacks (inline keyboards ONLY during initial setup) --

@router.callback_query(F.data.startswith("gender_"))
async def process_gender(callback: CallbackQuery, state: FSMContext) -> None:
    gender = callback.data.replace("gender_", "")
    await state.update_data(gender=gender)
    await callback.message.edit_text("Введи свой возраст (лет):")
    await state.set_state(ProfileSetup.age)
    await callback.answer()


@router.message(ProfileSetup.age)
async def process_age(message: Message, state: FSMContext) -> None:
    try:
        age = int(message.text.strip())
        if age < 10 or age > 120:
            await message.answer("Введи реальный возраст (10-120):")
            return
    except ValueError:
        await message.answer("Введи число. Например: 25")
        return
    await state.update_data(age=age)
    await message.answer("Введи свой рост (см):")
    await state.set_state(ProfileSetup.height)


@router.message(ProfileSetup.height)
async def process_height(message: Message, state: FSMContext) -> None:
    try:
        height = float(message.text.strip().replace(",", "."))
        if height < 100 or height > 250:
            await message.answer("Введи реальный рост (100-250 см):")
            return
    except ValueError:
        await message.answer("Введи число. Например: 175")
        return
    await state.update_data(height=height)
    await message.answer("Введи свой вес (кг):")
    await state.set_state(ProfileSetup.weight)


@router.message(ProfileSetup.weight)
async def process_weight(message: Message, state: FSMContext) -> None:
    try:
        weight = float(message.text.strip().replace(",", "."))
        if weight < 30 or weight > 300:
            await message.answer("Введи реальный вес (30-300 кг):")
            return
    except ValueError:
        await message.answer("Введи число. Например: 75")
        return
    await state.update_data(weight=weight)
    from nutrition_bot.utils.keyboards import activity_level_keyboard
    await message.answer(
        "Выбери уровень физической активности:",
        reply_markup=activity_level_keyboard(),
    )
    await state.set_state(ProfileSetup.activity_level)


@router.callback_query(F.data.startswith("activity_"))
async def process_activity(callback: CallbackQuery, state: FSMContext) -> None:
    current = await state.get_state()
    if current != ProfileSetup.activity_level.state:
        await callback.answer()
        return
    level = callback.data.replace("activity_", "")
    await state.update_data(activity_level=level)
    from nutrition_bot.utils.keyboards import goal_keyboard
    await callback.message.edit_text(
        "Выбери свою цель:", reply_markup=goal_keyboard(),
    )
    await state.set_state(ProfileSetup.goal)
    await callback.answer()


@router.callback_query(F.data.startswith("goal_"))
async def process_goal(callback: CallbackQuery, state: FSMContext) -> None:
    current = await state.get_state()
    if current != ProfileSetup.goal.state:
        await callback.answer()
        return
    goal = callback.data.replace("goal_", "")
    data = await state.get_data()

    save_user(
        user_id=callback.from_user.id,
        username=callback.from_user.username or "",
        gender=data["gender"],
        age=data["age"],
        height_cm=data["height"],
        weight_kg=data["weight"],
        activity_level=data["activity_level"],
        goal=goal,
    )

    user = get_user(callback.from_user.id)
    daily_target = calculate_daily_target(
        user["gender"], user["weight_kg"], user["height_cm"],
        user["age"], user["activity_level"], user["goal"],
    )

    from nutrition_bot.utils.formatters import format_profile
    text = (
        "Профиль создан!\n\n" + format_profile(user, daily_target)
        + "\n\nТеперь просто пиши мне текстом. Например:\n"
        "- Составь меню на день\n"
        "- Что поесть на завтрак?\n"
        "- Съел омлет из 3 яиц"
    )
    await callback.message.edit_text(text, parse_mode="HTML")
    await state.clear()
    await callback.answer()


# -- Main conversational handler (catch-all for text) --

@router.message(F.text)
async def handle_text(message: Message) -> None:
    """Route ALL free-form text messages through AI."""
    user = get_user(message.from_user.id)
    if not user or not user.get("age"):
        await message.answer("Сначала создай профиль: /start")
        return

    await message.answer("Думаю...")

    daily_target = calculate_daily_target(
        user["gender"], user["weight_kg"], user["height_cm"],
        user["age"], user["activity_level"], user["goal"],
    )
    user_ctx = build_user_context(user, daily_target)

    day = get_today_day_of_week()
    schedule = get_schedule(message.from_user.id, day)
    schedule_cal = calculate_schedule_calories(schedule)
    schedule_ctx = build_schedule_context(schedule, schedule_cal)

    today = date.today().isoformat()
    meals_today = get_meals_for_date(message.from_user.id, today)
    eaten_ctx = _build_eaten_context(meals_today)

    calendar_ctx = None
    try:
        from nutrition_bot.services.calendar_service import (
            get_today_events_text,
        )
        calendar_ctx = await get_today_events_text(message.from_user.id)
    except Exception:
        pass

    history = _get_history(message.from_user.id)

    response = await get_ai_response(
        user_message=message.text,
        user_context=user_ctx,
        schedule_context=schedule_ctx,
        history=history,
        eaten_context=eaten_ctx,
        calendar_context=calendar_ctx,
    )

    _add_to_history(message.from_user.id, "user", message.text)
    _add_to_history(message.from_user.id, "assistant", response)

    if len(response) > 4000:
        for i in range(0, len(response), 4000):
            await message.answer(response[i:i + 4000])
    else:
        await message.answer(response)
