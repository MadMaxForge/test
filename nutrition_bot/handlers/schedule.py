"""Schedule management handlers."""

import logging

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery

from nutrition_bot.database.models import (
    get_user, get_schedule, add_schedule_entry,
    clear_schedule,
)
from nutrition_bot.services.calculator import (
    calculate_daily_target, calculate_schedule_calories,
    get_today_day_of_week,
)
from nutrition_bot.utils.keyboards import (
    schedule_menu_keyboard, day_of_week_keyboard,
    activity_type_keyboard, intensity_keyboard,
)
from nutrition_bot.utils.formatters import format_schedule, format_schedule_calories

logger = logging.getLogger(__name__)

router = Router()


class AddActivity(StatesGroup):
    day = State()
    activity_type = State()
    name = State()
    time_start = State()
    time_end = State()
    intensity = State()


@router.message(F.text == "📅 Расписание")
async def schedule_menu(message: Message) -> None:
    user = get_user(message.from_user.id)
    if not user:
        await message.answer("Сначала создайте профиль: /start")
        return
    await message.answer(
        "📅 <b>Управление расписанием</b>\n\n"
        "Добавьте свои спортивные и умственные активности.\n"
        "Бот рассчитает расход калорий и скорректирует питание.",
        reply_markup=schedule_menu_keyboard(),
        parse_mode="HTML",
    )


@router.callback_query(F.data == "schedule_add")
async def schedule_add_start(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.message.edit_text(
        "Выберите день недели:",
        reply_markup=day_of_week_keyboard("sday"),
    )
    await state.set_state(AddActivity.day)
    await callback.answer()


@router.callback_query(F.data.startswith("sday_"))
async def schedule_pick_day(callback: CallbackQuery, state: FSMContext) -> None:
    day_str = callback.data.replace("sday_", "")
    if day_str == "today":
        day = get_today_day_of_week()
    else:
        day = int(day_str)
    await state.update_data(day=day)
    await callback.message.edit_text(
        "Выберите тип активности:",
        reply_markup=activity_type_keyboard(),
    )
    await state.set_state(AddActivity.activity_type)
    await callback.answer()


@router.callback_query(F.data.startswith("atype_"))
async def schedule_pick_type(callback: CallbackQuery, state: FSMContext) -> None:
    atype = callback.data.replace("atype_", "")
    await state.update_data(activity_type=atype)

    if atype == "sport":
        examples = (
            "Например: бег, плавание, силовая тренировка, "
            "велосипед, футбол, йога, кроссфит"
        )
    else:
        examples = (
            "Например: работа за компьютером, учёба, "
            "программирование, экзамен, совещания"
        )

    await callback.message.edit_text(
        f"Введите название активности:\n\n💡 {examples}"
    )
    await state.set_state(AddActivity.name)
    await callback.answer()


@router.message(AddActivity.name)
async def schedule_enter_name(message: Message, state: FSMContext) -> None:
    await state.update_data(name=message.text.strip())
    await message.answer(
        "Введите время начала (формат ЧЧ:ММ):\n"
        "Например: 09:00"
    )
    await state.set_state(AddActivity.time_start)


@router.message(AddActivity.time_start)
async def schedule_enter_start(message: Message, state: FSMContext) -> None:
    time_str = message.text.strip()
    if not _validate_time(time_str):
        await message.answer("Неверный формат. Введите время в формате ЧЧ:ММ (например, 09:00):")
        return
    await state.update_data(time_start=time_str)
    await message.answer(
        "Введите время окончания (формат ЧЧ:ММ):\n"
        "Например: 10:30"
    )
    await state.set_state(AddActivity.time_end)


@router.message(AddActivity.time_end)
async def schedule_enter_end(message: Message, state: FSMContext) -> None:
    time_str = message.text.strip()
    if not _validate_time(time_str):
        await message.answer("Неверный формат. Введите время в формате ЧЧ:ММ (например, 10:30):")
        return
    await state.update_data(time_end=time_str)
    await message.answer(
        "Выберите интенсивность:",
        reply_markup=intensity_keyboard(),
    )
    await state.set_state(AddActivity.intensity)


@router.callback_query(F.data.startswith("intensity_"))
async def schedule_pick_intensity(callback: CallbackQuery, state: FSMContext) -> None:
    intensity = callback.data.replace("intensity_", "")
    data = await state.get_data()

    add_schedule_entry(
        user_id=callback.from_user.id,
        day_of_week=data["day"],
        time_start=data["time_start"],
        time_end=data["time_end"],
        activity_type=data["activity_type"],
        activity_name=data["name"],
        intensity=intensity,
        calories_per_hour=0,
    )

    from nutrition_bot.services.calculator import (
        estimate_activity_calories, get_day_of_week_name,
    )
    start_parts = data["time_start"].split(":")
    end_parts = data["time_end"].split(":")
    duration_h = max(0, (int(end_parts[0]) * 60 + int(end_parts[1]) -
                         int(start_parts[0]) * 60 - int(start_parts[1])) / 60)

    est_cal = estimate_activity_calories(
        data["activity_type"], data["name"], intensity, duration_h,
    )

    day_name = get_day_of_week_name(data["day"])
    type_name = "🏋️ Спорт" if data["activity_type"] == "sport" else "🧠 Умственная"
    intensity_names = {"low": "лёгкая", "medium": "средняя", "high": "высокая"}

    await callback.message.edit_text(
        f"✅ Активность добавлена!\n\n"
        f"📅 {day_name}\n"
        f"⏰ {data['time_start']} - {data['time_end']} ({round(duration_h, 1)}ч)\n"
        f"📌 {data['name']} ({type_name})\n"
        f"💪 Интенсивность: {intensity_names.get(intensity, intensity)}\n"
        f"🔥 ~{est_cal} ккал\n\n"
        "Добавить ещё?",
        reply_markup=schedule_menu_keyboard(),
        parse_mode="HTML",
    )
    await state.clear()
    await callback.answer()


@router.callback_query(F.data == "schedule_view")
async def schedule_view(callback: CallbackQuery) -> None:
    await callback.message.edit_text(
        "Выберите день для просмотра:",
        reply_markup=day_of_week_keyboard("viewday"),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("viewday_"))
async def schedule_view_day(callback: CallbackQuery) -> None:
    day_str = callback.data.replace("viewday_", "")
    if day_str == "today":
        day = get_today_day_of_week()
    else:
        day = int(day_str)

    entries = get_schedule(callback.from_user.id, day)
    text = format_schedule(entries, day)
    await callback.message.edit_text(text, parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data == "schedule_clear")
async def schedule_clear_menu(callback: CallbackQuery) -> None:
    await callback.message.edit_text(
        "Очистить расписание для дня:",
        reply_markup=day_of_week_keyboard("clearday"),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("clearday_"))
async def schedule_clear_day(callback: CallbackQuery) -> None:
    day_str = callback.data.replace("clearday_", "")
    if day_str == "today":
        day = get_today_day_of_week()
    else:
        day = int(day_str)

    from nutrition_bot.services.calculator import get_day_of_week_name
    count = clear_schedule(callback.from_user.id, day)
    day_name = get_day_of_week_name(day)
    await callback.message.edit_text(
        f"🗑 Расписание на {day_name} очищено (удалено записей: {count}).",
    )
    await callback.answer()


@router.callback_query(F.data == "schedule_calories")
async def schedule_show_calories(callback: CallbackQuery) -> None:
    user = get_user(callback.from_user.id)
    if not user:
        await callback.answer("Сначала создайте профиль: /start")
        return

    day = get_today_day_of_week()
    entries = get_schedule(callback.from_user.id, day)
    schedule_cal = calculate_schedule_calories(entries)
    daily_target = calculate_daily_target(
        user["gender"], user["weight_kg"], user["height_cm"],
        user["age"], user["activity_level"], user["goal"],
    )

    text = format_schedule_calories(schedule_cal, daily_target)
    await callback.message.edit_text(text, parse_mode="HTML")
    await callback.answer()


def _validate_time(time_str: str) -> bool:
    try:
        parts = time_str.split(":")
        if len(parts) != 2:
            return False
        h, m = int(parts[0]), int(parts[1])
        return 0 <= h <= 23 and 0 <= m <= 59
    except ValueError:
        return False
