"""Food logging command handler."""

from aiogram import Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from nutrition_bot.database.models import get_user
from nutrition_bot.utils.keyboards import meal_type_keyboard
from nutrition_bot.handlers.meals import LogMeal

router = Router()


@router.message(Command("log"))
async def cmd_log_food(message: Message, state: FSMContext) -> None:
    user = get_user(message.from_user.id)
    if not user:
        await message.answer("Сначала создайте профиль: /start")
        return

    await message.answer(
        "🍽 <b>Запись приёма пищи</b>\n\n"
        "Выберите тип приёма пищи:",
        reply_markup=meal_type_keyboard(),
        parse_mode="HTML",
    )
    await state.set_state(LogMeal.meal_type)
