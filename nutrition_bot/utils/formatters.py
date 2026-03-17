"""Text formatters for bot messages."""

from nutrition_bot.services.calculator import get_day_of_week_name


def format_profile(user: dict, daily_target: dict) -> str:
    goal_names = {"lose": "🔥 Похудение", "maintain": "⚖️ Поддержание формы", "gain": "🏋️ Набор массы"}
    gender_names = {"male": "👨 Мужской", "female": "👩 Женский"}
    activity_names = {
        "sedentary": "🪑 Сидячий",
        "light": "🚶 Лёгкая",
        "moderate": "🏃 Умеренная",
        "active": "💪 Высокая",
        "very_active": "🔥 Очень высокая",
    }

    return (
        "📊 <b>Ваш профиль</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"Пол: {gender_names.get(user['gender'], user['gender'])}\n"
        f"Возраст: {user['age']} лет\n"
        f"Рост: {user['height_cm']} см\n"
        f"Вес: {user['weight_kg']} кг\n"
        f"Активность: {activity_names.get(user['activity_level'], user['activity_level'])}\n"
        f"Цель: {goal_names.get(user['goal'], user['goal'])}\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"⚡ Базовый метаболизм: {daily_target['bmr']} ккал\n"
        f"🔥 Суточный расход (TDEE): {daily_target['tdee']} ккал\n"
        f"🎯 Целевые калории: <b>{daily_target['target_calories']} ккал</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"🥩 Белки: {daily_target['protein_g']}г ({daily_target['protein_pct']}%)\n"
        f"🧈 Жиры: {daily_target['fat_g']}г ({daily_target['fat_pct']}%)\n"
        f"🍞 Углеводы: {daily_target['carbs_g']}г ({daily_target['carbs_pct']}%)"
    )


def format_schedule(schedule: list[dict], day: int) -> str:
    day_name = get_day_of_week_name(day)
    if not schedule:
        return f"📅 <b>{day_name}</b>\n\nРасписание пусто. Добавьте активности!"

    type_icons = {"sport": "🏋️", "mental": "🧠"}
    intensity_icons = {"low": "🟢", "medium": "🟡", "high": "🔴"}

    lines = [f"📅 <b>{day_name}</b>\n━━━━━━━━━━━━━━━━━━━━"]
    for entry in schedule:
        icon = type_icons.get(entry["activity_type"], "📌")
        intensity_icon = intensity_icons.get(entry["intensity"], "")
        lines.append(
            f"{icon} {entry['time_start']}-{entry['time_end']}: "
            f"<b>{entry['activity_name']}</b> {intensity_icon}\n"
            f"   ID: {entry['id']}"
        )

    return "\n".join(lines)


def format_schedule_calories(schedule_calories: dict, daily_target: dict) -> str:
    lines = [
        "📊 <b>Расход калорий за день</b>",
        "━━━━━━━━━━━━━━━━━━━━",
    ]

    for detail in schedule_calories["details"]:
        type_icon = "🏋️" if detail["type"] == "sport" else "🧠"
        lines.append(
            f"{type_icon} {detail['name']} ({detail['duration_h']}ч) — "
            f"<b>{detail['calories']} ккал</b>"
        )

    lines.extend([
        "━━━━━━━━━━━━━━━━━━━━",
        f"🏋️ Спорт: {schedule_calories['sport']} ккал",
        f"🧠 Умственная работа: {schedule_calories['mental']} ккал",
        f"🔥 <b>Итого от активностей: {schedule_calories['total']} ккал</b>",
        "",
        f"🎯 Базовая норма: {daily_target['target_calories']} ккал",
        f"📈 С учётом активностей: <b>{daily_target['target_calories'] + schedule_calories['total']} ккал</b>",
        "",
        "💡 <i>При интенсивных тренировках рекомендуется "
        "увеличить потребление белка и углеводов</i>",
    ])

    return "\n".join(lines)


def format_day_stats(
    daily_target: dict,
    meals: list[dict],
    schedule_calories: dict,
) -> str:
    total_cal = sum(m["calories"] for m in meals)
    total_protein = sum(m["protein"] for m in meals)
    total_fat = sum(m["fat"] for m in meals)
    total_carbs = sum(m["carbs"] for m in meals)

    adjusted_target = daily_target["target_calories"] + schedule_calories["total"]

    remaining_cal = adjusted_target - total_cal
    remaining_protein = daily_target["protein_g"] - total_protein
    remaining_fat = daily_target["fat_g"] - total_fat
    remaining_carbs = daily_target["carbs_g"] - total_carbs

    progress = min(100, round(total_cal / adjusted_target * 100)) if adjusted_target > 0 else 0
    bar_filled = progress // 5
    bar_empty = 20 - bar_filled
    progress_bar = "█" * bar_filled + "░" * bar_empty

    meal_type_names = {
        "breakfast": "🌅 Завтрак",
        "lunch": "🍲 Обед",
        "snack": "🍎 Перекус",
        "dinner": "🌙 Ужин",
    }

    lines = [
        "📈 <b>Статистика за день</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        f"Прогресс: [{progress_bar}] {progress}%",
        "",
        f"🔥 Съедено: {round(total_cal)} / {adjusted_target} ккал",
        f"🥩 Белки: {round(total_protein)} / {daily_target['protein_g']}г",
        f"🧈 Жиры: {round(total_fat)} / {daily_target['fat_g']}г",
        f"🍞 Углеводы: {round(total_carbs)} / {daily_target['carbs_g']}г",
        "",
    ]

    if remaining_cal > 0:
        lines.append(f"📌 <b>Осталось: {round(remaining_cal)} ккал</b>")
        lines.append(
            f"   Б: {max(0, round(remaining_protein))}г / "
            f"Ж: {max(0, round(remaining_fat))}г / "
            f"У: {max(0, round(remaining_carbs))}г"
        )
    else:
        lines.append("⚠️ <b>Норма калорий превышена!</b>")

    if meals:
        lines.extend(["", "🍽 <b>Приёмы пищи:</b>"])
        for meal in meals:
            name = meal_type_names.get(meal["meal_type"], meal["meal_type"])
            lines.append(
                f"  {name}: {meal['description']}\n"
                f"    {round(meal['calories'])} ккал | "
                f"Б {round(meal['protein'])}г / Ж {round(meal['fat'])}г / У {round(meal['carbs'])}г"
            )
    else:
        lines.extend(["", "🍽 Пока нет записей о еде."])

    return "\n".join(lines)
