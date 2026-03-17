"""Telegram keyboard builders — only used during initial profile setup."""

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def gender_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="M", callback_data="gender_male"),
            InlineKeyboardButton(text="F", callback_data="gender_female"),
        ],
    ])


def goal_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Набор массы", callback_data="goal_gain")],
        [InlineKeyboardButton(text="Поддержание формы", callback_data="goal_maintain")],
        [InlineKeyboardButton(text="Похудение", callback_data="goal_lose")],
    ])


def activity_level_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Сидячий", callback_data="activity_sedentary")],
        [InlineKeyboardButton(text="Лёгкая (1-2 тр/нед)", callback_data="activity_light")],
        [InlineKeyboardButton(text="Умеренная (3-4 тр/нед)", callback_data="activity_moderate")],
        [InlineKeyboardButton(text="Высокая (5-6 тр/нед)", callback_data="activity_active")],
        [InlineKeyboardButton(text="Очень высокая (ежедневно)", callback_data="activity_very_active")],
    ])
