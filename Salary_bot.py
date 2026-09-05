import re, json, os, csv, io, logging
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ---------- Конфигурация ----------
F = ("bot_settings.json", "masters.json", "incomes.json", "users.json")
DEFAULT_PERCENT = 70.0
BACK = "back_to_menu"

# ---------- Клавиатуры ----------
KB = ReplyKeyboardMarkup([
    ["➕ Добавить мастера", "📋 Список мастеров", "➕ Добавить доход"],
    ["💸 Расходы", "📊 Статистика", "🏆 Рейтинг"],
    ["🧮 Простой расчёт", "📤 Экспорт CSV", "⚙️ Процент удержания"],
    ["❓ Помощь"]
], resize_keyboard=True)

def add_back(k):
    k.append([InlineKeyboardButton("🔙 Назад", callback_data=BACK)])
    return k

def period_kb(p):
    return InlineKeyboardMarkup(add_back([
        [InlineKeyboardButton("📅 Сегодня", callback_data=f"{p}_сегодня"),
         InlineKeyboardButton("📅 Неделя", callback_data=f"{p}_неделя")],
        [InlineKeyboardButton("📅 Месяц", callback_data=f"{p}_месяц"),
         InlineKeyboardButton("📅 Год", callback_data=f"{p}_год")],
        [InlineKeyboardButton("📅 Вся история", callback_data=f"{p}_все"),
         InlineKeyboardButton("📅 Произвольный диапазон", callback_data=f"{p}_custom")]
    ]))

# ---------- Менеджер данных ----------
class DataManager:
    def __init__(self):
        self.settings = self._load(F[0], {"deduction_percent": DEFAULT_PERCENT})
        self.masters = self._load(F[1], [])
        self.incomes = self._load(F[2], [])
        self.users = self._load(F[3], {})

    def _load(self, file, default):
        if os.path.exists(file):
            try:
                with open(file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    if file == F[1] and not isinstance(data, list):
                        return default
                    if file == F[2] and not isinstance(data, list):
                        return default
                    if file == F[3] and not isinstance(data, dict):
                        return default
                    if file == F[0] and not isinstance(data, dict):
                        return default
                    return data
            except:
                return default
        return default

    def _save(self, file, data):
        if file == F[1] and isinstance(data, list):
            data = [str(x) for x in data if x is not None and str(x).strip()]
        elif file == F[0] and isinstance(data, dict):
            if "deduction_percent" in data:
                try:
                    data["deduction_percent"] = float(data["deduction_percent"])
                except:
                    data["deduction_percent"] = DEFAULT_PERCENT
        elif file == F[3] and isinstance(data, dict):
            for uid, name in list(data.items()):
                if name is None or not str(name).strip():
                    del data[uid]
                else:
                    data[uid] = str(name).strip()
        elif file == F[2] and isinstance(data, list):
            for inc in data:
                if "master" in inc:
                    inc["master"] = str(inc["master"]) if inc["master"] else ""
                if "amount" in inc:
                    try:
                        inc["amount"] = float(inc["amount"])
                    except:
                        inc["amount"] = 0.0
        with open(file, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    def save_all(self):
        self._save(F[0], self.settings)
        self._save(F[1], self.masters)
        self._save(F[2], self.incomes)
        self._save(F[3], self.users)

# ---------- Парсинг чисел ----------
def parse_numbers(text):
    if not text:
        return 0.0
    cleaned = re.sub(r'(?<=\d)\s+(?=\d)', '', text)
    def replace_comma(match):
        start = match.start()
        end = match.end()
        after = re.search(r'\d+', cleaned[end:])
        if after:
            digits_after = after.group()
            if len(digits_after) <= 2:
                return '.'
        return ','
    cleaned = re.sub(r'(?<=\d),(?=\d)', replace_comma, cleaned)
    return sum(float(n) for n in re.findall(r'-?\d+(?:\.\d+)?', cleaned) if n)

def get_message_text(update):
    if not update or not update.message:
        return ""
    msg = update.message
    if msg.text:
        return msg.text
    if msg.caption:
        return msg.caption
    if msg.document and msg.document.file_name:
        return msg.document.file_name
    return ""

# ---------- Расчёт ----------
def calculate(accrued, percent):
    try:
        percent = float(percent)
    except (ValueError, TypeError):
        percent = DEFAULT_PERCENT
    deduction = accrued * (percent / 100.0)
    return {
        "accrued": accrued,
        "deductions": deduction,
        "net": accrued - deduction,
        "percent": percent
    }

# ---------- Фильтр доходов по периоду ----------
def filter_incomes(incomes, master_name=None, period=None):
    now = datetime.now()
    start_date = end_date = None
    if period:
        if period in ("день", "сегодня"):
            start_date = datetime(now.year, now.month, now.day)
            end_date = start_date + timedelta(days=1) - timedelta(seconds=1)
        elif period == "неделя":
            start_date = now - timedelta(days=7)
        elif period == "месяц":
            start_date = now - timedelta(days=30)
        elif period == "год":
            start_date = now - timedelta(days=365)
        elif isinstance(period, tuple) and len(period) == 2:
            start_date, end_date = period

    result = []
    for inc in incomes:
        if master_name and inc.get("master") != master_name:
            continue
        if start_date:
            inc_date = datetime.fromisoformat(inc["date"])
            if inc_date < start_date:
                continue
            if end_date and inc_date > end_date:
                continue
        result.append(inc)
    return result

def parse_period(arg):
    if arg in ("день", "сегодня", "неделя", "месяц", "год", "все"):
        return arg
    parts = arg.split()
    if len(parts) == 2:
        try:
            d1 = datetime.strptime(parts[0], "%Y-%m-%d")
            d2 = datetime.strptime(parts[1], "%Y-%m-%d")
            return (d1, d2) if d1 <= d2 else (d2, d1)
        except:
            pass
    return None

# ---------- Основной бот ----------
class SalaryBot:
    def __init__(self, token):
        self.data = DataManager()
        try:
            self.percent = float(self.data.settings.get("deduction_percent", DEFAULT_PERCENT))
        except:
            self.percent = DEFAULT_PERCENT
        self.app = Application.builder().token(token).build()
        self._register_handlers()

    def _register_handlers(self):
        commands = [
            ("start", self.start),
            ("help", self.help),
            ("add_master", self.add_master),
            ("remove_master", self.remove_master),
            ("masters", self.list_masters),
            ("incomes", self.list_incomes),
            ("edit_income", self.edit_income),
            ("export", self.export_csv),
            ("percent", self.set_percent),
            ("stats", self.stats),
            ("rating", self.rating),
            ("register", self.register_user),
            ("unregister", self.unregister_user),
            ("calc", self.calc_command),
            ("add_income", self.add_income_command),
            ("extra", self.show_extra),
            ("clear_extra", self.clear_extra),
            ("add_expense", self.add_expense_command),
            ("expenses", self.show_expenses),
            ("clear_expenses", self.clear_expenses)
        ]
        for cmd, handler in commands:
            self.app.add_handler(CommandHandler(cmd, handler))

        self.app.add_handler(MessageHandler(filters.COMMAND, self.unknown_command))
        self.app.add_handler(CallbackQueryHandler(self.callback, pattern="^(percent|master_|skip_master|stats_master_|stats_period_|rating_period_|edit_master_|back_to_menu|noop)"))
        self.app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_message))
        self.app.add_error_handler(self.error_handler)

    async def error_handler(self, update, context):
        logger.error(f"Ошибка: {context.error}")
        if update and update.effective_message:
            await update.effective_message.reply_text("❌ Внутренняя ошибка. Попробуйте позже.")

    async def unknown_command(self, update, context):
        await update.message.reply_text("❌ Неизвестная команда. Используйте /help.", reply_markup=KB)

    async def start(self, update, context):
        await update.message.reply_text("👋 Я считаю зарплату по списку работ.\nОтправь текст с числами.", reply_markup=KB)

    async def help(self, update, context):
        await update.message.reply_text(
            "📖 Справка:\n"
            "/add_master – добавить мастера\n"
            "/masters – список мастеров (редактор)\n"
            "/stats – статистика\n"
            "/rating – рейтинг\n"
            "/export – выгрузить CSV\n"
            "/percent – изменить процент удержания\n"
            "/register <имя> – привязать себя к мастеру\n"
            "/unregister – отвязаться\n"
            "/calc <текст> – быстрый расчёт без сохранения\n"
            "/add_income <сумма> <описание> – добавить дополнительный доход\n"
            "/extra – показать добавленные доходы\n"
            "/clear_extra – очистить добавленные доходы\n"
            "/add_expense <сумма> <описание> – добавить расход (вычитается из начисления)\n"
            "/expenses – показать расходы\n"
            "/clear_expenses – очистить расходы",
            reply_markup=KB
        )

    # ---------- Регистрация ----------
    async def register_user(self, update, context):
        if not context.args:
            return await update.message.reply_text("Укажите имя: /register Иван")
        name = " ".join(context.args).strip()
        if not name:
            return await update.message.reply_text("Имя не может быть пустым.")
        if name not in self.data.masters:
            return await update.message.reply_text(f"Мастер '{name}' не существует.")
        if any(m == name and int(uid) != update.effective_user.id for uid, m in self.data.users.items()):
            return await update.message.reply_text(f"Имя '{name}' уже занято.")
        self.data.users[str(update.effective_user.id)] = name
        self.data.save_all()
        await update.message.reply_text(f"✅ Вы зарегистрированы как '{name}'.")

    async def unregister_user(self, update, context):
        uid = str(update.effective_user.id)
        if uid in self.data.users:
            del self.data.users[uid]
            self.data.save_all()
            await update.message.reply_text("✅ Вы отвязаны.")
        else:
            await update.message.reply_text("Вы не зарегистрированы.")

    # ---------- Мастера ----------
    async def add_master(self, update, context):
        if context.args:
            name = " ".join(context.args).strip()
            if not name:
                return await update.message.reply_text("Имя не может быть пустым.")
            if name in self.data.masters:
                return await update.message.reply_text(f"Мастер {name} уже есть.")
            self.data.masters.append(name)
            self.data.save_all()
            await update.message.reply_text(f"✅ Мастер {name} добавлен.")
        else:
            await update.message.reply_text("Введите имя нового мастера:")
            context.user_data["wait_master"] = True

    async def remove_master(self, update, context):
        name = " ".join(context.args).strip()
        if not name:
            return await update.message.reply_text("Укажите имя: /remove_master Иван")
        if name not in self.data.masters:
            return await update.message.reply_text(f"Мастер {name} не найден.")
        self.data.masters.remove(name)
        self.data.save_all()
        await update.message.reply_text(f"✅ Мастер {name} удалён.")

    async def list_masters(self, update, context):
        if not self.data.masters:
            return await update.message.reply_text("Список мастеров пуст.")
        keyboard = []
        for m in self.data.masters:
            keyboard.append([
                InlineKeyboardButton(m, callback_data="noop"),
                InlineKeyboardButton("✏️", callback_data=f"edit_master_rename_{m}"),
                InlineKeyboardButton("❌", callback_data=f"edit_master_delete_{m}")
            ])
        keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data=BACK)])
        await update.message.reply_text("📋 Список мастеров:", reply_markup=InlineKeyboardMarkup(keyboard))

    # ---------- Доходы ----------
    async def list_incomes(self, update, context):
        if not self.data.incomes:
            return await update.message.reply_text("Нет записей.")
        lines = ["📋 Доходы:"]
        for i, inc in enumerate(self.data.incomes, 1):
            lines.append(f"{i}. {inc['master']} – {inc['amount']:.2f} ({inc['date'][:10]})")
        for chunk in [lines[i:i+20] for i in range(0, len(lines), 20)]:
            await update.message.reply_text("\n".join(chunk))

    async def edit_income(self, update, context):
        args = context.args
        if len(args) < 2:
            return await update.message.reply_text("/edit_income <номер> <сумма>")
        try:
            idx = int(args[0]) - 1
            if idx < 0 or idx >= len(self.data.incomes):
                return await update.message.reply_text("Некорректный номер.")
            new_amount = float(args[1])
            if new_amount < 0:
                return await update.message.reply_text("Сумма не может быть отрицательной.")
            self.data.incomes[idx]["amount"] = new_amount
            self.data.save_all()
            await update.message.reply_text(f"✅ Доход №{idx+1} обновлён: {new_amount:.2f}")
        except ValueError:
            await update.message.reply_text("Ошибка ввода. Введите числа.")

    async def export_csv(self, update, context):
        if not self.data.incomes:
            return await update.message.reply_text("Нет данных.")
        output = io.StringIO()
        writer = csv.writer(output, delimiter=';')
        writer.writerow(["Мастер", "Сумма", "Дата", "Текст"])
        for inc in self.data.incomes:
            writer.writerow([inc["master"], inc["amount"], inc["date"], inc.get("text", "")])
        output.seek(0)
        await update.message.reply_document(
            document=output.getvalue().encode('utf-8-sig'),
            filename="incomes.csv",
            caption="📊 Экспорт доходов"
        )

    # ---------- Процент ----------
    async def set_percent(self, update, context):
        keyboard = add_back([
            [InlineKeyboardButton("70%", callback_data="percent_70"),
             InlineKeyboardButton("60%", callback_data="percent_60")],
            [InlineKeyboardButton("50%", callback_data="percent_50"),
             InlineKeyboardButton("40%", callback_data="percent_40")],
            [InlineKeyboardButton("Своё...", callback_data="percent_custom")]
        ])
        await update.message.reply_text("Выберите процент:", reply_markup=InlineKeyboardMarkup(keyboard))

    async def _handle_percent_input(self, update, context):
        try:
            new_percent = float(update.message.text.strip())
            if 0 <= new_percent <= 100:
                self.percent = new_percent
                self.data.settings["deduction_percent"] = new_percent
                self.data.save_all()
                await update.message.reply_text(f"✅ Удержание установлено на {new_percent:.1f}%")
            else:
                await update.message.reply_text("❌ Процент должен быть от 0 до 100.")
        except ValueError:
            await update.message.reply_text("❌ Введите число.")
        context.user_data["wait_percent"] = False

    # ---------- Дополнительные доходы ----------
    async def add_income_command(self, update, context):
        if len(context.args) < 2:
            return await update.message.reply_text("Использование: /add_income <сумма> <описание>\nПример: /add_income 5000 Премия")
        amount_str = context.args[0]
        description = " ".join(context.args[1:])
        try:
            amount = float(amount_str)
        except ValueError:
            return await update.message.reply_text("❌ Сумма должна быть числом.")
        if amount <= 0:
            return await update.message.reply_text("❌ Сумма должна быть положительной.")
        if "extra_incomes" not in context.user_data:
            context.user_data["extra_incomes"] = []
        context.user_data["extra_incomes"].append({"amount": amount, "description": description})
        await update.message.reply_text(f"✅ Добавлен дополнительный доход: {amount:.2f} руб. ({description})")

    async def show_extra(self, update, context):
        extras = context.user_data.get("extra_incomes", [])
        if not extras:
            await update.message.reply_text("Нет добавленных доходов.")
            return
        total = sum(e["amount"] for e in extras)
        lines = ["📋 Добавленные доходы:"]
        for i, e in enumerate(extras, 1):
            lines.append(f"{i}. {e['amount']:.2f} руб. – {e['description']}")
        lines.append(f"\n💰 Итого: {total:.2f} руб.")
        await update.message.reply_text("\n".join(lines))

    async def clear_extra(self, update, context):
        if "extra_incomes" in context.user_data:
            del context.user_data["extra_incomes"]
        await update.message.reply_text("🧹 Все добавленные доходы очищены.")

    # ---------- Расходы ----------
    async def add_expense_command(self, update, context):
        if len(context.args) < 2:
            return await update.message.reply_text("Использование: /add_expense <сумма> <описание>\nПример: /add_expense 1500 Материалы")
        amount_str = context.args[0]
        description = " ".join(context.args[1:])
        try:
            amount = float(amount_str)
        except ValueError:
            return await update.message.reply_text("❌ Сумма должна быть числом.")
        if amount <= 0:
            return await update.message.reply_text("❌ Сумма должна быть положительной.")
        if "expenses" not in context.user_data:
            context.user_data["expenses"] = []
        context.user_data["expenses"].append({"amount": amount, "description": description})
        await update.message.reply_text(f"✅ Добавлен расход: {amount:.2f} руб. ({description})")

    async def show_expenses(self, update, context):
        expenses = context.user_data.get("expenses", [])
        if not expenses:
            await update.message.reply_text("Нет добавленных расходов.")
            return
        total = sum(e["amount"] for e in expenses)
        lines = ["📋 Расходы:"]
        for i, e in enumerate(expenses, 1):
            lines.append(f"{i}. {e['amount']:.2f} руб. – {e['description']}")
        lines.append(f"\n💰 Итого: {total:.2f} руб.")
        await update.message.reply_text("\n".join(lines))

    async def clear_expenses(self, update, context):
        if "expenses" in context.user_data:
            del context.user_data["expenses"]
        await update.message.reply_text("🧹 Все расходы очищены.")

    async def _get_total_extra(self, context):
        extras = context.user_data.get("extra_incomes", [])
        return sum(e["amount"] for e in extras)

    async def _get_total_expenses(self, context):
        expenses = context.user_data.get("expenses", [])
        return sum(e["amount"] for e in expenses)

    # ---------- Команда /calc ----------
    async def calc_command(self, update, context):
        if context.args:
            text = " ".join(context.args)
            extra = await self._get_total_extra(context)
            expenses = await self._get_total_expenses(context)
            acc = parse_numbers(text) + extra - expenses
            if acc <= 0:
                return await update.message.reply_text("❌ Итоговая сумма <= 0 (нет чисел или расходы превышают доход).")
            result = calculate(acc, self.percent)
            await update.message.reply_text(
                f"📊 Начислено: {result['accrued']:.2f} руб.\n"
                f"Удержание {result['percent']:.1f}%: {result['deductions']:.2f} руб.\n"
                f"💵 К выдаче: {result['net']:.2f} руб."
            )
        else:
            await update.message.reply_text("Введите текст с числами: /calc Лексус 1300")

    # ---------- Статистика и рейтинг ----------
    async def stats(self, update, context):
        if context.args:
            name = context.args[0]
            period = parse_period(" ".join(context.args[1:])) if len(context.args) > 1 else None
            if name in self.data.masters:
                return await self._show_stats_or_rating(update, context, "stats", master=name, period=period)
            else:
                return await self._show_stats_or_rating(update, context, "stats", master=None, period=name)
        keyboard = [[InlineKeyboardButton("📊 Все мастера", callback_data="stats_master_все")]]
        for m in self.data.masters:
            keyboard.append([InlineKeyboardButton(m, callback_data=f"stats_master_{m}")])
        await update.message.reply_text("Выберите мастера:", reply_markup=InlineKeyboardMarkup(add_back(keyboard)))

    async def rating(self, update, context):
        if context.args:
            period = parse_period(" ".join(context.args))
            if period:
                return await self._show_stats_or_rating(update, context, "rating", period=period)
        await update.message.reply_text("Выберите период:", reply_markup=period_kb("rating_period"))

    async def _show_stats_or_rating(self, update, context, mode, master=None, period=None, target=None):
        if isinstance(period, str) and period in ("день", "сегодня", "неделя", "месяц", "год", "все"):
            period_spec = None if period == "все" else period
        elif isinstance(period, tuple):
            period_spec = period
        else:
            period_spec = None

        filtered = filter_incomes(
            self.data.incomes,
            master_name=master if master != "все" else None,
            period=period_spec
        )

        if not filtered:
            msg = "Нет данных за выбранный период."
            if target:
                await target.edit_text(msg, reply_markup=InlineKeyboardMarkup(add_back([])))
            else:
                await update.message.reply_text(msg)
            return

        period_display = {
            "сегодня": "Сегодня",
            "неделя": "Неделя",
            "месяц": "Месяц",
            "год": "Год",
            "все": "Всю историю"
        }.get(period if isinstance(period, str) else "все", str(period) if period else "весь период")

        if mode == "stats":
            if master and master != "все":
                total = sum(inc["amount"] for inc in filtered)
                count = len(filtered)
                text = f"📊 Статистика мастера *{master}* за {period_display}\n💰 {total:.2f} руб.\n📦 {count} записей"
                if count:
                    text += f"\n📈 Средний чек: {total/count:.2f} руб."
            else:
                stats = {}
                for inc in filtered:
                    stats[inc["master"]] = stats.get(inc["master"], 0) + inc["amount"]
                total_all = sum(stats.values())
                text = f"📊 *Общая статистика за {period_display}*\n" + "\n".join(
                    f"• {m}: {s:.2f} руб." for m, s in stats.items()
                )
                text += f"\n\n🏷 *Итого: {total_all:.2f} руб.*"
        else:  # rating
            rating_dict = {}
            for inc in filtered:
                rating_dict[inc["master"]] = rating_dict.get(inc["master"], 0) + inc["amount"]
            sorted_rating = sorted(rating_dict.items(), key=lambda x: x[1], reverse=True)
            lines = [f"🏆 *Рейтинг мастеров за {period_display}*"]
            for i, (m, amount) in enumerate(sorted_rating, 1):
                medal = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else f"{i}."
                lines.append(f"{medal} {m} – {amount:.2f} руб.")
            text = "\n".join(lines)

        if target:
            await target.edit_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(add_back([])))
        else:
            await update.message.reply_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(add_back([])))

    # ---------- Callback ----------
    async def callback(self, update, context):
        query = update.callback_query
        data = query.data
        logger.info(f"Callback: {data}")
        try:
            await query.answer()

            if data == BACK:
                context.user_data.clear()
                await query.message.delete()
                await query.message.reply_text("Главное меню:", reply_markup=KB)
                return

            if data == "noop":
                return

            if data.startswith("percent_"):
                val = data.split("_")[1]
                if val == "custom":
                    await query.edit_message_text("Введите число от 0 до 100:")
                    context.user_data["wait_percent"] = True
                else:
                    p = float(val)
                    self.percent = p
                    self.data.settings["deduction_percent"] = p
                    self.data.save_all()
                    await query.edit_message_text(f"✅ Удержание установлено на {p:.1f}%")
                return

            if data.startswith("edit_master_delete_"):
                name = data.replace("edit_master_delete_", "")
                if name in self.data.masters:
                    self.data.masters.remove(name)
                    self.data.save_all()
                    await self._refresh_master_list(query)
                else:
                    await query.edit_message_text(f"Мастер {name} уже удалён.")
                return

            if data.startswith("edit_master_rename_"):
                name = data.replace("edit_master_rename_", "")
                context.user_data["rename_old"] = name
                await query.edit_message_text(f"Введите новое имя для «{name}»:")
                context.user_data["wait_rename"] = True
                return

            if data.startswith("stats_master_"):
                master = data.replace("stats_master_", "")
                context.user_data["stats_master"] = master
                await query.edit_message_text(
                    f"Мастер: {master if master != 'все' else 'Все'}\nВыберите период:",
                    reply_markup=period_kb("stats_period")
                )
                return

            if data.startswith("stats_period_"):
                period = data.replace("stats_period_", "")
                if period == "custom":
                    await query.edit_message_text("Введите даты ГГГГ-ММ-ДД ГГГГ-ММ-ДД:")
                    context.user_data["wait_custom_dates"] = True
                    return
                master = context.user_data.get("stats_master", "все")
                await self._show_stats_or_rating(update, context, "stats", master=master, period=period, target=query.message)
                return

            if data.startswith("rating_period_"):
                period = data.replace("rating_period_", "")
                if period == "custom":
                    await query.edit_message_text("Введите даты ГГГГ-ММ-ДД ГГГГ-ММ-ДД:")
                    context.user_data["wait_rating_dates"] = True
                    return
                await self._show_stats_or_rating(update, context, "rating", period=period, target=query.message)
                return

            if data.startswith("master_") or data == "skip_master":
                if data == "skip_master":
                    await query.edit_message_text("Доход не записан.")
                    return
                master = data[7:]
                result = context.user_data.get("last_result")
                text = context.user_data.get("last_text", "")
                if not result:
                    await query.edit_message_text("❌ Ошибка: результат не найден.")
                    return
                self.data.incomes.append({
                    "master": master,
                    "amount": result["net"],
                    "date": datetime.now().isoformat(),
                    "text": text
                })
                self.data.save_all()
                await query.edit_message_text(f"✅ Доход {result['net']:.2f} руб. для {master} записан.")
                return

            logger.warning(f"Неизвестный callback: {data}")
        except Exception as e:
            logger.error(f"Callback error {data}: {e}")
            try:
                await query.edit_message_text("Ошибка. Попробуйте снова.")
            except:
                pass

    async def _refresh_master_list(self, query):
        if not self.data.masters:
            await query.edit_message_text("Список мастеров пуст.")
            return
        keyboard = []
        for m in self.data.masters:
            keyboard.append([
                InlineKeyboardButton(m, callback_data="noop"),
                InlineKeyboardButton("✏️", callback_data=f"edit_master_rename_{m}"),
                InlineKeyboardButton("❌", callback_data=f"edit_master_delete_{m}")
            ])
        keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data=BACK)])
        await query.edit_message_text("📋 Список мастеров:", reply_markup=InlineKeyboardMarkup(keyboard))

    # ---------- Обработка сообщений ----------
    async def handle_message(self, update, context):
        try:
            if not update or not update.message:
                return

            uid = update.effective_user.id
            text = get_message_text(update)
            if not text:
                await update.message.reply_text("Я не вижу текста.")
                return

            # Кнопки
            button_map = {
                "➕ Добавить мастера": self.add_master,
                "📋 Список мастеров": self.list_masters,
                "➕ Добавить доход": self.add_income_button,
                "💸 Расходы": self.add_expense_button,
                "📊 Статистика": self.stats,
                "🏆 Рейтинг": self.rating,
                "🧮 Простой расчёт": self.simple_calc,
                "📤 Экспорт CSV": self.export_csv,
                "⚙️ Процент удержания": self.set_percent,
                "❓ Помощь": self.help
            }
            if text in button_map:
                await button_map[text](update, context)
                return

            # Ожидания
            if context.user_data.get("wait_calc_only"):
                extra = await self._get_total_extra(context)
                expenses = await self._get_total_expenses(context)
                acc = parse_numbers(text) + extra - expenses
                if acc <= 0:
                    await update.message.reply_text("❌ Итоговая сумма <= 0 (нет чисел или расходы превышают доход).")
                else:
                    result = calculate(acc, self.percent)
                    await update.message.reply_text(
                        f"📊 Начислено: {result['accrued']:.2f} руб.\n"
                        f"Удержание {result['percent']:.1f}%: {result['deductions']:.2f} руб.\n"
                        f"💵 К выдаче: {result['net']:.2f} руб.\n\n✅ Расчёт выполнен (без сохранения)."
                    )
                context.user_data["wait_calc_only"] = False
                return

            if context.user_data.get("wait_extra_income"):
                parts = text.split(maxsplit=1)
                if len(parts) < 2:
                    await update.message.reply_text("Введите сумму и описание через пробел, например:\n5000 Премия")
                    return
                try:
                    amount = float(parts[0])
                except ValueError:
                    await update.message.reply_text("❌ Сумма должна быть числом.")
                    return
                if amount <= 0:
                    await update.message.reply_text("❌ Сумма должна быть положительной.")
                    return
                description = parts[1]
                if "extra_incomes" not in context.user_data:
                    context.user_data["extra_incomes"] = []
                context.user_data["extra_incomes"].append({"amount": amount, "description": description})
                context.user_data["wait_extra_income"] = False
                await update.message.reply_text(f"✅ Добавлен дополнительный доход: {amount:.2f} руб. ({description})")
                return

            if context.user_data.get("wait_expense"):
                parts = text.split(maxsplit=1)
                if len(parts) < 2:
                    await update.message.reply_text("Введите сумму и описание через пробел, например:\n1500 Материалы")
                    return
                try:
                    amount = float(parts[0])
                except ValueError:
                    await update.message.reply_text("❌ Сумма должна быть числом.")
                    return
                if amount <= 0:
                    await update.message.reply_text("❌ Сумма должна быть положительной.")
                    return
                description = parts[1]
                if "expenses" not in context.user_data:
                    context.user_data["expenses"] = []
                context.user_data["expenses"].append({"amount": amount, "description": description})
                context.user_data["wait_expense"] = False
                await update.message.reply_text(f"✅ Добавлен расход: {amount:.2f} руб. ({description})")
                return

            if context.user_data.get("wait_master"):
                name = text.strip()
                if not name:
                    await update.message.reply_text("Имя не может быть пустым.")
                    return
                if name in self.data.masters:
                    await update.message.reply_text(f"Мастер {name} уже есть.")
                else:
                    self.data.masters.append(name)
                    self.data.save_all()
                    await update.message.reply_text(f"✅ Мастер {name} добавлен.")
                context.user_data["wait_master"] = False
                return

            if context.user_data.get("wait_rename"):
                old = context.user_data.get("rename_old")
                new = text.strip()
                if not new:
                    await update.message.reply_text("Имя не может быть пустым.")
                    return
                if new in self.data.masters and new != old:
                    await update.message.reply_text(f"Мастер {new} уже существует.")
                    context.user_data.pop("wait_rename", None)
                    context.user_data.pop("rename_old", None)
                    return
                idx = self.data.masters.index(old)
                self.data.masters[idx] = new
                for inc in self.data.incomes:
                    if inc["master"] == old:
                        inc["master"] = new
                for uid2, m in self.data.users.items():
                    if m == old:
                        self.data.users[uid2] = new
                self.data.save_all()
                context.user_data.pop("wait_rename", None)
                context.user_data.pop("rename_old", None)
                await update.message.reply_text(f"✅ Переименован: {old} → {new}")
                await self.list_masters(update, context)
                return

            if context.user_data.get("wait_percent"):
                await self._handle_percent_input(update, context)
                return

            if context.user_data.get("wait_custom_dates") or context.user_data.get("wait_rating_dates"):
                parts = text.split()
                if len(parts) == 2:
                    try:
                        d1 = datetime.strptime(parts[0], "%Y-%m-%d")
                        d2 = datetime.strptime(parts[1], "%Y-%m-%d")
                        period = (d1, d2) if d1 <= d2 else (d2, d1)
                        if context.user_data.get("wait_custom_dates"):
                            master = context.user_data.get("stats_master", "все")
                            context.user_data["wait_custom_dates"] = False
                            await self._show_stats_or_rating(update, context, "stats", master=master, period=period)
                        else:
                            context.user_data["wait_rating_dates"] = False
                            await self._show_stats_or_rating(update, context, "rating", period=period)
                        return
                    except Exception as e:
                        logger.error(f"Ошибка парсинга дат: {e}")
                        await update.message.reply_text("Ошибка формата. Введите даты как ГГГГ-ММ-ДД ГГГГ-ММ-ДД")
                else:
                    await update.message.reply_text("Введите две даты через пробел.")
                return

            # Основной расчёт
            extra = await self._get_total_extra(context)
            expenses = await self._get_total_expenses(context)
            acc = parse_numbers(text) + extra - expenses
            if acc <= 0:
                await update.message.reply_text("❌ Итоговая сумма <= 0 (нет чисел или расходы превышают доход).")
                return

            result = calculate(acc, self.percent)
            master = self.data.users.get(str(uid))
            if master:
                self.data.incomes.append({
                    "master": master,
                    "amount": result["net"],
                    "date": datetime.now().isoformat(),
                    "text": text + (f" (доп. доходы: {extra:.2f}, расходы: {expenses:.2f})" if extra or expenses else "")
                })
                self.data.save_all()
                await update.message.reply_text(
                    f"📊 Начислено: {result['accrued']:.2f} руб.\n"
                    f"Удержание {result['percent']:.1f}%: {result['deductions']:.2f} руб.\n"
                    f"💵 К выдаче: {result['net']:.2f} руб.\n\n✅ Доход записан на мастера {master}."
                )
            else:
                context.user_data["last_result"] = result
                context.user_data["last_text"] = text + (f" (доп. доходы: {extra:.2f}, расходы: {expenses:.2f})" if extra or expenses else "")
                keyboard = []
                if self.data.masters:
                    row = []
                    for m in self.data.masters:
                        row.append(InlineKeyboardButton(m, callback_data=f"master_{m}"))
                        if len(row) == 2:
                            keyboard.append(row)
                            row = []
                    if row:
                        keyboard.append(row)
                keyboard.append([InlineKeyboardButton("❌ Не записывать", callback_data="skip_master")])
                await update.message.reply_text(
                    f"📊 Начислено: {result['accrued']:.2f} руб.\n"
                    f"Удержание {result['percent']:.1f}%: {result['deductions']:.2f} руб.\n"
                    f"💵 К выдаче: {result['net']:.2f} руб.\n\n"
                    "Вы не зарегистрированы. Выберите мастера для записи дохода:",
                    reply_markup=InlineKeyboardMarkup(add_back(keyboard))
                )

        except Exception as e:
            logger.error(f"Критическая ошибка в handle_message: {e}", exc_info=True)
            await update.message.reply_text("❌ Произошла ошибка. Попробуйте позже.")

    # ---------- Обработчики кнопок ----------
    async def add_income_button(self, update, context):
        await update.message.reply_text("Введите сумму и описание через пробел, например:\n5000 Премия")
        context.user_data["wait_extra_income"] = True

    async def add_expense_button(self, update, context):
        await update.message.reply_text("Введите сумму и описание через пробел, например:\n1500 Материалы")
        context.user_data["wait_expense"] = True

    async def simple_calc(self, update, context):
        await update.message.reply_text("Введите список работ с числами для расчёта (без сохранения):")
        context.user_data["wait_calc_only"] = True

    # ---------- Запуск ----------
    def run(self):
        logger.info("Бот запущен")
        self.app.run_polling()

if __name__ == "__main__":
    token = os.environ.get("BOT_TOKEN")
    if not token:
        try:
            token = input("Введите токен: ").strip()
        except EOFError:
            print("Установите переменную окружения BOT_TOKEN")
            exit()
    if not token:
        print("Токен не введён.")
        exit()
    bot = SalaryBot(token)
    bot.run()
