"""Calorie and macro calculator based on user profile and activities."""

from datetime import datetime

ACTIVITY_MULTIPLIERS = {
    "sedentary": 1.2,
    "light": 1.375,
    "moderate": 1.55,
    "active": 1.725,
    "very_active": 1.9,
}

ACTIVITY_CALORIES_PER_HOUR = {
    "sport": {
        "low": {
            "бег трусцой": 400, "ходьба": 200, "йога": 180,
            "растяжка": 150, "пилатес": 200, "велосипед (медленно)": 250,
            "плавание (медленно)": 350, "настольный теннис": 250,
        },
        "medium": {
            "бег": 600, "плавание": 500, "велосипед": 450,
            "футбол": 500, "баскетбол": 480, "волейбол": 350,
            "теннис": 400, "бокс (тренировка)": 550,
            "силовая тренировка": 400, "кроссфит": 550,
            "танцы": 350, "гребля": 450, "лыжи": 500,
            "скалолазание": 500, "единоборства": 550,
        },
        "high": {
            "бег (интервальный)": 800, "спринт": 900,
            "кроссфит (интенсивный)": 700, "бокс (спарринг)": 750,
            "плавание (интенсивное)": 700, "хоккей": 650,
            "гребля (интенсивная)": 650, "HIIT": 750,
            "тяжёлая атлетика": 500, "борьба": 700,
        },
    },
    "mental": {
        "low": {"чтение": 80, "медитация": 70, "лёгкая работа за ПК": 90},
        "medium": {
            "работа за компьютером": 110, "учёба": 120,
            "программирование": 120, "совещания": 100,
            "преподавание": 130,
        },
        "high": {
            "экзамен": 150, "интенсивная умственная работа": 140,
            "публичное выступление": 140, "сложные переговоры": 130,
        },
    },
}

GOAL_ADJUSTMENTS = {
    "lose": -400,
    "maintain": 0,
    "gain": 400,
}

MACRO_RATIOS = {
    "lose": {"protein": 0.35, "fat": 0.25, "carbs": 0.40},
    "maintain": {"protein": 0.30, "fat": 0.30, "carbs": 0.40},
    "gain": {"protein": 0.30, "fat": 0.25, "carbs": 0.45},
}


def calculate_bmr(gender: str, weight_kg: float, height_cm: float, age: int) -> float:
    """Mifflin-St Jeor formula."""
    if gender == "male":
        return 10 * weight_kg + 6.25 * height_cm - 5 * age + 5
    else:
        return 10 * weight_kg + 6.25 * height_cm - 5 * age - 161


def calculate_tdee(bmr: float, activity_level: str) -> float:
    multiplier = ACTIVITY_MULTIPLIERS.get(activity_level, 1.55)
    return bmr * multiplier


def calculate_daily_target(
    gender: str,
    weight_kg: float,
    height_cm: float,
    age: int,
    activity_level: str,
    goal: str,
) -> dict:
    bmr = calculate_bmr(gender, weight_kg, height_cm, age)
    tdee = calculate_tdee(bmr, activity_level)
    adjustment = GOAL_ADJUSTMENTS.get(goal, 0)
    target_calories = tdee + adjustment

    ratios = MACRO_RATIOS.get(goal, MACRO_RATIOS["maintain"])

    protein_calories = target_calories * ratios["protein"]
    fat_calories = target_calories * ratios["fat"]
    carbs_calories = target_calories * ratios["carbs"]

    return {
        "bmr": round(bmr),
        "tdee": round(tdee),
        "target_calories": round(target_calories),
        "protein_g": round(protein_calories / 4),
        "fat_g": round(fat_calories / 9),
        "carbs_g": round(carbs_calories / 4),
        "protein_pct": round(ratios["protein"] * 100),
        "fat_pct": round(ratios["fat"] * 100),
        "carbs_pct": round(ratios["carbs"] * 100),
    }


def estimate_activity_calories(
    activity_type: str,
    activity_name: str,
    intensity: str,
    duration_hours: float,
) -> float:
    type_data = ACTIVITY_CALORIES_PER_HOUR.get(activity_type, {})
    intensity_data = type_data.get(intensity, {})

    cal_per_hour = intensity_data.get(activity_name.lower(), 0)
    if cal_per_hour == 0:
        defaults = {"sport": {"low": 200, "medium": 400, "high": 650},
                    "mental": {"low": 80, "medium": 110, "high": 140}}
        cal_per_hour = defaults.get(activity_type, {}).get(intensity, 300)

    return round(cal_per_hour * duration_hours)


def calculate_schedule_calories(schedule: list[dict]) -> dict:
    """Calculate total calories burned from schedule entries."""
    total = 0.0
    sport_total = 0.0
    mental_total = 0.0
    details = []

    for entry in schedule:
        start_parts = entry["time_start"].split(":")
        end_parts = entry["time_end"].split(":")
        start_minutes = int(start_parts[0]) * 60 + int(start_parts[1])
        end_minutes = int(end_parts[0]) * 60 + int(end_parts[1])
        duration_hours = max(0, (end_minutes - start_minutes) / 60)

        if entry.get("calories_per_hour", 0) > 0:
            calories = round(entry["calories_per_hour"] * duration_hours)
        else:
            calories = estimate_activity_calories(
                entry["activity_type"],
                entry["activity_name"],
                entry["intensity"],
                duration_hours,
            )

        total += calories
        if entry["activity_type"] == "sport":
            sport_total += calories
        else:
            mental_total += calories

        details.append({
            "name": entry["activity_name"],
            "type": entry["activity_type"],
            "duration_h": round(duration_hours, 1),
            "calories": calories,
        })

    return {
        "total": round(total),
        "sport": round(sport_total),
        "mental": round(mental_total),
        "details": details,
    }


def get_day_of_week_name(day: int) -> str:
    names = {
        0: "Понедельник", 1: "Вторник", 2: "Среда",
        3: "Четверг", 4: "Пятница", 5: "Суббота", 6: "Воскресенье",
    }
    return names.get(day, "Неизвестно")


def get_today_day_of_week() -> int:
    return datetime.now().weekday()
