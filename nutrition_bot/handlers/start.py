"""Start and profile setup handlers."""

import logging

from aiogram import Router, F
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery

from nutrition_bot.database.models import get_user, save_user
from nutrition_bot.services.calculator import calculate_daily_target
from nutrition_bot.utils.keyboards import (
    main_menu_keyboard,
    gender_keyboard,
    goal_keyboard,
    activity_level_keyboard,
)
from nutrition_bot.utils.formatters import format_profile

logger = logging.getLogger(__name__)

router = Router()


class ProfileSetup(StatesGroup):
    gender = State()
    age = State()
    height = State()
    weight = State()
    activity_level = State()
    goal = State()


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    user = get_user(message.from_user.id)
    if user and user.get("age"):
        daily_target = calculate_daily_target(
            user["gender"], user["weight_kg"], user["height_cm"],
            user["age"], user["activity_level"], user["goal"],
        )
        text = (
            f"С возвращением, {message.from_user.first_name}! 👋\n\n"
            + format_profile(user, daily_target)
        )
        await message.answer(text, reply_markup=main_menu_keyboard(), parse_mode="HTML")
    else:
        await message.answer(
            f"Привет, {message.from_user.first_name}! 👋\n\n"
            "Я — бот-диетолог 🥗\n"
            "Помогу подобрать оптимальное питание с учётом:\n"
            "• Твоего расписания (спорт, работа, учёба)\n"
            "• Целей (похудение, набор массы, поддержание)\n"
            "• Продуктов российского рынка\n\n"
            "Давай настроим твой профиль! Выбери пол:",
            reply_markup=gender_keyboard(),
        )
        await state.set_state(ProfileSetup.gender)


@router.callback_query(F.data.startswith("gender_"))
async def process_gender(callback: CallbackQuery, state: FSMContext) -> None:
    gender = callback.data.replace("gender_", "")
    await state.update_data(gender=gender)
    await callback.message.edit_text("Введите ваш возраст (лет):")
    await state.set_state(ProfileSetup.age)
    await callback.answer()


@router.message(ProfileSetup.age)
async def process_age(message: Message, state: FSMContext) -> None:
    try:
        age = int(message.text.strip())
        if age < 10 or age > 120:
            await message.answer("Пожалуйста, введите реальный возраст (10-120):")
            return
    except ValueError:
        await message.answer("Введите число. Например: 25")
        return

    await state.update_data(age=age)
    await message.answer("Введите ваш рост (см):")
    await state.set_state(ProfileSetup.height)


@router.message(ProfileSetup.height)
async def process_height(message: Message, state: FSMContext) -> None:
    try:
        height = float(message.text.strip().replace(",", "."))
        if height < 100 or height > 250:
            await message.answer("Пожалуйста, введите реальный рост (100-250 см):")
            return
    except ValueError:
        await message.answer("Введите число. Например: 175")
        return

    await state.update_data(height=height)
    await message.answer("Введите ваш вес (кг):")
    await state.set_state(ProfileSetup.weight)


@router.message(ProfileSetup.weight)
async def process_weight(message: Message, state: FSMContext) -> None:
    try:
        weight = float(message.text.strip().replace(",", "."))
        if weight < 30 or weight > 300:
            await message.answer("Пожалуйста, введите реальный вес (30-300 кг):")
            return
    except ValueError:
        await message.answer("Введите число. Например: 75")
        return

    await state.update_data(weight=weight)
    await message.answer(
        "Выберите уровень физической активности:",
        reply_markup=activity_level_keyboard(),
    )
    await state.set_state(ProfileSetup.activity_level)


@router.callback_query(F.data.startswith("activity_"))
async def process_activity_level(callback: CallbackQuery, state: FSMContext) -> None:
    current_state = await state.get_state()
    if current_state != ProfileSetup.activity_level.state:
        await callback.answer()
        return

    level = callback.data.replace("activity_", "")
    await state.update_data(activity_level=level)
    await callback.message.edit_text(
        "Выберите вашу цель:",
        reply_markup=goal_keyboard(),
    )
    await state.set_state(ProfileSetup.goal)
    await callback.answer()


@router.callback_query(F.data.startswith("goal_"))
async def process_goal(callback: CallbackQuery, state: FSMContext) -> None:
    current_state = await state.get_state()
    if current_state != ProfileSetup.goal.state:
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

    text = "✅ Профиль создан!\n\n" + format_profile(user, daily_target)
    await callback.message.edit_text(text, parse_mode="HTML")
    await callback.message.answer(
        "Теперь вы можете:\n"
        "• 📅 Настроить расписание\n"
        "• 🍽 Получить меню на день\n"
        "• 🤖 Спросить AI-диетолога\n\n"
        "Используйте кнопки меню ниже 👇",
        reply_markup=main_menu_keyboard(),
    )
    await state.clear()
    await callback.answer()
