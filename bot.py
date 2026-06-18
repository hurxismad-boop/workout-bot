import sqlite3
import asyncio
from datetime import datetime, timedelta
import os
import sys

from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters


# V5: токен берём из переменной окружения BOT_TOKEN.
# Так безопаснее: токен не лежит в коде и не попадает в GitHub.
TOKEN = os.getenv("BOT_TOKEN")

if not TOKEN:
    print("ОШИБКА: не найден BOT_TOKEN.")
    print("Локально в PowerShell напиши:")
    print('$env:BOT_TOKEN="твой_токен_от_BotFather"')
    print("py bot.py")
    sys.exit(1)

# Для Render с persistent disk можно поставить:
# DATABASE_PATH=/var/data/workouts_v5.db
DATABASE_PATH = os.getenv("DATABASE_PATH", "workouts_v5.db")
db_dir = os.path.dirname(DATABASE_PATH)
if db_dir:
    os.makedirs(db_dir, exist_ok=True)

conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
cursor = conn.cursor()


cursor.execute("""
CREATE TABLE IF NOT EXISTS workouts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    date TEXT NOT NULL,
    muscle TEXT NOT NULL,
    exercise TEXT NOT NULL,
    weight REAL NOT NULL,
    reps INTEGER NOT NULL,
    sets INTEGER NOT NULL,
    volume REAL NOT NULL,
    one_rm REAL NOT NULL
)
""")


cursor.execute("""
CREATE TABLE IF NOT EXISTS body_weight (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    date TEXT NOT NULL,
    weight REAL NOT NULL
)
""")


cursor.execute("""
CREATE TABLE IF NOT EXISTS user_settings (
    user_id INTEGER PRIMARY KEY,
    plan_type TEXT DEFAULT 'ppl'
)
""")


conn.commit()


main_menu = ReplyKeyboardMarkup(
    [
        ["➕ Тренировка", "📊 Статистика"],
        ["📈 Прогресс", "📅 План"],
        ["🍗 Питание", "🏆 Рекорды"],
        ["🔥 Уровень", "💤 Восстановление"],
        ["🏅 Достижения", "⏱ Таймер отдыха"],
        ["🗑 Удалить последнюю", "⚖️ Вес"],
        ["🧠 1RM"],
    ],
    resize_keyboard=True
)


muscle_menu = ReplyKeyboardMarkup(
    [
        ["Грудь", "Спина"],
        ["Ноги", "Плечи"],
        ["Бицепс", "Трицепс"],
        ["Пресс", "Икры"],
        ["⬅️ Назад"],
    ],
    resize_keyboard=True
)


plan_menu = ReplyKeyboardMarkup(
    [
        ["PPL", "Upper/Lower"],
        ["Full Body"],
        ["⬅️ Назад"],
    ],
    resize_keyboard=True
)


def exercises_menu(muscle):
    exercises = {
        "грудь": ["Жим_лёжа", "Жим_гантелей", "Разводка", "Отжимания"],
        "спина": ["Тяга_штанги", "Тяга_блока", "Подтягивания", "Становая"],
        "ноги": ["Присед", "Жим_ногами", "Выпады", "Сгибание_ног"],
        "плечи": ["Жим_стоя", "Махи_в_стороны", "Жим_гантелей", "Тяга_к_подбородку"],
        "бицепс": ["Подъём_штанги", "Молотки", "Подъём_гантелей"],
        "трицепс": ["Французский_жим", "Блок_на_трицепс", "Отжимания_на_брусьях"],
        "пресс": ["Скручивания", "Планка", "Подъём_ног"],
        "икры": ["Подъём_на_носки", "Икры_в_тренажёре"],
    }

    buttons = [[exercise] for exercise in exercises.get(muscle, [])]
    buttons.append(["✏️ Свое упражнение"])
    buttons.append(["⬅️ Назад"])

    return ReplyKeyboardMarkup(buttons, resize_keyboard=True)


def fmt_num(value):
    value = float(value)
    if value.is_integer():
        return str(int(value))
    return f"{value:.1f}"


def parse_float(text):
    return float(text.replace(",", "."))


def calculate_1rm(weight, reps):
    return weight * (1 + reps / 30)


def make_sparkline(values):
    """
    Текстовый график без странных символов.
    Хорошо читается в Telegram.
    """
    if not values:
        return "нет данных"

    values = [float(v) for v in values]
    min_v = min(values)
    max_v = max(values)

    lines = []
    max_bar_length = 12

    for i, value in enumerate(values, start=1):
        if max_v == min_v:
            bar_length = 6
        else:
            bar_length = round((value - min_v) / (max_v - min_v) * max_bar_length)
            if bar_length < 1:
                bar_length = 1

        bar = "#" * bar_length
        lines.append(f"{i}. {fmt_num(value)} кг | {bar}")

    return "\n".join(lines)


def save_workout(user_id, muscle, exercise, weight, reps, sets):
    volume = weight * reps * sets
    one_rm = calculate_1rm(weight, reps)

    cursor.execute(
        """
        INSERT INTO workouts
        (user_id, date, muscle, exercise, weight, reps, sets, volume, one_rm)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            user_id,
            datetime.now().strftime("%Y-%m-%d %H:%M"),
            muscle,
            exercise,
            weight,
            reps,
            sets,
            volume,
            one_rm
        )
    )

    conn.commit()
    return volume, one_rm


def get_plan_type(user_id):
    cursor.execute(
        "SELECT plan_type FROM user_settings WHERE user_id=?",
        (user_id,)
    )

    row = cursor.fetchone()

    if row:
        return row[0]

    cursor.execute(
        "INSERT INTO user_settings (user_id, plan_type) VALUES (?, ?)",
        (user_id, "ppl")
    )
    conn.commit()

    return "ppl"


def set_plan_type(user_id, plan_type):
    cursor.execute(
        """
        INSERT INTO user_settings (user_id, plan_type)
        VALUES (?, ?)
        ON CONFLICT(user_id) DO UPDATE SET plan_type=excluded.plan_type
        """,
        (user_id, plan_type)
    )
    conn.commit()


def get_plan_days(plan_type):
    if plan_type == "upperlower":
        return [
            "Верх тела — грудь, спина, плечи, руки",
            "Низ тела — ноги, ягодицы, икры, пресс",
            "Отдых или лёгкое кардио",
            "Верх тела — объём",
            "Низ тела — объём",
            "Отдых",
            "Отдых или растяжка",
        ]

    if plan_type == "fullbody":
        return [
            "Full Body A — присед, жим, тяга",
            "Отдых",
            "Full Body B — становая, жим стоя, подтягивания",
            "Отдых",
            "Full Body C — ноги, грудь, спина",
            "Отдых",
            "Отдых или лёгкое кардио",
        ]

    return [
        "Push — грудь, плечи, трицепс",
        "Pull — спина, бицепс",
        "Legs — ноги, ягодицы, икры",
        "Отдых",
        "Push — грудь, плечи, трицепс",
        "Pull — спина, бицепс",
        "Legs или отдых",
    ]


def normalize_goal(goal):
    goal = goal.lower()

    if goal in ["масса", "набор", "bulk", "gain"]:
        return "масса"

    if goal in ["сушка", "похудение", "cut", "lose"]:
        return "сушка"

    return "поддержание"


def calculate_nutrition(weight, height, age, goal, gender):
    goal = normalize_goal(goal)
    gender = gender.lower()

    if gender in ["ж", "f", "female", "woman"]:
        bmr = 10 * weight + 6.25 * height - 5 * age - 161
    else:
        bmr = 10 * weight + 6.25 * height - 5 * age + 5

    maintenance = bmr * 1.55

    if goal == "масса":
        calories = maintenance + 300
        protein_low = weight * 1.6
        protein_high = weight * 2.2
    elif goal == "сушка":
        calories = maintenance - 400
        protein_low = weight * 1.8
        protein_high = weight * 2.4
    else:
        calories = maintenance
        protein_low = weight * 1.6
        protein_high = weight * 2.0

    protein = (protein_low + protein_high) / 2
    fats = weight * 0.8
    carbs = (calories - protein * 4 - fats * 9) / 4

    if carbs < 0:
        carbs = 0

    return {
        "goal": goal,
        "bmr": bmr,
        "maintenance": maintenance,
        "calories": calories,
        "protein_low": protein_low,
        "protein_high": protein_high,
        "protein": protein,
        "fats": fats,
        "carbs": carbs,
    }


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()

    await update.message.reply_text(
        "💪 Фитнес-бот v5\n\n"
        "Есть:\n"
        "➕ учёт тренировок\n"
        "📈 прогресс\n"
        "📅 планы тренировок\n"
        "🍗 питание\n"
        "🏆 рекорды\n"
        "🔥 уровни\n"
        "💤 восстановление\n\n"
        "Выбери действие:",
        reply_markup=main_menu
    )


async def add_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 5:
        await update.message.reply_text(
            "Пример:\n"
            "/add грудь жим_лёжа 100 5 4\n\n"
            "Формат:\n"
            "/add мышца упражнение вес повторы подходы"
        )
        return

    muscle = context.args[0].lower()
    exercise = context.args[1].replace("_", " ")

    try:
        weight = parse_float(context.args[2])
        reps = int(context.args[3])
        sets = int(context.args[4])
    except ValueError:
        await update.message.reply_text("Вес, повторы и подходы должны быть числами.")
        return

    volume, one_rm = save_workout(
        update.effective_user.id,
        muscle,
        exercise,
        weight,
        reps,
        sets
    )

    await update.message.reply_text(
        f"✅ Записано:\n"
        f"{muscle} — {exercise}\n"
        f"{fmt_num(weight)} кг × {reps} × {sets}\n"
        f"Объём: {fmt_num(volume)} кг\n"
        f"Примерный 1RM: {fmt_num(one_rm)} кг\n\n"
        f"+50 XP",
        reply_markup=main_menu
    )


async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    cursor.execute(
        """
        SELECT date, muscle, exercise, weight, reps, sets, volume, one_rm
        FROM workouts
        WHERE user_id=?
        ORDER BY id DESC
        LIMIT 10
        """,
        (user_id,)
    )

    rows = cursor.fetchall()

    if not rows:
        await update.message.reply_text("Тренировок пока нет.", reply_markup=main_menu)
        return

    cutoff = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d %H:%M")

    cursor.execute(
        """
        SELECT COUNT(*), COALESCE(SUM(volume), 0)
        FROM workouts
        WHERE user_id=? AND date>=?
        """,
        (user_id, cutoff)
    )

    week_count, week_volume = cursor.fetchone()

    text = (
        "📊 Статистика\n\n"
        f"За 7 дней:\n"
        f"Тренировок: {week_count}\n"
        f"Общий объём: {fmt_num(week_volume)} кг\n\n"
        "Последние записи:\n\n"
    )

    for date, muscle, exercise, weight, reps, sets, volume, one_rm in rows:
        text += (
            f"{date}\n"
            f"{muscle} — {exercise}\n"
            f"{fmt_num(weight)} кг × {reps} × {sets}\n"
            f"Объём: {fmt_num(volume)} кг | 1RM: {fmt_num(one_rm)} кг\n\n"
        )

    await update.message.reply_text(text, reply_markup=main_menu)


async def show_exercise_progress(update: Update, exercise_query):
    user_id = update.effective_user.id
    exercise_query = exercise_query.replace("_", " ").lower().strip()

    cursor.execute(
        """
        SELECT date, exercise, weight, reps, sets, one_rm
        FROM workouts
        WHERE user_id=? AND LOWER(exercise)=?
        ORDER BY date ASC
        """,
        (user_id, exercise_query)
    )

    rows = cursor.fetchall()

    if not rows:
        cursor.execute(
            """
            SELECT date, exercise, weight, reps, sets, one_rm
            FROM workouts
            WHERE user_id=? AND LOWER(exercise) LIKE ?
            ORDER BY date ASC
            """,
            (user_id, f"%{exercise_query}%")
        )
        rows = cursor.fetchall()

    if not rows:
        await update.message.reply_text(
            "Нет данных по этому упражнению.\n\n"
            "Пример:\n/progress жим_лёжа",
            reply_markup=main_menu
        )
        return

    last_rows = rows[-12:]

    weights = [row[2] for row in last_rows]
    one_rms = [row[5] for row in last_rows]

    weight_graph = make_sparkline(weights)
    one_rm_graph = make_sparkline(one_rms)

    first_1rm = one_rms[0]
    last_1rm = one_rms[-1]
    diff = last_1rm - first_1rm

    exercise_name = last_rows[-1][1]

    text = (
        f"📈 Прогресс: {exercise_name}\n\n"
        f"Рабочий вес:\n{weight_graph}\n\n"
        f"Примерный 1RM:\n{one_rm_graph}\n\n"
        f"1RM: {fmt_num(first_1rm)} кг → {fmt_num(last_1rm)} кг\n"
    )

    if diff > 0:
        text += f"Прогресс: +{fmt_num(diff)} кг 🔥\n\n"
    elif diff < 0:
        text += f"Просадка: {fmt_num(diff)} кг ⚠️\n\n"
    else:
        text += "Пока без изменений.\n\n"

    text += "Последние записи:\n"

    for date, exercise, weight, reps, sets, one_rm in last_rows[-5:]:
        text += (
            f"{date}: {fmt_num(weight)}×{reps}×{sets} "
            f"| 1RM {fmt_num(one_rm)}\n"
        )

    await update.message.reply_text(text, reply_markup=main_menu)


async def progress_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text(
            "Напиши упражнение:\n"
            "/progress жим_лёжа\n\n"
            "Или нажми кнопку 📈 Прогресс.",
            reply_markup=main_menu
        )
        return

    exercise_query = " ".join(context.args)
    await show_exercise_progress(update, exercise_query)


async def plan_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    plan_type = get_plan_type(user_id)
    days = get_plan_days(plan_type)

    day_names = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
    today_index = datetime.now().weekday()

    readable = {
        "ppl": "PPL",
        "upperlower": "Upper/Lower",
        "fullbody": "Full Body"
    }

    text = f"📅 Твой план: {readable.get(plan_type, plan_type)}\n\n"

    for i, workout in enumerate(days):
        marker = "➡️" if i == today_index else "•"
        text += f"{marker} {day_names[i]} — {workout}\n"

    text += (
        "\nЧтобы сменить план, выбери кнопку ниже:\n"
        "PPL / Upper/Lower / Full Body"
    )

    await update.message.reply_text(text, reply_markup=plan_menu)


async def setplan_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text(
            "Пример:\n"
            "/setplan ppl\n"
            "/setplan upperlower\n"
            "/setplan fullbody",
            reply_markup=plan_menu
        )
        return

    raw = context.args[0].lower()

    if raw in ["ppl", "pushpulllegs"]:
        plan_type = "ppl"
        name = "PPL"
    elif raw in ["upperlower", "upper/lower", "верхниз"]:
        plan_type = "upperlower"
        name = "Upper/Lower"
    elif raw in ["fullbody", "full", "фулбади"]:
        plan_type = "fullbody"
        name = "Full Body"
    else:
        await update.message.reply_text("Такого плана нет. Выбери PPL, Upper/Lower или Full Body.")
        return

    set_plan_type(update.effective_user.id, plan_type)

    await update.message.reply_text(
        f"✅ План изменён на: {name}",
        reply_markup=main_menu
    )


async def today_plan_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    plan_type = get_plan_type(user_id)
    days = get_plan_days(plan_type)
    today_index = datetime.now().weekday()

    await update.message.reply_text(
        f"📅 Сегодня по плану:\n\n{days[today_index]}",
        reply_markup=main_menu
    )


async def nutrition_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 4:
        await update.message.reply_text(
            "Пример:\n"
            "/calories 82 180 18 масса м\n\n"
            "Формат:\n"
            "/calories вес рост возраст цель пол\n\n"
            "Цель: масса / сушка / поддержание\n"
            "Пол: м или ж",
            reply_markup=main_menu
        )
        return

    try:
        weight = parse_float(context.args[0])
        height = parse_float(context.args[1])
        age = int(context.args[2])
        goal = context.args[3]
        gender = context.args[4] if len(context.args) >= 5 else "м"
    except ValueError:
        await update.message.reply_text("Вес, рост и возраст должны быть числами.")
        return

    result = calculate_nutrition(weight, height, age, goal, gender)

    await update.message.reply_text(
        f"🍗 Питание для цели: {result['goal']}\n\n"
        f"Поддержание: ~{fmt_num(result['maintenance'])} ккал\n"
        f"Твоя цель: ~{fmt_num(result['calories'])} ккал/день\n\n"
        f"Белок: {fmt_num(result['protein_low'])}–{fmt_num(result['protein_high'])} г/день\n"
        f"Жиры: ~{fmt_num(result['fats'])} г/день\n"
        f"Углеводы: ~{fmt_num(result['carbs'])} г/день\n\n"
        f"Это примерный расчёт. Смотри по весу и самочувствию 1–2 недели.",
        reply_markup=main_menu
    )


async def protein_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text(
            "Пример:\n/protein 82 масса",
            reply_markup=main_menu
        )
        return

    try:
        weight = parse_float(context.args[0])
    except ValueError:
        await update.message.reply_text("Вес должен быть числом.")
        return

    goal = context.args[1] if len(context.args) >= 2 else "поддержание"
    goal = normalize_goal(goal)

    if goal == "масса":
        low = weight * 1.6
        high = weight * 2.2
    elif goal == "сушка":
        low = weight * 1.8
        high = weight * 2.4
    else:
        low = weight * 1.6
        high = weight * 2.0

    await update.message.reply_text(
        f"🥩 Белок для цели: {goal}\n\n"
        f"Твой вес: {fmt_num(weight)} кг\n"
        f"Белок: {fmt_num(low)}–{fmt_num(high)} г/день",
        reply_markup=main_menu
    )


async def pr(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cursor.execute(
        """
        SELECT exercise, MAX(weight), MAX(one_rm)
        FROM workouts
        WHERE user_id=?
        GROUP BY exercise
        ORDER BY MAX(one_rm) DESC
        LIMIT 15
        """,
        (update.effective_user.id,)
    )

    rows = cursor.fetchall()

    if not rows:
        await update.message.reply_text("Рекордов пока нет.", reply_markup=main_menu)
        return

    text = "🏆 Твои рекорды:\n\n"

    for exercise, max_weight, max_one_rm in rows:
        text += (
            f"{exercise}\n"
            f"Макс. вес: {fmt_num(max_weight)} кг\n"
            f"Примерный 1RM: {fmt_num(max_one_rm)} кг\n\n"
        )

    await update.message.reply_text(text, reply_markup=main_menu)


async def level(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    cursor.execute(
        "SELECT COUNT(*), COALESCE(SUM(volume), 0) FROM workouts WHERE user_id=?",
        (user_id,)
    )

    count, total_volume = cursor.fetchone()

    xp = count * 50 + int(total_volume // 1000) * 5
    lvl = xp // 300 + 1
    next_level = lvl * 300

    await update.message.reply_text(
        f"🔥 Твой уровень\n\n"
        f"Уровень: {lvl}\n"
        f"XP: {xp}/{next_level}\n"
        f"До следующего уровня: {next_level - xp} XP\n\n"
        f"Тренировок записано: {count}\n"
        f"Общий объём: {fmt_num(total_volume)} кг",
        reply_markup=main_menu
    )


async def recovery(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cursor.execute(
        """
        SELECT muscle, MAX(date)
        FROM workouts
        WHERE user_id=?
        GROUP BY muscle
        """,
        (update.effective_user.id,)
    )

    rows = cursor.fetchall()

    if not rows:
        await update.message.reply_text("Пока нет данных для восстановления.", reply_markup=main_menu)
        return

    recovery_hours = {
        "грудь": (48, 72),
        "спина": (48, 72),
        "ноги": (48, 96),
        "плечи": (24, 72),
        "бицепс": (24, 48),
        "трицепс": (24, 48),
        "пресс": (24, 48),
        "икры": (24, 48),
    }

    text = "💤 Восстановление:\n\n"
    now = datetime.now()

    for muscle, last_date in rows:
        try:
            last_dt = datetime.strptime(last_date, "%Y-%m-%d %H:%M")
            hours_passed = (now - last_dt).total_seconds() / 3600
        except ValueError:
            hours_passed = 0

        low, high = recovery_hours.get(muscle.lower(), (24, 72))

        if hours_passed >= high:
            status = "✅ можно тренировать"
        elif hours_passed >= low:
            status = "⚠️ почти восстановилась"
        else:
            status = "⏳ лучше ещё отдохнуть"

        text += (
            f"{muscle}\n"
            f"Последний раз: {last_date}\n"
            f"Прошло: {int(hours_passed)} ч\n"
            f"Норма: {low}–{high} ч\n"
            f"Статус: {status}\n\n"
        )

    await update.message.reply_text(text, reply_markup=main_menu)


async def delete_last(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    cursor.execute(
        """
        SELECT id, date, muscle, exercise, weight, reps, sets
        FROM workouts
        WHERE user_id=?
        ORDER BY id DESC
        LIMIT 1
        """,
        (user_id,)
    )

    row = cursor.fetchone()

    if not row:
        await update.message.reply_text("Удалять нечего — тренировок пока нет.", reply_markup=main_menu)
        return

    workout_id, date, muscle, exercise, weight, reps, sets = row

    cursor.execute("DELETE FROM workouts WHERE id=? AND user_id=?", (workout_id, user_id))
    conn.commit()

    await update.message.reply_text(
        f"🗑 Удалена последняя тренировка:\n\n"
        f"{date}\n"
        f"{muscle} — {exercise}\n"
        f"{fmt_num(weight)} кг × {reps} × {sets}",
        reply_markup=main_menu
    )


async def rest_timer_task(context: ContextTypes.DEFAULT_TYPE, chat_id, seconds):
    await asyncio.sleep(seconds)
    await context.bot.send_message(
        chat_id=chat_id,
        text=f"⏰ Отдых {seconds} сек окончен. Делай следующий подход!"
    )


async def rest_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Пример:\n/rest 90")
        return

    try:
        seconds = int(context.args[0])
    except ValueError:
        await update.message.reply_text("Время должно быть числом. Например: /rest 90")
        return

    if seconds < 10 or seconds > 3600:
        await update.message.reply_text("Можно поставить от 10 до 3600 секунд.")
        return

    asyncio.create_task(rest_timer_task(context, update.effective_chat.id, seconds))

    await update.message.reply_text(
        f"⏱ Таймер запущен на {seconds} сек.",
        reply_markup=main_menu
    )


async def one_rm_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2:
        await update.message.reply_text(
            "Пример:\n"
            "/onerm 100 5\n\n"
            "Это значит: 100 кг на 5 повторов."
        )
        return

    try:
        weight = parse_float(context.args[0])
        reps = int(context.args[1])
    except ValueError:
        await update.message.reply_text("Вес и повторы должны быть числами.")
        return

    one_rm = calculate_1rm(weight, reps)

    await update.message.reply_text(
        f"🧠 Примерный 1RM:\n\n"
        f"{fmt_num(weight)} кг × {reps}\n"
        f"≈ {fmt_num(one_rm)} кг на 1 раз",
        reply_markup=main_menu
    )


async def achievements(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    cursor.execute(
        """
        SELECT COUNT(*), COALESCE(SUM(volume), 0), COALESCE(MAX(weight), 0), COALESCE(MAX(one_rm), 0)
        FROM workouts
        WHERE user_id=?
        """,
        (user_id,)
    )

    count, total_volume, max_weight, max_one_rm = cursor.fetchone()

    achievement_list = [
        ("🏅 Первая тренировка", count >= 1),
        ("🏅 10 тренировок", count >= 10),
        ("🏅 50 тренировок", count >= 50),
        ("🏅 100 тренировок", count >= 100),
        ("🔥 10 000 кг общего объёма", total_volume >= 10000),
        ("🔥 50 000 кг общего объёма", total_volume >= 50000),
        ("🔥 100 000 кг общего объёма", total_volume >= 100000),
        ("🏋️ 100 кг в упражнении", max_weight >= 100),
        ("🧠 1RM больше 100 кг", max_one_rm >= 100),
        ("🦍 1RM больше 150 кг", max_one_rm >= 150),
    ]

    unlocked = [name for name, ok in achievement_list if ok]
    locked = [name for name, ok in achievement_list if not ok]

    text = "🏅 Достижения\n\n"

    if unlocked:
        text += "Открыто:\n"
        for item in unlocked:
            text += f"✅ {item}\n"
    else:
        text += "Пока нет открытых достижений.\n"

    text += "\nБлижайшие цели:\n"

    for item in locked[:5]:
        text += f"🔒 {item}\n"

    await update.message.reply_text(text, reply_markup=main_menu)


async def add_weight(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Пример:\n/weight 82.5")
        return

    try:
        weight = parse_float(context.args[0])
    except ValueError:
        await update.message.reply_text("Вес должен быть числом.")
        return

    cursor.execute(
        """
        INSERT INTO body_weight (user_id, date, weight)
        VALUES (?, ?, ?)
        """,
        (
            update.effective_user.id,
            datetime.now().strftime("%Y-%m-%d %H:%M"),
            weight
        )
    )

    conn.commit()

    await update.message.reply_text(
        f"⚖️ Вес записан: {fmt_num(weight)} кг",
        reply_markup=main_menu
    )


async def weight_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cursor.execute(
        """
        SELECT date, weight
        FROM body_weight
        WHERE user_id=?
        ORDER BY id ASC
        LIMIT 30
        """,
        (update.effective_user.id,)
    )

    rows = cursor.fetchall()

    if not rows:
        await update.message.reply_text("Вес пока не записан.", reply_markup=main_menu)
        return

    last_rows = rows[-12:]
    weights = [row[1] for row in last_rows]
    graph = make_sparkline(weights)

    text = (
        "⚖️ Прогресс веса тела\n\n"
        f"{graph}\n\n"
        f"{fmt_num(weights[0])} кг → {fmt_num(weights[-1])} кг\n\n"
        "Последние записи:\n"
    )

    for date, weight in last_rows[-7:]:
        text += f"{date} — {fmt_num(weight)} кг\n"

    await update.message.reply_text(text, reply_markup=main_menu)


async def start_workout_flow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    context.user_data["state"] = "awaiting_muscle"

    await update.message.reply_text(
        "Выбери группу мышц:",
        reply_markup=muscle_menu
    )


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    state = context.user_data.get("state")

    if text == "⬅️ Назад":
        context.user_data.clear()
        await update.message.reply_text("Главное меню:", reply_markup=main_menu)
        return

    if text == "➕ Тренировка":
        await start_workout_flow(update, context)
        return

    if text == "📊 Статистика":
        await stats(update, context)
        return

    if text == "📈 Прогресс":
        context.user_data.clear()
        context.user_data["state"] = "awaiting_progress_exercise"
        await update.message.reply_text(
            "Напиши название упражнения.\n\n"
            "Например:\n"
            "Жим лёжа\n"
            "Присед\n"
            "Подтягивания",
            reply_markup=main_menu
        )
        return

    if text == "📅 План":
        await plan_command(update, context)
        return

    if text in ["PPL", "Upper/Lower", "Full Body"]:
        if text == "PPL":
            set_plan_type(update.effective_user.id, "ppl")
        elif text == "Upper/Lower":
            set_plan_type(update.effective_user.id, "upperlower")
        elif text == "Full Body":
            set_plan_type(update.effective_user.id, "fullbody")

        await update.message.reply_text(
            f"✅ План изменён на: {text}",
            reply_markup=main_menu
        )
        return

    if text == "🍗 Питание":
        context.user_data.clear()
        context.user_data["state"] = "awaiting_nutrition"
        await update.message.reply_text(
            "Напиши данные так:\n\n"
            "82 180 18 масса м\n\n"
            "Где:\n"
            "82 — вес\n"
            "180 — рост\n"
            "18 — возраст\n"
            "масса — цель\n"
            "м — пол\n\n"
            "Цель: масса / сушка / поддержание",
            reply_markup=main_menu
        )
        return

    if text == "🏆 Рекорды":
        await pr(update, context)
        return

    if text == "🔥 Уровень":
        await level(update, context)
        return

    if text == "💤 Восстановление":
        await recovery(update, context)
        return

    if text == "🏅 Достижения":
        await achievements(update, context)
        return

    if text == "🗑 Удалить последнюю":
        await delete_last(update, context)
        return

    if text == "⏱ Таймер отдыха":
        context.user_data.clear()
        context.user_data["state"] = "awaiting_rest"
        await update.message.reply_text("На сколько секунд поставить таймер? Например: 90")
        return

    if text == "⚖️ Вес":
        await update.message.reply_text(
            "Записать вес:\n"
            "/weight 82.5\n\n"
            "Посмотреть график веса:\n"
            "/weightstats",
            reply_markup=main_menu
        )
        return

    if text == "🧠 1RM":
        await update.message.reply_text(
            "Напиши:\n"
            "/onerm 100 5\n\n"
            "Это значит: 100 кг на 5 повторов.",
            reply_markup=main_menu
        )
        return

    if state == "awaiting_progress_exercise":
        context.user_data.clear()
        await show_exercise_progress(update, text)
        return

    if state == "awaiting_nutrition":
        parts = text.split()

        if len(parts) < 4:
            await update.message.reply_text("Нужно так:\n82 180 18 масса м")
            return

        try:
            weight = parse_float(parts[0])
            height = parse_float(parts[1])
            age = int(parts[2])
            goal = parts[3]
            gender = parts[4] if len(parts) >= 5 else "м"
        except ValueError:
            await update.message.reply_text("Вес, рост и возраст должны быть числами.")
            return

        context.user_data.clear()

        result = calculate_nutrition(weight, height, age, goal, gender)

        await update.message.reply_text(
            f"🍗 Питание для цели: {result['goal']}\n\n"
            f"Поддержание: ~{fmt_num(result['maintenance'])} ккал\n"
            f"Твоя цель: ~{fmt_num(result['calories'])} ккал/день\n\n"
            f"Белок: {fmt_num(result['protein_low'])}–{fmt_num(result['protein_high'])} г/день\n"
            f"Жиры: ~{fmt_num(result['fats'])} г/день\n"
            f"Углеводы: ~{fmt_num(result['carbs'])} г/день",
            reply_markup=main_menu
        )
        return

    if state == "awaiting_muscle":
        muscle = text.lower()
        allowed = ["грудь", "спина", "ноги", "плечи", "бицепс", "трицепс", "пресс", "икры"]

        if muscle not in allowed:
            await update.message.reply_text("Выбери мышцу кнопкой ниже.", reply_markup=muscle_menu)
            return

        context.user_data["new_workout"] = {"muscle": muscle}
        context.user_data["state"] = "awaiting_exercise"

        await update.message.reply_text(
            "Выбери упражнение:",
            reply_markup=exercises_menu(muscle)
        )
        return

    if state == "awaiting_exercise":
        if text == "✏️ Свое упражнение":
            context.user_data["state"] = "awaiting_custom_exercise"
            await update.message.reply_text("Напиши название упражнения:")
            return

        context.user_data["new_workout"]["exercise"] = text.replace("_", " ")
        context.user_data["state"] = "awaiting_weight"

        await update.message.reply_text(
            "Введи вес в кг.\n\n"
            "Для штанги/гантелей — вес снаряда.\n"
            "Для отжиманий/подтягиваний — свой вес тела.\n"
            "Если есть доп. вес, пиши вес тела + доп. вес.\n\n"
            "Например: 70"
        )
        return

    if state == "awaiting_custom_exercise":
        context.user_data["new_workout"]["exercise"] = text
        context.user_data["state"] = "awaiting_weight"

        await update.message.reply_text(
            "Введи вес в кг.\n\n"
            "Для штанги/гантелей — вес снаряда.\n"
            "Для упражнений с весом тела — свой вес тела.\n\n"
            "Например: 70"
        )
        return

    if state == "awaiting_weight":
        try:
            weight = parse_float(text)
        except ValueError:
            await update.message.reply_text("Вес должен быть числом. Например: 82.5")
            return

        if weight <= 0:
            await update.message.reply_text("Вес должен быть больше 0.")
            return

        context.user_data["new_workout"]["weight"] = weight
        context.user_data["state"] = "awaiting_reps"

        await update.message.reply_text("Сколько повторов? Например: 5")
        return

    if state == "awaiting_reps":
        try:
            reps = int(text)
        except ValueError:
            await update.message.reply_text("Повторы должны быть числом. Например: 5")
            return

        if reps <= 0:
            await update.message.reply_text("Повторы должны быть больше 0.")
            return

        context.user_data["new_workout"]["reps"] = reps
        context.user_data["state"] = "awaiting_sets"

        await update.message.reply_text("Сколько подходов? Например: 4")
        return

    if state == "awaiting_sets":
        try:
            sets = int(text)
        except ValueError:
            await update.message.reply_text("Подходы должны быть числом. Например: 4")
            return

        if sets <= 0:
            await update.message.reply_text("Подходы должны быть больше 0.")
            return

        data = context.user_data["new_workout"]

        muscle = data["muscle"]
        exercise = data["exercise"]
        weight = data["weight"]
        reps = data["reps"]

        volume, one_rm = save_workout(
            update.effective_user.id,
            muscle,
            exercise,
            weight,
            reps,
            sets
        )

        context.user_data.clear()

        await update.message.reply_text(
            f"✅ Тренировка записана:\n\n"
            f"{muscle} — {exercise}\n"
            f"{fmt_num(weight)} кг × {reps} × {sets}\n"
            f"Объём: {fmt_num(volume)} кг\n"
            f"Примерный 1RM: {fmt_num(one_rm)} кг\n\n"
            f"+50 XP",
            reply_markup=main_menu
        )
        return

    if state == "awaiting_rest":
        try:
            seconds = int(text)
        except ValueError:
            await update.message.reply_text("Напиши число секунд. Например: 90")
            return

        if seconds < 10 or seconds > 3600:
            await update.message.reply_text("Можно поставить от 10 до 3600 секунд.")
            return

        context.user_data.clear()
        asyncio.create_task(rest_timer_task(context, update.effective_chat.id, seconds))

        await update.message.reply_text(
            f"⏱ Таймер запущен на {seconds} сек.",
            reply_markup=main_menu
        )
        return

    await update.message.reply_text(
        "Я не понял. Выбери действие в меню или напиши /start.",
        reply_markup=main_menu
    )


def main():
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))

    app.add_handler(CommandHandler("add", add_command))
    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(CommandHandler("progress", progress_command))
    app.add_handler(CommandHandler("plan", plan_command))
    app.add_handler(CommandHandler("setplan", setplan_command))
    app.add_handler(CommandHandler("today", today_plan_command))
    app.add_handler(CommandHandler("calories", nutrition_command))
    app.add_handler(CommandHandler("protein", protein_command))

    app.add_handler(CommandHandler("pr", pr))
    app.add_handler(CommandHandler("level", level))
    app.add_handler(CommandHandler("recovery", recovery))
    app.add_handler(CommandHandler("delete_last", delete_last))
    app.add_handler(CommandHandler("rest", rest_command))
    app.add_handler(CommandHandler("onerm", one_rm_command))
    app.add_handler(CommandHandler("1rm", one_rm_command))
    app.add_handler(CommandHandler("achievements", achievements))
    app.add_handler(CommandHandler("weight", add_weight))
    app.add_handler(CommandHandler("weightstats", weight_stats))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    print("Bot v5 starting...")
    app.run_polling()


if __name__ == "__main__":
    main()
