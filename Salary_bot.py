#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import re, json, os, csv, io
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes

# ---------- Конфигурация ----------
SETTINGS_FILE, MASTERS_FILE, INCOMES_FILE, USERS_FILE = "bot_settings.json", "masters.json", "incomes.json", "users.json"
DEFAULT_PERCENT = 70.0

# ---------- Клавиатура ----------
MAIN_KB = ReplyKeyboardMarkup([
    ["➕ Добавить мастера", "📋 Список мастеров"],
    ["📊 Статистика", "🏆 Рейтинг"],
    ["📤 Экспорт CSV", "⚙️ Процент удержания"],
    ["❓ Помощь"]
], resize_keyboard=True)

# ---------- Менеджер данных ----------
class DataManager:
    def __init__(self):
        self.settings = self._load(SETTINGS_FILE, {"deduction_percent": DEFAULT_PERCENT})
        self.masters = self._load(MASTERS_FILE, [])
        self.incomes = self._load(INCOMES_FILE, [])
        self.users = self._load(USERS_FILE, {})
    @staticmethod
    def _load(f, d):
        return json.load(open(f, encoding='utf-8')) if os.path.exists(f) else d
    def _save(self, f, d):
        with open(f, 'w', encoding='utf-8') as fp: json.dump(d, fp, indent=2, ensure_ascii=False)
    def save_settings(self): self._save(SETTINGS_FILE, self.settings)
    def save_masters(self): self._save(MASTERS_FILE, self.masters)
    def save_incomes(self): self._save(INCOMES_FILE, self.incomes)
    def save_users(self): self._save(USERS_FILE, self.users)

# ---------- Парсинг чисел ----------
def parse_total_from_text(text):
    if not text: return 0.0
    cleaned = re.sub(r'(?<=\d)\s+(?=\d)', '', text)
    cleaned = re.sub(r'(?<=\d),(?=\d)', '.', cleaned)
    return sum(float(n) for n in re.findall(r'-?\d+(?:\.\d+)?', cleaned) if n)

def get_message_text(update):
    return update.message.text or update.message.caption or ""

# ---------- Расчёт зарплаты ----------
def calculate_salary(accrued, percent):
    total_accrued = accrued
    deduction_amount = total_accrued * (percent / 100.0)
    return {"accrued": accrued, "total_accrued": total_accrued,
            "deductions": deduction_amount, "net_salary": total_accrued - deduction_amount, "percent": percent}

# ---------- Фильтрация по периоду ----------
def filter_incomes(incomes_list, master_name=None, period=None):
    now = datetime.now()
    start_date = end_date = None
    if period:
        if period in ("день","сегодня"):
            start_date, end_date = datetime(now.year, now.month, now.day), datetime(now.year, now.month, now.day) + timedelta(days=1) - timedelta(seconds=1)
        elif period == "неделя": start_date = now - timedelta(days=7)
        elif period == "месяц": start_date = now - timedelta(days=30)
        elif period == "год": start_date = now - timedelta(days=365)
        elif isinstance(period, tuple) and len(period)==2:
            start_date, end_date = period
    return [inc for inc in incomes_list if (not master_name or inc.get("master")==master_name) and
            (not start_date or (start_date <= datetime.fromisoformat(inc["date"]) and
                               (not end_date or datetime.fromisoformat(inc["date"]) <= end_date)))]

# ---------- Вспомогательные функции ----------
def build_period_keyboard(callback_prefix):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📅 Сегодня", callback_data=f"{callback_prefix}_сегодня"),
         InlineKeyboardButton("📅 Неделя", callback_data=f"{callback_prefix}_неделя")],
        [InlineKeyboardButton("📅 Месяц", callback_data=f"{callback_prefix}_месяц"),
         InlineKeyboardButton("📅 Год", callback_data=f"{callback_prefix}_год")],
        [InlineKeyboardButton("📅 Вся история", callback_data=f"{callback_prefix}_все")],
        [InlineKeyboardButton("📅 Произвольный диапазон", callback_data=f"{callback_prefix}_custom")]
    ])

def parse_period_arg(arg):
    if arg in ("день","сегодня","неделя","месяц","год"): return arg
    parts = arg.split()
    if len(parts)==2:
        try:
            d1, d2 = datetime.strptime(parts[0], "%Y-%m-%d"), datetime.strptime(parts[1], "%Y-%m-%d")
            return (d1, d2) if d1 <= d2 else (d2, d1)
        except: pass
    return None

# ---------- Основной класс ----------
class SalaryBot:
    def __init__(self, token):
        self.data = DataManager()
        self.percent = self.data.settings.get("deduction_percent", DEFAULT_PERCENT)
        self.app = Application.builder().token(token).build()
        self._register_handlers()

    def _register_handlers(self):
        for cmd, handler in [("start", self.start), ("help", self.help), ("add_master", self.add_master),
                             ("remove_master", self.remove_master), ("masters", self.list_masters),
                             ("incomes", self.list_incomes), ("edit_income", self.edit_income),
                             ("export", self.export_csv), ("percent", self.set_percent),
                             ("stats", self.stats), ("rating", self.rating),
                             ("register", self.register), ("unregister", self.unregister)]:
            self.app.add_handler(CommandHandler(cmd, handler))
        self.app.add_handler(CallbackQueryHandler(self.callback_handler, pattern="^(percent|master_|skip_master|stats_master_|stats_period_|rating_period_)"))
        self.app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_message))

    # ---------- Команды ----------
    async def start(self, update, ctx):
        await update.message.reply_text("👋 Я считаю зарплату по списку работ.\nОтправь текст с числами, я найду их и посчитаю.", reply_markup=MAIN_KB)
    async def help(self, update, ctx):
        await update.message.reply_text(
            "📖 Справка:\n➕ Добавить мастера\n📋 Список мастеров\n📊 Статистика\n🏆 Рейтинг\n📤 Экспорт CSV\n⚙️ Процент удержания\n❓ Помощь\n/register <имя> – привязать себя\n/unregister – отвязать", reply_markup=MAIN_KB)

    # ---------- Регистрация ----------
    async def register(self, update, ctx):
        args, uid = ctx.args, update.effective_user.id
        if not args: return await update.message.reply_text("Укажите имя: /register Иван")
        name = " ".join(args).strip()
        if name not in self.data.masters: return await update.message.reply_text(f"Мастер '{name}' не существует.")
        if any(m==name and int(u)!=uid for u,m in self.data.users.items()):
            return await update.message.reply_text(f"Имя '{name}' уже занято.")
        self.data.users[str(uid)] = name; self.data.save_users()
        await update.message.reply_text(f"✅ Вы зарегистрированы как мастер '{name}'.")
    async def unregister(self, update, ctx):
        uid = str(update.effective_user.id)
        if uid in self.data.users: del self.data.users[uid]; self.data.save_users(); await update.message.reply_text("✅ Вы отвязаны.")
        else: await update.message.reply_text("Вы не зарегистрированы.")

    # ---------- Мастера ----------
    async def add_master(self, update, ctx):
        if ctx.args:
            name = " ".join(ctx.args).strip()
            if name in self.data.masters: return await update.message.reply_text(f"Мастер {name} уже есть.")
            self.data.masters.append(name); self.data.save_masters(); await update.message.reply_text(f"✅ Мастер {name} добавлен.")
        else:
            await update.message.reply_text("Введите имя нового мастера:")
            ctx.user_data["waiting_for_master_name"] = True
    async def remove_master(self, update, ctx):
        name = " ".join(ctx.args).strip()
        if not name: return await update.message.reply_text("Укажите имя: /remove_master Иван")
        if name not in self.data.masters: return await update.message.reply_text(f"Мастер {name} не найден.")
        self.data.masters.remove(name); self.data.save_masters(); await update.message.reply_text(f"✅ Мастер {name} удалён.")
    async def list_masters(self, update, ctx):
        if not self.data.masters: return await update.message.reply_text("Список мастеров пуст.")
        await update.message.reply_text("📋 Мастера:\n" + "\n".join(f"• {m}" for m in self.data.masters))

    # ---------- Доходы ----------
    async def list_incomes(self, update, ctx):
        if not self.data.incomes: return await update.message.reply_text("Нет записей.")
        lines = ["📋 Доходы (№ : мастер : сумма : дата):"]
        for i, inc in enumerate(self.data.incomes, 1):
            lines.append(f"{i}. {inc['master']} – {inc['amount']:.2f} ({inc['date'][:10]})")
        for chunk in [lines[i:i+20] for i in range(0, len(lines), 20)]:
            await update.message.reply_text("\n".join(chunk))
    async def edit_income(self, update, ctx):
        args = ctx.args
        if len(args)<2: return await update.message.reply_text("Использование: /edit_income <номер> <сумма>")
        try:
            idx = int(args[0])-1
            if idx < 0 or idx >= len(self.data.incomes): return await update.message.reply_text("Некорректный номер.")
            new_amount = float(args[1])
            if new_amount < 0: return await update.message.reply_text("Сумма не может быть отрицательной.")
            self.data.incomes[idx]["amount"] = new_amount; self.data.save_incomes()
            await update.message.reply_text(f"✅ Доход №{idx+1} обновлён: {new_amount:.2f}")
        except ValueError: await update.message.reply_text("Ошибка: введите число.")
    async def export_csv(self, update, ctx):
        if not self.data.incomes: return await update.message.reply_text("Нет данных.")
        output = io.StringIO()
        csv.writer(output, delimiter=';').writerow(["Мастер","Сумма","Дата","Исходный текст"])
        for inc in self.data.incomes:
            csv.writer(output, delimiter=';').writerow([inc["master"], inc["amount"], inc["date"], inc.get("text","")])
        output.seek(0)
        await update.message.reply_document(document=output.getvalue().encode('utf-8-sig'), filename="incomes.csv", caption="📊 Экспорт доходов")

    # ---------- Процент ----------
    async def set_percent(self, update, ctx):
        await update.message.reply_text("Выберите процент:", reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("70%", callback_data="percent_70"), InlineKeyboardButton("60%", callback_data="percent_60")],
            [InlineKeyboardButton("50%", callback_data="percent_50"), InlineKeyboardButton("40%", callback_data="percent_40")],
            [InlineKeyboardButton("Своё...", callback_data="percent_custom")]
        ]))
    async def _handle_percent_input(self, update, ctx):
        try:
            new_percent = float(update.message.text.strip())
            if 0 <= new_percent <= 100:
                self.percent = new_percent
                self.data.settings["deduction_percent"] = new_percent
                self.data.save_settings()
                await update.message.reply_text(f"✅ Удержание установлено на {new_percent:.1f}%")
            else: await update.message.reply_text("❌ От 0 до 100.")
        except: await update.message.reply_text("❌ Введите число.")
        ctx.user_data["waiting_percent"] = False

    # ---------- Статистика ----------
    async def stats(self, update, ctx):
        if ctx.args:
            name = ctx.args[0]
            period = parse_period_arg(" ".join(ctx.args[1:])) if len(ctx.args)>1 else None
            if name in self.data.masters:
                return await self._show_stats_or_rating(update, ctx, "stats", master=name, period=period)
            else:
                return await self._show_stats_or_rating(update, ctx, "stats", master=None, period=name)
        # Показать выбор мастера
        keyboard = [[InlineKeyboardButton("📊 Все мастера", callback_data="stats_master_все")]]
        for m in self.data.masters: keyboard.append([InlineKeyboardButton(m, callback_data=f"stats_master_{m}")])
        await update.message.reply_text("Выберите мастера:", reply_markup=InlineKeyboardMarkup(keyboard))

    # ---------- Рейтинг ----------
    async def rating(self, update, ctx):
        if ctx.args:
            period = parse_period_arg(" ".join(ctx.args))
            if period: return await self._show_stats_or_rating(update, ctx, "rating", period=period)
        await update.message.reply_text("Выберите период:", reply_markup=build_period_keyboard("rating_period"))

    # ---------- Универсальный вывод статистики/рейтинга ----------
    async def _show_stats_or_rating(self, update, ctx, mode, master=None, period=None, reply_target=None):
        if isinstance(period, str) and period in ("день","сегодня","неделя","месяц","год","все"):
            period_spec = None if period=="все" else period
        elif isinstance(period, tuple):
            period_spec = period
        else:
            period_spec = None

        filtered = filter_incomes(self.data.incomes, master_name=master if master!="все" else None, period=period_spec)
        if not filtered:
            msg = "Нет данных за выбранный период."
            if reply_target: return await reply_target.edit_text(msg)
            return await update.message.reply_text(msg)

        period_display = {"сегодня":"Сегодня","неделя":"Неделя","месяц":"Месяц","год":"Год","все":"Всю историю"}.get(period if isinstance(period,str) else "все", str(period)) if period else "весь период"

        if mode == "stats":
            if master and master != "все":
                total = sum(inc["amount"] for inc in filtered)
                count = len(filtered)
                text = f"📊 Статистика мастера *{master}* за {period_display}\n💰 {total:.2f} руб.\n📦 {count} записей"
                if count: text += f"\n📈 Средний чек: {total/count:.2f} руб."
            else:
                stats = {}
                for inc in filtered:
                    stats[inc["master"]] = stats.get(inc["master"], 0) + inc["amount"]
                total_all = sum(stats.values())
                text = f"📊 *Общая статистика за {period_display}*\n" + "\n".join(f"• {m}: {s:.2f} руб." for m,s in stats.items())
                text += f"\n\n🏷 *Итого: {total_all:.2f} руб.*"
        else:  # rating
            rating_dict = {}
            for inc in filtered:
                rating_dict[inc["master"]] = rating_dict.get(inc["master"], 0) + inc["amount"]
            sorted_rating = sorted(rating_dict.items(), key=lambda x: x[1], reverse=True)
            lines = [f"🏆 *Рейтинг мастеров за {period_display}*"]
            for i, (m, amt) in enumerate(sorted_rating, 1):
                medal = "🥇" if i==1 else "🥈" if i==2 else "🥉" if i==3 else f"{i}."
                lines.append(f"{medal} {m} – {amt:.2f} руб.")
            text = "\n".join(lines)

        if reply_target: await reply_target.edit_text(text, parse_mode="Markdown")
        else: await update.message.reply_text(text, parse_mode="Markdown")

    # ---------- Callback-обработчик ----------
    async def callback_handler(self, update, ctx):
        query = update.callback_query
        await query.answer()
        data = query.data

        # Процент
        if data.startswith("percent_"):
            val = data.split("_")[1]
            if val == "custom":
                await query.edit_message_text("Введите число от 0 до 100:")
                ctx.user_data["waiting_percent"] = True
            else:
                p = float(val)
                self.percent = p
                self.data.settings["deduction_percent"] = p
                self.data.save_settings()
                await query.edit_message_text(f"✅ Удержание установлено на {p:.1f}%")
            return

        # Выбор мастера для статистики
        if data.startswith("stats_master_"):
            master = data.replace("stats_master_", "")
            ctx.user_data["stats_master"] = master
            await query.edit_message_text(f"Мастер: {master if master!='все' else 'Все'}\nВыберите период:", reply_markup=build_period_keyboard("stats_period"))
            return

        # Выбор периода для статистики
        if data.startswith("stats_period_"):
            period = data.replace("stats_period_", "")
            if period == "custom":
                await query.edit_message_text("Введите две даты через пробел (ГГГГ-ММ-ДД ГГГГ-ММ-ДД):")
                ctx.user_data["waiting_custom_dates"] = True
                return
            master = ctx.user_data.get("stats_master", "все")
            await self._show_stats_or_rating(update, ctx, "stats", master=master, period=period, reply_target=query.message)
            return

        # Выбор периода для рейтинга
        if data.startswith("rating_period_"):
            period = data.replace("rating_period_", "")
            if period == "custom":
                await query.edit_message_text("Введите две даты через пробел (ГГГГ-ММ-ДД ГГГГ-ММ-ДД):")
                ctx.user_data["waiting_rating_dates"] = True
                return
            await self._show_stats_or_rating(update, ctx, "rating", period=period, reply_target=query.message)
            return

        # Запись дохода (выбор мастера)
        if data.startswith("master_") or data == "skip_master":
            if data == "skip_master":
                await query.edit_message_text("Доход не записан.")
                return
            master_name = data[7:]
            result = ctx.user_data.get("last_result")
            text = ctx.user_data.get("last_text", "")
            if not result:
                await query.edit_message_text("❌ Ошибка: результат не найден.")
                return
            self.data.incomes.append({"master": master_name, "amount": result["net_salary"],
                                      "date": datetime.now().isoformat(), "text": text})
            self.data.save_incomes()
            await query.edit_message_text(f"✅ Доход {result['net_salary']:.2f} руб. записан для мастера {master_name}.")

    # ---------- Обработчик сообщений ----------
    async def handle_message(self, update, ctx):
        user_id = update.effective_user.id
        text = get_message_text(update)
        if not text: return

        # Кнопки
        btn_map = {
            "➕ Добавить мастера": self.add_master,
            "📋 Список мастеров": self.list_masters,
            "📊 Статистика": self.stats,
            "🏆 Рейтинг": self.rating,
            "📤 Экспорт CSV": self.export_csv,
            "⚙️ Процент удержания": self.set_percent,
            "❓ Помощь": self.help
        }
        if text in btn_map:
            await btn_map[text](update, ctx)
            return

        # Ожидание ввода имени мастера
        if ctx.user_data.get("waiting_for_master_name"):
            name = text.strip()
            if name in self.data.masters:
                await update.message.reply_text(f"Мастер {name} уже есть.")
            else:
                self.data.masters.append(name); self.data.save_masters()
                await update.message.reply_text(f"✅ Мастер {name} добавлен.")
            ctx.user_data["waiting_for_master_name"] = False
            return

        # Ожидание процента
        if ctx.user_data.get("waiting_percent"):
            await self._handle_percent_input(update, ctx)
            return

        # Ожидание дат для статистики/рейтинга
        if ctx.user_data.get("waiting_custom_dates") or ctx.user_data.get("waiting_rating_dates"):
            parts = text.split()
            if len(parts) == 2:
                try:
                    d1 = datetime.strptime(parts[0], "%Y-%m-%d")
                    d2 = datetime.strptime(parts[1], "%Y-%m-%d")
                    period = (d1, d2) if d1 <= d2 else (d2, d1)
                    if ctx.user_data.get("waiting_custom_dates"):
                        master = ctx.user_data.get("stats_master", "все")
                        ctx.user_data["waiting_custom_dates"] = False
                        await self._show_stats_or_rating(update, ctx, "stats", master=master, period=period)
                    else:
                        ctx.user_data["waiting_rating_dates"] = False
                        await self._show_stats_or_rating(update, ctx, "rating", period=period)
                    return
                except ValueError: await update.message.reply_text("Ошибка формата. Введите даты как ГГГГ-ММ-ДД ГГГГ-ММ-ДД")
            else: await update.message.reply_text("Введите две даты через пробел.")
            return

        # Основной расчёт
        accrued = parse_total_from_text(text)
        if accrued == 0:
            return await update.message.reply_text("❌ Не найдено чисел. Пример: 'Лексус 1300 перевод'")

        result = calculate_salary(accrued, self.percent)
        master = self.data.users.get(str(user_id))
        if master:
            self.data.incomes.append({"master": master, "amount": result["net_salary"],
                                      "date": datetime.now().isoformat(), "text": text})
            self.data.save_incomes()
            await update.message.reply_text(
                f"📊 Начислено: {result['accrued']:.2f} руб.\nУдержание {result['percent']:.1f}%: {result['deductions']:.2f} руб.\n"
                f"💵 К выдаче: {result['net_salary']:.2f} руб.\n\n✅ Доход автоматически записан на мастера {master}.")
        else:
            ctx.user_data["last_result"] = result
            ctx.user_data["last_text"] = text
            keyboard = []
            if self.data.masters:
                row = []
                for m in self.data.masters:
                    row.append(InlineKeyboardButton(m, callback_data=f"master_{m}"))
                    if len(row) == 2:
                        keyboard.append(row); row = []
                if row: keyboard.append(row)
            keyboard.append([InlineKeyboardButton("❌ Не записывать", callback_data="skip_master")])
            await update.message.reply_text(
                f"📊 Начислено: {result['accrued']:.2f} руб.\nУдержание {result['percent']:.1f}%: {result['deductions']:.2f} руб.\n"
                f"💵 К выдаче: {result['net_salary']:.2f} руб.\n\nВы не зарегистрированы. Хотите записать доход для мастера?",
                reply_markup=InlineKeyboardMarkup(keyboard))

    # ---------- Запуск ----------
    def run(self):
        print("✅ Бот запущен.")
        self.app.run_polling()

# ---------- Точка входа ----------
def main():
    token = os.environ.get("BOT_TOKEN")
    if not token:
        try: token = input("Введите токен бота: ").strip()
        except EOFError: print("Установите переменную BOT_TOKEN"); return
    if not token: print("Токен не введён."); return
    SalaryBot(token).run()

if __name__ == "__main__":
    main()
