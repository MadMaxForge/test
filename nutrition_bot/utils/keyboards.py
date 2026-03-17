"""Telegram keyboard builders."""

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    KeyboardButton,
)


def main_menu_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📊 Мой профиль"), KeyboardButton(text="🎯 Мои цели")],
            [KeyboardButton(text="📅 Расписание"), KeyboardButton(text="🍽 Меню на день")],
            [KeyboardButton(text="📈 Статистика дня"), KeyboardButton(text="🤖 Спросить AI")],
            [KeyboardButton(text="⚙️ Настройки")],
        ],
        resize_keyboard=True,
    )


def gender_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="👨 Мужской", callback_data="gender_male"),
            InlineKeyboardButton(text="👩 Женский", callback_data="gender_female"),
        ],
    ])


def goal_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🏋️ Набор мышечной массы", callback_data="goal_gain")],
        [InlineKeyboardButton(text="⚖️ Поддержание формы", callback_data="goal_maintain")],
        [InlineKeyboardButton(text="🔥 Похудение", callback_data="goal_lose")],
    ])


def activity_level_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🪑 Сидячий образ жизни", callback_data="activity_sedentary")],
        [InlineKeyboardButton(text="🚶 Лёгкая активность (1-2 тр/нед)", callback_data="activity_light")],
        [InlineKeyboardButton(text="🏃 Умеренная (3-4 тр/нед)", callback_data="activity_moderate")],
        [InlineKeyboardButton(text="💪 Высокая (5-6 тр/нед)", callback_data="activity_active")],
        [InlineKeyboardButton(text="🔥 Очень высокая (ежедневно)", callback_data="activity_very_active")],
    ])


def schedule_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Добавить активность", callback_data="schedule_add")],
        [InlineKeyboardButton(text="📋 Посмотреть расписание", callback_data="schedule_view")],
        [InlineKeyboardButton(text="🗑 Очистить день", callback_data="schedule_clear")],
        [InlineKeyboardButton(text="📊 Расход калорий за день", callback_data="schedule_calories")],
    ])


def day_of_week_keyboard(prefix: str = "day") -> InlineKeyboardMarkup:
    days = [
        ("Пн", 0), ("Вт", 1), ("Ср", 2), ("Чт", 3),
        ("Пт", 4), ("Сб", 5), ("Вс", 6),
    ]
    buttons = [
        InlineKeyboardButton(text=name, callback_data=f"{prefix}_{num}")
        for name, num in days
    ]
    rows = [buttons[:4], buttons[4:]]
    rows.append([InlineKeyboardButton(text="📅 Сегодня", callback_data=f"{prefix}_today")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def activity_type_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🏋️ Спорт", callback_data="atype_sport")],
        [InlineKeyboardButton(text="🧠 Умственная нагрузка", callback_data="atype_mental")],
    ])


def intensity_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🟢 Лёгкая", callback_data="intensity_low")],
        [InlineKeyboardButton(text="🟡 Средняя", callback_data="intensity_medium")],
        [InlineKeyboardButton(text="🔴 Высокая", callback_data="intensity_high")],
    ])


def meal_type_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🌅 Завтрак", callback_data="meal_breakfast"),
            InlineKeyboardButton(text="🍲 Обед", callback_data="meal_lunch"),
        ],
        [
            InlineKeyboardButton(text="🍎 Перекус", callback_data="meal_snack"),
            InlineKeyboardButton(text="🌙 Ужин", callback_data="meal_dinner"),
        ],
    ])


def settings_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👤 Изменить профиль", callback_data="settings_profile")],
        [InlineKeyboardButton(text="🎯 Изменить цель", callback_data="settings_goal")],
        [InlineKeyboardButton(text="📊 Изменить уровень активности", callback_data="settings_activity")],
    ])


def confirm_keyboard(prefix: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Да", callback_data=f"{prefix}_yes"),
            InlineKeyboardButton(text="❌ Нет", callback_data=f"{prefix}_no"),
        ],
    ])
