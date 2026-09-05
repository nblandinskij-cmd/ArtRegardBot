#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import re
import json
import os
from datetime import datetime, timedelta
import csv
import io
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes,
)

# ---------- Файлы ----------
SETTINGS_FILE = "bot_settings.json"
MASTERS_FILE = "masters.json"
INCOMES_FILE = "incomes.json"
DEFAULT_PERCENT = 70.0

# ---------- Загрузка/сохранение ----------
def load_json(file, default):
    if os.path.exists(file):
        try:
            with open(file, "r", encoding="utf-8") as f:
                return json.load(f)
        except:
            return default
    return default

def save_json(file, data):
    with open(file, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

settings = load_json(SETTINGS_FILE, {"deduction_percent": DEFAULT_PERCENT})
deduction_percent = settings.get("deduction_percent", DEFAULT_PERCENT)
masters = load_json(MASTERS_FILE, [])
incomes = load_json(INCOMES_FILE, [])

# ---------- Парсинг чисел ----------
def parse_total_from_text(text: str) -> float:
    numbers = re.findall(r"\d+(\.\d+)?", text)
    total = 0.0
    for num in numbers:
        try:
            total += float(num[0] if isinstance(num, tuple) else num)
        except:
            continue
    return total

# ---------- Расчёт зарплаты ----------
def calculate_salary(accrued: float, percent: float) -> dict:
    region_coef = 1.0
    bonus = 0.0
    deduction_fixed = 0.0
    advance = 0.0
    total_before_coef = accrued + bonus
    total_accrued = total_before_coef * region_coef
    deduction_percent_amount = total_accrued * (percent / 100.0)
    deductions = deduction_percent_amount + deduction_fixed
    net_salary = total_accrued - deductions - advance
    return {
        "accrued": accrued,
        "total_accrued": total_accrued,
        "deductions": deductions,
        "net_salary": net_salary,
        "percent": percent
    }

# ---------- Команды ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Я помогаю считать зарплату по списку работ.\n"
        "📌 Отправь мне текст со строками вида:\n"
        "Название 1300 перевод\n"
        "Название 2650 нал\n"
        "Я найду все числа и посчитаю сумму.\n"
        "Затем я спрошу, для какого мастера записать доход.\n\n"
        "Команды:\n"
        "/add_master <имя> – добавить мастера\n"
        "/remove_master <имя> – удалить мастера\n"
        "/masters – список мастеров\n"
        "/incomes – список всех доходов с номерами\n"
        "/edit_income <номер> <новая_сумма> – изменить сумму дохода\n"
        "/stats [имя] [период] – статистика\n"
        "/export – экспортировать все доходы в CSV\n"
        "/percent – изменить процент удержания\n"
        "/help – эта справка"
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start(update, context)

# ---------- Мастера ----------
async def add_master(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if not args:
        await update.message.reply_text("Укажите имя мастера: /add_master Иван")
        return
    name = " ".join(args).strip()
    if name in masters:
        await update.message.reply_text(f"Мастер {name} уже существует.")
        return
    masters.append(name)
    save_json(MASTERS_FILE, masters)
    await update.message.reply_text(f"✅ Мастер {name} добавлен.")

async def remove_master(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if not args:
        await update.message.reply_text("Укажите имя мастера: /remove_master Иван")
        return
    name = " ".join(args).strip()
    if name not in masters:
        await update.message.reply_text(f"Мастер {name} не найден.")
        return
    masters.remove(name)
    save_json(MASTERS_FILE, masters)
    await update.message.reply_text(f"✅ Мастер {name} удалён.")

async def list_masters(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not masters:
        await update.message.reply_text("Список мастеров пуст.")
        return
    text = "📋 Список мастеров:\n" + "\n".join(f"• {m}" for m in masters)
    await update.message.reply_text(text)

# ---------- Доходы: список, редактирование ----------
async def list_incomes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not incomes:
        await update.message.reply_text("Нет записей о доходах.")
        return
    lines = ["📋 Все доходы (номер : мастер : сумма : дата):"]
    for i, inc in enumerate(incomes, 1):
        lines.append(f"{i}. {inc['master']} – {inc['amount']:.2f} руб. ({inc['date'][:10]})")
    for i in range(0, len(lines), 20):
        await update.message.reply_text("\n".join(lines[i:i+20]))

async def edit_income(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if len(args) < 2:
        await update.message.reply_text("Использование: /edit_income <номер> <новая_сумма>")
        return
    try:
        idx = int(args[0]) - 1
        if idx < 0 or idx >= len(incomes):
            await update.message.reply_text("Некорректный номер записи.")
            return
        new_amount = float(args[1])
        if new_amount < 0:
            await update.message.reply_text("Сумма не может быть отрицательной.")
            return
        incomes[idx]["amount"] = new_amount
        save_json(INCOMES_FILE, incomes)
        await update.message.reply_text(f"✅ Доход №{idx+1} обновлён: {new_amount:.2f} руб.")
    except ValueError:
        await update.message.reply_text("Ошибка: введите число.")

# ---------- Экспорт в CSV ----------
async def export_csv(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not incomes:
        await update.message.reply_text("Нет данных для экспорта.")
        return
    output = io.StringIO()
    writer = csv.writer(output, delimiter=';')
    writer.writerow(["Мастер", "Сумма", "Дата", "Исходный текст"])
    for inc in incomes:
        writer.writerow([inc["master"], inc["amount"], inc["date"], inc.get("text", "")])
    output.seek(0)
    await update.message.reply_document(
        document=output.getvalue().encode('utf-8-sig'),
        filename="incomes.csv",
        caption="📊 Экспорт доходов в CSV (открывается в Excel)"
    )

# ---------- Процент ----------
async def set_percent(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("70%", callback_data="70"),
         InlineKeyboardButton("60%", callback_data="60")],
        [InlineKeyboardButton("50%", callback_data="50"),
         InlineKeyboardButton("40%", callback_data="40")],
        [InlineKeyboardButton("Своё...", callback_data="custom")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Выберите процент удержания:", reply_markup=reply_markup)

async def percent_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    if data == "custom":
        await query.edit_message_text("Введите число от 0 до 100 (например, 65):")
        context.user_data["waiting_percent"] = True
        return
    try:
        new_percent = float(data)
        if 0 <= new_percent <= 100:
            global deduction_percent
            deduction_percent = new_percent
            settings["deduction_percent"] = new_percent
            save_json(SETTINGS_FILE, settings)
            await query.edit_message_text(f"✅ Процент удержания установлен на {new_percent:.1f}%")
        else:
            await query.edit_message_text("❌ Процент должен быть от 0 до 100.")
    except:
        await query.edit_message_text("❌ Ошибка.")

async def handle_percent_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("waiting_percent"):
        return
    try:
        new_percent = float(update.message.text.strip())
        if 0 <= new_percent <= 100:
            global deduction_percent
            deduction_percent = new_percent
            settings["deduction_percent"] = new_percent
            save_json(SETTINGS_FILE, settings)
            await update.message.reply_text(f"✅ Процент удержания установлен на {new_percent:.1f}%")
        else:
            await update.message.reply_text("❌ Процент должен быть от 0 до 100.")
    except:
        await update.message.reply_text("❌ Введите число, например 65.")
    context.user_data["waiting_percent"] = False

# ---------- Основной расчёт ----------
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get("waiting_percent"):
        await handle_percent_input(update, context)
        return

    text = update.message.text
    accrued = parse_total_from_text(text)
    if accrued == 0:
        await update.message.reply_text("❌ Не найдено чисел в сообщении.")
        return

    result = calculate_salary(accrued, deduction_percent)
    context.user_data["last_result"] = result
    context.user_data["last_text"] = text

    if not masters:
        keyboard = [[InlineKeyboardButton("Пропустить (без мастера)", callback_data="skip_master")]]
    else:
        keyboard = []
        row = []
        for m in masters:
            row.append(InlineKeyboardButton(m, callback_data=f"master_{m}"))
            if len(row) == 2:
                keyboard.append(row)
                row = []
        if row:
            keyboard.append(row)
        keyboard.append([InlineKeyboardButton("❌ Не записывать", callback_data="skip_master")])

    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        f"📊 Начислено: {result['accrued']:.2f} руб.\n"
        f"Удержание {result['percent']:.1f}%: {result['deductions']:.2f} руб.\n"
        f"💵 К выдаче: {result['net_salary']:.2f} руб.\n\n"
        f"Записать этот доход для мастера?",
        reply_markup=reply_markup
    )

async def master_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    if data == "skip_master":
        await query.edit_message_text("Доход не записан.")
        return
    if data.startswith("master_"):
        master_name = data[7:]
        result = context.user_data.get("last_result")
        text = context.user_data.get("last_text", "")
        if not result:
            await query.edit_message_text("❌ Ошибка: результат не найден.")
            return
        income_entry = {
            "master": master_name,
            "amount": result["net_salary"],
            "date": datetime.now().isoformat(),
            "text": text
        }
        incomes.append(income_entry)
        save_json(INCOMES_FILE, incomes)
        await query.edit_message_text(
            f"✅ Доход {result['net_salary']:.2f} руб. записан для мастера {master_name}."
        )

# ---------- Статистика ----------
def parse_period(args):
    if not args:
        return None, None, None, True
    possible_name = args[0]
    if possible_name in masters:
        name = possible_name
        rest = args[1:]
    else:
        name = None
        rest = args

    period_keywords = ["день", "сегодня", "неделя", "месяц", "год"]
    if rest:
        first = rest[0].lower()
        if first in period_keywords:
            return name, first, None, False
        elif len(rest) >= 2:
            try:
                d1 = datetime.strptime(rest[0], "%Y-%m-%d")
                d2 = datetime.strptime(rest[1], "%Y-%m-%d")
                if d1 > d2:
                    d1, d2 = d2, d1
                return name, (d1, d2), None, False
            except ValueError:
                pass
    return name, None, None, True

def filter_incomes_by_period(incomes_list, name, period_spec):
    now = datetime.now()
    start_date = None
    end_date = None

    if period_spec is None:
        pass
    elif isinstance(period_spec, tuple) and len(period_spec) == 2:
        start_date, end_date = period_spec
    elif isinstance(period_spec, str):
        if period_spec in ["день", "сегодня"]:
            start_date = datetime(now.year, now.month, now.day)
            end_date = start_date + timedelta(days=1) - timedelta(seconds=1)
        elif period_spec == "неделя":
            start_date = now - timedelta(days=7)
        elif period_spec == "месяц":
            start_date = now - timedelta(days=30)
        elif period_spec == "год":
            start_date = now - timedelta(days=365)

    filtered = []
    for inc in incomes_list:
        if name and inc.get("master") != name:
            continue
        if start_date:
            inc_date = datetime.fromisoformat(inc["date"])
            if inc_date < start_date:
                continue
            if end_date and inc_date > end_date:
                continue
        filtered.append(inc)
    return filtered

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    name, period_spec, _, all_history = parse_period(args)

    if name is None:
        if not incomes:
            await update.message.reply_text("Нет записей о доходах.")
            return
        filtered = filter_incomes_by_period(incomes, None, period_spec if not all_history else None)
        if not filtered:
            await update.message.reply_text("Нет данных за выбранный период.")
            return
        stats_dict = {}
        for inc in filtered:
            m = inc["master"]
            stats_dict[m] = stats_dict.get(m, 0) + inc["amount"]
        total_all = sum(stats_dict.values())
        lines = ["📊 *Общая статистика*"]
        for m, s in stats_dict.items():
            lines.append(f"• {m}: {s:.2f} руб.")
        lines.append(f"\n🏷 *Итого по всем мастерам: {total_all:.2f} руб.*")
        if period_spec:
            lines.append(f"\n📅 Период: {period_spec}")
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
        return

    filtered = filter_incomes_by_period(incomes, name, period_spec if not all_history else None)
    if not filtered:
        await update.message.reply_text(f"Нет данных по мастеру {name}{' за указанный период' if period_spec else ''}.")
        return
    total = sum(inc["amount"] for inc in filtered)
    count = len(filtered)
    text = f"📊 Статистика мастера *{name}*\n"
    text += f"💰 Всего доход: {total:.2f} руб.\n"
    text += f"📦 Количество операций: {count}\n"
    if count > 0:
        text += f"📈 Средний чек: {total/count:.2f} руб."
    if period_spec:
        text += f"\n📅 Период: {period_spec}"
    await update.message.reply_text(text, parse_mode="Markdown")

# ---------- Запуск ----------
def main():
    token = input("Введите токен бота: ").strip()
    if not token:
        print("Токен не введён.")
        return

    app = Application.builder().token(token).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("add_master", add_master))
    app.add_handler(CommandHandler("remove_master", remove_master))
    app.add_handler(CommandHandler("masters", list_masters))
    app.add_handler(CommandHandler("incomes", list_incomes))
    app.add_handler(CommandHandler("edit_income", edit_income))
    app.add_handler(CommandHandler("export", export_csv))
    app.add_handler(CommandHandler("percent", set_percent))
    app.add_handler(CommandHandler("stats", stats))

    app.add_handler(CallbackQueryHandler(percent_callback, pattern="^(70|60|50|40|custom)$"))
    app.add_handler(CallbackQueryHandler(master_callback, pattern="^(master_|skip_master)"))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("✅ Бот запущен. Нажмите Ctrl+C для остановки.")
    app.run_polling()

if __name__ == "__main__":
    main()
