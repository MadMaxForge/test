"""Settings handlers for updating profile."""

import logging

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery

from nutrition_bot.database.models import get_user, update_user_field
from nutrition_bot.services.calculator import calculate_daily_target
from nutrition_bot.utils.keyboards import (
    settings_keyboard, goal_keyboard, activity_level_keyboard,
    main_menu_keyboard,
)
from nutrition_bot.utils.formatters import format_profile

logger = logging.getLogger(__name__)

router = Router()


class EditProfile(StatesGroup):
    field = State()
    value = State()


@router.message(F.text == "⚙️ Настройки")
async def settings_menu(message: Message) -> None:
    user = get_user(message.from_user.id)
    if not user:
        await message.answer("Сначала создайте профиль: /start")
        return
    await message.answer(
        "⚙️ <b>Настройки</b>\n\n"
        "Что хотите изменить?",
        reply_markup=settings_keyboard(),
        parse_mode="HTML",
    )


@router.callback_query(F.data == "settings_goal")
async def settings_change_goal(callback: CallbackQuery) -> None:
    await callback.message.edit_text(
        "Выберите новую цель:",
        reply_markup=goal_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data == "settings_activity")
async def settings_change_activity(callback: CallbackQuery) -> None:
    await callback.message.edit_text(
        "Выберите новый уровень активности:",
        reply_markup=activity_level_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data == "settings_profile")
async def settings_edit_profile(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.message.edit_text(
        "Что хотите изменить?\n"
        "Введите в формате:\n"
        "<b>вес 80</b> — изменить вес на 80 кг\n"
        "<b>рост 180</b> — изменить рост на 180 см\n"
        "<b>возраст 30</b> — изменить возраст на 30",
        parse_mode="HTML",
    )
    await state.set_state(EditProfile.value)
    await callback.answer()


@router.message(EditProfile.value)
async def process_profile_edit(message: Message, state: FSMContext) -> None:
    text = message.text.strip().lower()
    parts = text.split()

    if len(parts) != 2:
        await message.answer(
            "Введите в формате: <b>вес 80</b> или <b>рост 175</b> или <b>возраст 25</b>",
            parse_mode="HTML",
        )
        return

    field_map = {
        "вес": ("weight_kg", float, 30, 300, "кг"),
        "рост": ("height_cm", float, 100, 250, "см"),
        "возраст": ("age", int, 10, 120, "лет"),
    }

    field_name = parts[0]
    if field_name not in field_map:
        await message.answer("Доступные поля: вес, рост, возраст")
        return

    db_field, type_fn, min_val, max_val, unit = field_map[field_name]

    try:
        value = type_fn(parts[1].replace(",", "."))
        if value < min_val or value > max_val:
            await message.answer(f"Значение должно быть от {min_val} до {max_val} {unit}")
            return
    except ValueError:
        await message.answer("Введите числовое значение")
        return

    update_user_field(message.from_user.id, db_field, value)

    user = get_user(message.from_user.id)
    daily_target = calculate_daily_target(
        user["gender"], user["weight_kg"], user["height_cm"],
        user["age"], user["activity_level"], user["goal"],
    )

    text = f"✅ {field_name.capitalize()} обновлён: {value} {unit}\n\n" + format_profile(user, daily_target)
    await message.answer(text, reply_markup=main_menu_keyboard(), parse_mode="HTML")
    await state.clear()


@router.callback_query(F.data.startswith("goal_"))
async def update_goal_setting(callback: CallbackQuery) -> None:
    goal = callback.data.replace("goal_", "")
    user = get_user(callback.from_user.id)
    if not user:
        await callback.answer("Профиль не найден")
        return

    update_user_field(callback.from_user.id, "goal", goal)
    user = get_user(callback.from_user.id)
    daily_target = calculate_daily_target(
        user["gender"], user["weight_kg"], user["height_cm"],
        user["age"], user["activity_level"], user["goal"],
    )

    goal_names = {"lose": "🔥 Похудение", "maintain": "⚖️ Поддержание формы", "gain": "🏋️ Набор массы"}
    text = f"✅ Цель изменена: {goal_names.get(goal, goal)}\n\n" + format_profile(user, daily_target)
    await callback.message.edit_text(text, parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data.startswith("activity_"))
async def update_activity_setting(callback: CallbackQuery) -> None:
    level = callback.data.replace("activity_", "")
    user = get_user(callback.from_user.id)
    if not user:
        await callback.answer("Профиль не найден")
        return

    update_user_field(callback.from_user.id, "activity_level", level)
    user = get_user(callback.from_user.id)
    daily_target = calculate_daily_target(
        user["gender"], user["weight_kg"], user["height_cm"],
        user["age"], user["activity_level"], user["goal"],
    )

    activity_names = {
        "sedentary": "🪑 Сидячий",
        "light": "🚶 Лёгкая",
        "moderate": "🏃 Умеренная",
        "active": "💪 Высокая",
        "very_active": "🔥 Очень высокая",
    }
    text = (
        f"✅ Уровень активности изменён: {activity_names.get(level, level)}\n\n"
        + format_profile(user, daily_target)
    )
    await callback.message.edit_text(text, parse_mode="HTML")
    await callback.answer()
