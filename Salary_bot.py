import re, json, os, csv, io, logging
from datetime import datetime, timedelta
from collections import defaultdict
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ---------- Конфигурация ----------
F = ("bot_settings.json", "masters.json", "incomes.json", "users.json", "branches.json")
DEFAULT_PERCENT = 70.0
BACK = "back_to_main"

# ---------- Клавиатуры ----------
MAIN_KB = ReplyKeyboardMarkup([
    ["👥 Мастера", "🏢 Филиалы"],
    ["📊 Отчёты", "🛠 Инструменты"],
    ["⚙️ Настройки"]
], resize_keyboard=True)

SUBMENUS = {
    "👥 Мастера": [("➕ Добавить мастера", "add_master"), ("📋 Список мастеров", "list_masters")],
    "🏢 Филиалы": [("➕ Добавить филиал", "add_branch"), ("📋 Список филиалов", "list_branches"), ("❌ Удалить филиал", "remove_branch")],
    "📊 Отчёты": [("📊 Статистика по филиалам", "stats_branch"), ("🏆 Рейтинг мастеров", "rating")],
    "🛠 Инструменты": [("🧮 Простой расчёт", "simple_calc"), ("📤 Экспорт CSV", "export")],
    "⚙️ Настройки": [("⚙️ Процент удержания", "percent"), ("❓ Помощь", "help")]
}

def add_back(k):
    k.append([InlineKeyboardButton("🔙 Назад", callback_data=BACK)])
    return k

def period_kb(prefix):
    return InlineKeyboardMarkup(add_back([
        [InlineKeyboardButton("📅 Сегодня", callback_data=f"{prefix}_сегодня"),
         InlineKeyboardButton("📅 Неделя", callback_data=f"{prefix}_неделя")],
        [InlineKeyboardButton("📅 Месяц", callback_data=f"{prefix}_месяц"),
         InlineKeyboardButton("📅 Год", callback_data=f"{prefix}_год")],
        [InlineKeyboardButton("📅 Вся история", callback_data=f"{prefix}_все"),
         InlineKeyboardButton("📅 Произвольный диапазон", callback_data=f"{prefix}_custom")]
    ]))

# ---------- Менеджер данных ----------
class DataManager:
    def __init__(self):
        self.settings = self._load(F[0], {"deduction_percent": DEFAULT_PERCENT})
        self.masters = self._load(F[1], [])
        if self.masters and isinstance(self.masters[0], str):
            self.masters = [{"name": m, "branch": "Основной"} for m in self.masters]
            self._save(F[1], self.masters)
        self.incomes = self._load(F[2], [])
        self.users = self._load(F[3], {})
        self.branches = self._load(F[4], [])
        if not self.branches:
            for m in self.masters:
                if m["branch"] not in self.branches:
                    self.branches.append(m["branch"])
            self._save(F[4], self.branches)

    def _load(self, file, default):
        if os.path.exists(file):
            try:
                data = json.load(open(file, 'r', encoding='utf-8'))
                if isinstance(data, dict if file in (F[0], F[3]) else list):
                    return data
            except:
                pass
        return default

    def _save(self, file, data):
        if file == F[1]:  # masters
            data = [{"name": str(item.get("name", item)).strip(), "branch": str(item.get("branch", "Основной")).strip()} for item in data]
        elif file == F[4]:  # branches
            data = [str(b).strip() for b in data if str(b).strip()]
        elif file == F[0]:  # settings
            if "deduction_percent" in data:
                data["deduction_percent"] = float(data.get("deduction_percent", DEFAULT_PERCENT))
        elif file == F[3]:  # users
            data = {k: v for k, v in data.items() if v and str(v).strip()}
        elif file == F[2]:  # incomes
            for inc in data:
                inc["master"] = str(inc.get("master", ""))
                inc["amount"] = float(inc.get("amount", 0.0))
                inc["branch"] = inc.get("branch") or self.get_branch(inc["master"])
        with open(file, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    def save_all(self):
        for f in F: self._save(f, getattr(self, f.replace(".json", "").replace("_settings", "settings").replace("_masters", "masters").replace("_incomes", "incomes").replace("_users", "users").replace("_branches", "branches")))

    def get_branch(self, master_name):
        for m in self.masters:
            if m["name"] == master_name: return m["branch"]
        return "Основной"

    def get_masters_by_branch(self, branch):
        return [m["name"] for m in self.masters if m["branch"] == branch]

    def get_all_master_names(self):
        return [m["name"] for m in self.masters]

# ---------- Парсинг и утилиты ----------
def parse_numbers(text):
    if not text: return 0.0
    cleaned = re.sub(r'(?<=\d)\s+(?=\d)', '', text)
    cleaned = re.sub(r'(?<=\d),(?=\d)', lambda m: '.' if len(re.search(r'\d+', cleaned[m.end():]).group()) <= 2 else ',', cleaned)
    return sum(float(n) for n in re.findall(r'-?\d+(?:\.\d+)?', cleaned) if n)

def get_message_text(update):
    msg = update.effective_message
    return msg.text or msg.caption or "" if msg else ""

def calculate(accrued, percent):
    percent = float(percent) if percent else DEFAULT_PERCENT
    deduction = accrued * (percent / 100.0)
    return {"accrued": accrued, "deductions": deduction, "net": accrued - deduction, "percent": percent}

def filter_incomes(incomes, master_name=None, branch=None, period=None):
    start_date, end_date = None, None
    if period:
        now = datetime.now()
        if period in ("день", "сегодня"):
            start_date, end_date = datetime(now.year, now.month, now.day), start_date + timedelta(days=1) - timedelta(seconds=1)
        elif period == "неделя": start_date = now - timedelta(days=7)
        elif period == "месяц": start_date = now - timedelta(days=30)
        elif period == "год": start_date = now - timedelta(days=365)
        elif isinstance(period, tuple) and len(period) == 2:
            start_date, end_date = period
    return [inc for inc in incomes
            if (not master_name or inc.get("master") == master_name)
            and (not branch or inc.get("branch") == branch)
            and (not start_date or (start_date <= datetime.fromisoformat(inc["date"]) and (not end_date or datetime.fromisoformat(inc["date"]) <= end_date)))]

def parse_period(arg):
    if arg in ("день", "сегодня", "неделя", "месяц", "год", "все"): return arg
    parts = arg.split()
    if len(parts) == 2:
        try:
            d1, d2 = datetime.strptime(parts[0], "%Y-%m-%d"), datetime.strptime(parts[1], "%Y-%m-%d")
            return (d1, d2) if d1 <= d2 else (d2, d1)
        except: pass
    return None

# ---------- Основной бот ----------
class SalaryBot:
    def __init__(self, token):
        self.data = DataManager()
        self.percent = float(self.data.settings.get("deduction_percent", DEFAULT_PERCENT))
        self.app = Application.builder().token(token).build()
        self._register_handlers()

    def _register_handlers(self):
        cmds = [("start", self.start), ("help", self.help), ("add_master", self.add_master),
                ("remove_master", self.remove_master), ("masters", self.list_masters),
                ("incomes", self.list_incomes), ("edit_income", self.edit_income),
                ("export", self.export_csv), ("percent", self.set_percent),
                ("stats", self.stats_branch_command), ("rating", self.rating),
                ("register", self.register_user), ("unregister", self.unregister_user),
                ("calc", self.calc_command), ("add_income", self.add_income_command),
                ("extra", self.show_extra), ("clear_extra", self.clear_extra),
                ("add_expense", self.add_expense_command), ("expenses", self.show_expenses),
                ("clear_expenses", self.clear_expenses), ("add_branch", self.add_branch),
                ("branches", self.list_branches), ("remove_branch", self.remove_branch),
                ("stats_branch", self.stats_branch_command)]
        for cmd, handler in cmds:
            self.app.add_handler(CommandHandler(cmd, handler))
        self.app.add_handler(MessageHandler(filters.COMMAND, self.unknown_command))
        self.app.add_handler(CallbackQueryHandler(self.callback, pattern="^(add_master|list_masters|add_income|add_expense|stats_branch|rating|simple_calc|export|percent|help|add_branch|list_branches|remove_branch|back_to_main|master_|skip_master|edit_master_|noop|percent_70|percent_60|percent_50|percent_40|percent_custom|branch_|stats_branch_period_|stats_branch_detail_)"))
        self.app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_message))
        self.app.add_error_handler(self.error_handler)

    async def error_handler(self, update, context):
        logger.error(f"Ошибка: {context.error}")
        if update and update.effective_message:
            await update.effective_message.reply_text("❌ Внутренняя ошибка. Попробуйте позже.")

    async def unknown_command(self, update, context):
        await update.effective_message.reply_text("❌ Неизвестная команда. Используйте /help.", reply_markup=MAIN_KB)

    async def start(self, update, context):
        await update.effective_message.reply_text(
            "🌟 *Добро пожаловать в ArtRegardFinance!*\n\n"
            "Я — ваш умный помощник для расчёта зарплаты.\n\n"
            "📌 *Что я умею:*\n"
            "• Считать зарплату по списку работ (автоматически нахожу все числа)\n"
            "• Учитывать дополнительные доходы и расходы\n"
            "• Вести статистику по мастерам и филиалам\n"
            "• Показывать рейтинг и аналитику\n\n"
            "📌 *Как начать?*\n"
            "Просто отправьте мне текст с числами (например, \"Лексус 1300\") — я сам найду все суммы и рассчитаю итог с учётом удержания.\n\n"
            "Используйте кнопки меню ниже для управления мастерами, филиалами, отчётами и настройками.\n\n"
            "💡 *Подсказка:* все дополнительные доходы и расходы можно добавлять через соответствующие разделы.",
            reply_markup=MAIN_KB, parse_mode="Markdown")

    async def help(self, update, context):
        await update.effective_message.reply_text(
            "📖 Справка:\n"
            "Используйте кнопки меню для навигации.\n\n"
            "👥 Мастера – управление мастерами (добавление, список, редактирование)\n"
            "🏢 Филиалы – управление филиалами\n"
            "📊 Отчёты – статистика по филиалам и рейтинг мастеров\n"
            "🛠 Инструменты – простой расчёт, экспорт CSV\n"
            "⚙️ Настройки – процент удержания, помощь\n\n"
            "Также доступны команды:\n"
            "/add_master <имя> [филиал] – добавить мастера\n"
            "/masters – список мастеров\n"
            "/add_branch <название> – добавить филиал\n"
            "/branches – список филиалов\n"
            "/stats_branch <филиал> [период] – статистика по филиалу\n"
            "/rating [период] – рейтинг мастеров\n"
            "/calc <текст> – быстрый расчёт без сохранения\n"
            "/add_income <сумма> <описание> – добавить доход\n"
            "/add_expense <сумма> <описание> – добавить расход\n"
            "/register <имя> – привязать себя к мастеру\n"
            "/unregister – отвязаться",
            reply_markup=MAIN_KB)

    # ---------- Вспомогательные методы ----------
    async def _reply(self, update, text, **kwargs):
        await update.effective_message.reply_text(text, **kwargs)

    async def _clear_state(self, context, *keys):
        for key in keys:
            context.user_data.pop(key, None)

    # ---------- Регистрация ----------
    async def register_user(self, update, context):
        if not context.args:
            return await self._reply(update, "Укажите имя: /register Иван")
        name = " ".join(context.args).strip()
        if not name: return await self._reply(update, "Имя не может быть пустым.")
        if name not in self.data.get_all_master_names():
            return await self._reply(update, f"Мастер '{name}' не существует.")
        if any(m == name and int(uid) != update.effective_user.id for uid, m in self.data.users.items()):
            return await self._reply(update, f"Имя '{name}' уже занято.")
        self.data.users[str(update.effective_user.id)] = name
        self.data.save_all()
        await self._reply(update, f"✅ Вы зарегистрированы как '{name}'.")

    async def unregister_user(self, update, context):
        uid = str(update.effective_user.id)
        if uid in self.data.users:
            del self.data.users[uid]
            self.data.save_all()
            await self._reply(update, "✅ Вы отвязаны.")
        else:
            await self._reply(update, "Вы не зарегистрированы.")

    # ---------- Филиалы ----------
    async def add_branch(self, update, context):
        if context.args:
            name = " ".join(context.args).strip()
            if not name: return await self._reply(update, "Название не может быть пустым.")
            if name in self.data.branches: return await self._reply(update, f"Филиал '{name}' уже существует.")
            self.data.branches.append(name)
            self.data.save_all()
            await self._reply(update, f"✅ Филиал '{name}' добавлен.")
        else:
            await self._reply(update, "Введите название нового филиала:")
            context.user_data["wait_branch_name"] = True

    async def list_branches(self, update, context):
        if not self.data.branches: return await self._reply(update, "Список филиалов пуст.")
        text = "🏢 Филиалы:\n" + "\n".join(f"• {b}" for b in self.data.branches)
        await self._reply(update, text)

    async def remove_branch(self, update, context):
        if not context.args: return await self._reply(update, "Укажите название филиала: /remove_branch Название")
        name = " ".join(context.args).strip()
        if name not in self.data.branches: return await self._reply(update, f"Филиал '{name}' не найден.")
        masters_in_branch = self.data.get_masters_by_branch(name)
        if masters_in_branch:
            return await self._reply(update, f"Невозможно удалить филиал '{name}', так как в нём есть мастера: {', '.join(masters_in_branch)}.\nСначала переназначьте или удалите мастеров.")
        self.data.branches.remove(name)
        self.data.save_all()
        await self._reply(update, f"✅ Филиал '{name}' удалён.")

    # ---------- Мастера ----------
    async def add_master(self, update, context):
        if context.args:
            parts = " ".join(context.args).strip().split()
            name, branch = parts[0], parts[1] if len(parts) > 1 else None
            if not name: return await self._reply(update, "Имя не может быть пустым.")
            if name in self.data.get_all_master_names(): return await self._reply(update, f"Мастер {name} уже существует.")
            if branch and branch not in self.data.branches:
                return await self._reply(update, f"Филиал '{branch}' не существует. Создайте его через /add_branch.")
            branch = branch or (self.data.branches[0] if self.data.branches else "Основной")
            if branch not in self.data.branches: self.data.branches.append(branch)
            self.data.masters.append({"name": name, "branch": branch})
            self.data.save_all()
            await self._reply(update, f"✅ Мастер {name} добавлен в филиал {branch}.")
        else:
            await self._reply(update, "Введите имя нового мастера:")
            context.user_data["wait_master_name"] = True

    async def _process_add_master_name(self, update, context, name):
        if not name: return await self._reply(update, "Имя не может быть пустым.")
        if name in self.data.get_all_master_names(): return await self._reply(update, f"Мастер {name} уже существует.")
        context.user_data["new_master_name"] = name
        if not self.data.branches:
            self.data.branches.append("Основной")
            self.data.save_all()
        keyboard = [[InlineKeyboardButton(b, callback_data=f"branch_{b}")] for b in self.data.branches]
        keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="back_to_main")])
        await self._reply(update, f"Выберите филиал для мастера {name}:", reply_markup=InlineKeyboardMarkup(keyboard))
        context.user_data["wait_branch_selection"] = True

    async def remove_master(self, update, context):
        name = " ".join(context.args).strip()
        if not name: return await self._reply(update, "Укажите имя: /remove_master Иван")
        if name not in self.data.get_all_master_names(): return await self._reply(update, f"Мастер {name} не найден.")
        self.data.masters = [m for m in self.data.masters if m["name"] != name]
        self.data.save_all()
        await self._reply(update, f"✅ Мастер {name} удалён.")

    async def list_masters(self, update, context):
        if not self.data.masters: return await self._reply(update, "Список мастеров пуст.")
        text = "📋 Мастера:\n" + "\n".join(f"• {m['name']} (филиал: {m['branch']})" for m in self.data.masters)
        await self._reply(update, text)

    # ---------- Доходы ----------
    async def list_incomes(self, update, context):
        if not self.data.incomes: return await self._reply(update, "Нет записей.")
        lines = ["📋 Доходы:"]
        for i, inc in enumerate(self.data.incomes, 1):
            lines.append(f"{i}. {inc['master']} – {inc['amount']:.2f} ({inc['date'][:10]})")
        for chunk in [lines[i:i+20] for i in range(0, len(lines), 20)]:
            await self._reply(update, "\n".join(chunk))

    async def edit_income(self, update, context):
        if len(context.args) < 2: return await self._reply(update, "/edit_income <номер> <сумма>")
        try:
            idx = int(context.args[0]) - 1
            if idx < 0 or idx >= len(self.data.incomes): return await self._reply(update, "Некорректный номер.")
            new_amount = float(context.args[1])
            if new_amount < 0: return await self._reply(update, "Сумма не может быть отрицательной.")
            self.data.incomes[idx]["amount"] = new_amount
            self.data.save_all()
            await self._reply(update, f"✅ Доход №{idx+1} обновлён: {new_amount:.2f}")
        except ValueError:
            await self._reply(update, "Ошибка ввода. Введите числа.")

    async def export_csv(self, update, context):
        if not self.data.incomes: return await self._reply(update, "Нет данных.")
        output = io.StringIO()
        writer = csv.writer(output, delimiter=';')
        writer.writerow(["Мастер", "Филиал", "Сумма", "Дата", "Текст"])
        for inc in self.data.incomes:
            writer.writerow([inc["master"], inc.get("branch", ""), inc["amount"], inc["date"], inc.get("text", "")])
        output.seek(0)
        await update.effective_message.reply_document(
            document=output.getvalue().encode('utf-8-sig'),
            filename="incomes.csv",
            caption="📊 Экспорт доходов"
        )

    # ---------- Процент ----------
    async def set_percent(self, update, context):
        keyboard = add_back([
            [InlineKeyboardButton("70%", callback_data="percent_70"), InlineKeyboardButton("60%", callback_data="percent_60")],
            [InlineKeyboardButton("50%", callback_data="percent_50"), InlineKeyboardButton("40%", callback_data="percent_40")],
            [InlineKeyboardButton("Своё...", callback_data="percent_custom")]
        ])
        await self._reply(update, "Выберите процент:", reply_markup=InlineKeyboardMarkup(keyboard))

    async def _handle_percent_input(self, update, context):
        try:
            new_percent = float(update.effective_message.text.strip())
            if 0 <= new_percent <= 100:
                self.percent = new_percent
                self.data.settings["deduction_percent"] = new_percent
                self.data.save_all()
                await self._reply(update, f"✅ Удержание установлено на {new_percent:.1f}%")
            else:
                await self._reply(update, "❌ Процент должен быть от 0 до 100.")
        except ValueError:
            await self._reply(update, "❌ Введите число.")
        context.user_data.pop("wait_percent", None)

    # ---------- Универсальный метод для добавления доходов/расходов ----------
    async def _add_extra(self, update, context, key, label):
        if len(context.args) < 2:
            return await self._reply(update, f"Использование: /{key} <сумма> <описание>\nПример: /{key} 5000 Премия")
        try:
            amount = float(context.args[0])
            if amount <= 0: return await self._reply(update, "❌ Сумма должна быть положительной.")
            description = " ".join(context.args[1:])
            context.user_data.setdefault(key, []).append({"amount": amount, "description": description})
            await self._reply(update, f"✅ Добавлен {label}: {amount:.2f} руб. ({description})")
        except ValueError:
            await self._reply(update, "❌ Сумма должна быть числом.")

    async def add_income_command(self, update, context):
        await self._add_extra(update, context, "extra_incomes", "дополнительный доход")

    async def add_expense_command(self, update, context):
        await self._add_extra(update, context, "expenses", "расход")

    async def _show_list(self, update, context, key, label, total_label):
        items = context.user_data.get(key, [])
        if not items:
            return await self._reply(update, f"Нет добавленных {label}.")
        total = sum(item["amount"] for item in items)
        lines = [f"📋 {label.capitalize()}:"]
        for i, item in enumerate(items, 1):
            lines.append(f"{i}. {item['amount']:.2f} руб. – {item['description']}")
        lines.append(f"\n💰 {total_label}: {total:.2f} руб.")
        await self._reply(update, "\n".join(lines))

    async def show_extra(self, update, context):
        await self._show_list(update, context, "extra_incomes", "добавленные доходы", "Итого")

    async def show_expenses(self, update, context):
        await self._show_list(update, context, "expenses", "расходы", "Итого")

    async def clear_extra(self, update, context):
        context.user_data.pop("extra_incomes", None)
        await self._reply(update, "🧹 Все добавленные доходы очищены.")

    async def clear_expenses(self, update, context):
        context.user_data.pop("expenses", None)
        await self._reply(update, "🧹 Все расходы очищены.")

    async def _get_total_extra(self, context):
        return sum(item["amount"] for item in context.user_data.get("extra_incomes", []))

    async def _get_total_expenses(self, context):
        return sum(item["amount"] for item in context.user_data.get("expenses", []))

    # ---------- Команда /calc ----------
    async def calc_command(self, update, context):
        if not context.args:
            return await self._reply(update, "Введите текст с числами: /calc Лексус 1300")
        text = " ".join(context.args)
        acc = parse_numbers(text) + await self._get_total_extra(context) - await self._get_total_expenses(context)
        if acc <= 0:
            return await self._reply(update, "❌ Итоговая сумма <= 0.")
        result = calculate(acc, self.percent)
        await self._reply(update, f"📊 Начислено: {result['accrued']:.2f} руб.\nУдержание {result['percent']:.1f}%: {result['deductions']:.2f} руб.\n💵 К выдаче: {result['net']:.2f} руб.")

    # ---------- Статистика по филиалам ----------
    async def stats_branch_command(self, update, context):
        if context.args:
            branch = context.args[0]
            period = parse_period(" ".join(context.args[1:])) if len(context.args) > 1 else None
            if branch not in self.data.branches:
                return await self._reply(update, f"Филиал '{branch}' не найден.")
            await self._show_branch_stats(update, context, branch, period)
        else:
            keyboard = [[InlineKeyboardButton(b, callback_data=f"branch_{b}")] for b in self.data.branches]
            keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="back_to_main")])
            await self._reply(update, "Выберите филиал для статистики:", reply_markup=InlineKeyboardMarkup(keyboard))
            context.user_data["stats_branch_mode"] = True

    async def _show_branch_stats(self, update, context, branch, period=None, target=None):
        filtered = filter_incomes(self.data.incomes, branch=branch, period=period)
        if not filtered:
            msg = f"Нет данных по филиалу '{branch}' за выбранный период."
            return await (target.edit_text(msg, reply_markup=InlineKeyboardMarkup(add_back([]))) if target else self._reply(update, msg))
        total = sum(inc["amount"] for inc in filtered)
        count = len(filtered)
        avg = total / count if count else 0
        period_display = {"сегодня":"Сегодня","неделя":"Неделя","месяц":"Месяц","год":"Год","все":"Всю историю"}.get(period if isinstance(period,str) else "все", str(period) if period else "весь период")
        masters_stats = defaultdict(float)
        for inc in filtered:
            masters_stats[inc["master"]] += inc["amount"]
        sorted_masters = sorted(masters_stats.items(), key=lambda x: x[1], reverse=True)
        text = f"📊 *Статистика филиала '{branch}'*\n📅 Период: {period_display}\n💰 Общая сумма: {total:.2f} руб.\n📦 Количество операций: {count}\n📈 Средний чек: {avg:.2f} руб.\n\n🏆 *Топ мастеров:*\n"
        for i, (m, amount) in enumerate(sorted_masters[:10], 1):
            medal = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else f"{i}."
            text += f"{medal} {m} – {amount:.2f} руб.\n"
        if len(sorted_masters) > 10:
            text += f"... и ещё {len(sorted_masters)-10} мастеров."
        keyboard = add_back([[InlineKeyboardButton("📅 Детализация по дням", callback_data=f"stats_branch_detail_days_{branch}_{period}")]])
        if target:
            await target.edit_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
        else:
            await self._reply(update, text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

    # ---------- Рейтинг ----------
    async def rating(self, update, context):
        if context.args:
            period = parse_period(" ".join(context.args))
            if period:
                return await self._show_rating(update, context, period)
        await self._reply(update, "Выберите период:", reply_markup=period_kb("rating_period"))

    async def _show_rating(self, update, context, period=None, target=None):
        filtered = filter_incomes(self.data.incomes, period=period)
        if not filtered:
            msg = "Нет данных за выбранный период."
            return await (target.edit_text(msg, reply_markup=InlineKeyboardMarkup(add_back([]))) if target else self._reply(update, msg))
        rating_dict = defaultdict(float)
        for inc in filtered:
            rating_dict[inc["master"]] += inc["amount"]
        sorted_rating = sorted(rating_dict.items(), key=lambda x: x[1], reverse=True)
        period_display = {"сегодня":"Сегодня","неделя":"Неделя","месяц":"Месяц","год":"Год","все":"Всю историю"}.get(period if isinstance(period,str) else "все", str(period) if period else "весь период")
        text = f"🏆 *Рейтинг мастеров за {period_display}*\n"
        for i, (m, amount) in enumerate(sorted_rating[:20], 1):
            medal = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else f"{i}."
            text += f"{medal} {m} – {amount:.2f} руб.\n"
        if len(sorted_rating) > 20:
            text += f"... и ещё {len(sorted_rating)-20} мастеров."
        if target:
            await target.edit_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(add_back([])))
        else:
            await self._reply(update, text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(add_back([])))

    # ---------- Подменю ----------
    async def show_submenu(self, update, context, category):
        items = SUBMENUS.get(category, [])
        keyboard = [[InlineKeyboardButton(text, callback_data=cb)] for text, cb in items]
        keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="back_to_main")])
        await self._reply(update, f"📂 *{category}* — выберите действие:", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

    # ---------- Callback ----------
    async def callback(self, update, context):
        query = update.callback_query
        data = query.data
        logger.info(f"Callback: {data}")
        try:
            await query.answer()
            if data == "back_to_main":
                context.user_data.clear()
                await query.message.delete()
                await query.message.reply_text("Главное меню:", reply_markup=MAIN_KB)
                return
            if data == "noop": return

            # Словарь обработчиков для быстрых действий
            handlers = {
                "add_master": self.add_master,
                "list_masters": self.list_masters,
                "add_branch": self.add_branch,
                "list_branches": self.list_branches,
                "remove_branch": self.remove_branch,
                "stats_branch": self.stats_branch_command,
                "rating": self.rating,
                "export": self.export_csv,
                "percent": self.set_percent,
                "help": self.help,
                "simple_calc": lambda u,c: (query.message.reply_text("Введите список работ с числами для расчёта (без сохранения):") or context.user_data.__setitem__("wait_calc_only", True)),
                "add_income": lambda u,c: (query.message.reply_text("Введите сумму и описание через пробел, например:\n5000 Премия") or context.user_data.__setitem__("wait_extra_income", True)),
                "add_expense": lambda u,c: (query.message.reply_text("Введите сумму и описание через пробел, например:\n1500 Материалы") or context.user_data.__setitem__("wait_expense", True))
            }
            if data in handlers:
                await handlers[data](update, context)
                return

            # Обработка branch_ (выбор филиала при добавлении мастера или статистики)
            if data.startswith("branch_"):
                branch = data.split("_", 1)[1]
                if branch in self.data.branches:
                    if context.user_data.get("wait_branch_selection"):
                        name = context.user_data.get("new_master_name")
                        if name:
                            self.data.masters.append({"name": name, "branch": branch})
                            self.data.save_all()
                            await query.edit_message_text(f"✅ Мастер {name} добавлен в филиал {branch}.")
                            self._clear_state(context, "new_master_name", "wait_branch_selection")
                            return
                    elif context.user_data.get("stats_branch_mode"):
                        context.user_data["stats_branch"] = branch
                        await query.edit_message_text(f"Филиал: {branch}\nВыберите период:", reply_markup=period_kb("stats_branch_period"))
                        return
                await query.edit_message_text("Филиал не найден.")
                return

            # Периоды для статистики по филиалу
            if data.startswith("stats_branch_period_"):
                period = data.split("_", 2)[2] if len(data.split("_")) > 2 else ""
                if period == "custom":
                    await query.edit_message_text("Введите даты ГГГГ-ММ-ДД ГГГГ-ММ-ДД:")
                    context.user_data["wait_custom_dates"] = True
                    return
                branch = context.user_data.get("stats_branch")
                if branch:
                    await self._show_branch_stats(update, context, branch, period, target=query.message)
                else:
                    await query.edit_message_text("Ошибка: филиал не выбран.")
                return

            # Детализация по дням
            if data.startswith("stats_branch_detail_days_"):
                parts = data.split("_", 3)
                if len(parts) >= 3:
                    branch = parts[2]
                    period = parts[3] if len(parts) > 3 else None
                    if period == "custom":
                        period = context.user_data.get("custom_period")
                    filtered = filter_incomes(self.data.incomes, branch=branch, period=period)
                    if not filtered:
                        await query.edit_message_text("Нет данных для детализации.")
                        return
                    daily = defaultdict(float)
                    for inc in filtered:
                        day = datetime.fromisoformat(inc["date"]).date()
                        daily[day] += inc["amount"]
                    sorted_days = sorted(daily.items())
                    text = f"📅 *Детализация по дням для филиала '{branch}'*\n" + "\n".join(f"{day.strftime('%d.%m.%Y')}: {amount:.2f} руб." for day, amount in sorted_days)
                    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(add_back([])))
                return

            # Редактирование мастеров
            if data.startswith("edit_master_delete_"):
                name = data.split("_", 2)[2]
                if name in self.data.get_all_master_names():
                    self.data.masters = [m for m in self.data.masters if m["name"] != name]
                    self.data.save_all()
                    await self._refresh_master_list(query)
                else:
                    await query.edit_message_text(f"Мастер {name} уже удалён.")
                return

            if data.startswith("edit_master_rename_"):
                name = data.split("_", 2)[2]
                context.user_data["rename_old"] = name
                await query.edit_message_text(f"Введите новое имя для «{name}»:")
                context.user_data["wait_rename"] = True
                return

            # Выбор мастера для записи дохода
            if data.startswith("master_") or data == "skip_master":
                if data == "skip_master":
                    await query.edit_message_text("Доход не записан.")
                    return
                master = data.split("_", 1)[1]
                result = context.user_data.get("last_result")
                text = context.user_data.get("last_text", "")
                if not result:
                    await query.edit_message_text("❌ Ошибка: результат не найден.")
                    return
                branch = self.data.get_branch(master)
                self.data.incomes.append({
                    "master": master,
                    "branch": branch,
                    "amount": result["net"],
                    "date": datetime.now().isoformat(),
                    "text": text
                })
                self.data.save_all()
                await query.edit_message_text(f"✅ Доход {result['net']:.2f} руб. для {master} записан (филиал {branch}).")
                return

            # Процент
            if data.startswith("percent_"):
                val = data.split("_", 1)[1]
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

            # Рейтинг период
            if data.startswith("rating_period_"):
                period = data.split("_", 1)[1]
                if period == "custom":
                    await query.edit_message_text("Введите даты ГГГГ-ММ-ДД ГГГГ-ММ-ДД:")
                    context.user_data["wait_rating_dates"] = True
                    return
                await self._show_rating(update, context, period, target=query.message)
                return

            logger.warning(f"Неизвестный callback: {data}")
        except Exception as e:
            logger.error(f"Callback error {data}: {e}")
            try:
                await query.edit_message_text("Ошибка. Попробуйте снова.")
            except: pass

    async def _refresh_master_list(self, query):
        if not self.data.masters:
            await query.edit_message_text("Список мастеров пуст.")
            return
        keyboard = []
        for m in self.data.masters:
            keyboard.append([
                InlineKeyboardButton(f"{m['name']} ({m['branch']})", callback_data="noop"),
                InlineKeyboardButton("✏️", callback_data=f"edit_master_rename_{m['name']}"),
                InlineKeyboardButton("❌", callback_data=f"edit_master_delete_{m['name']}")
            ])
        keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="back_to_main")])
        await query.edit_message_text("📋 Список мастеров:", reply_markup=InlineKeyboardMarkup(keyboard))

    # ---------- Основной обработчик сообщений ----------
    async def handle_message(self, update, context):
        try:
            logger.info(f"Получено сообщение: {update.effective_message.text}")
            if not update or not update.effective_message:
                return
            text = get_message_text(update)
            if not text:
                await self._reply(update, "Я не вижу текста.")
                return

            # Обработка кнопок категорий
            if text in SUBMENUS:
                await self.show_submenu(update, context, text)
                return

            # Ожидания (состояния)
            if context.user_data.get("wait_calc_only"):
                acc = parse_numbers(text) + await self._get_total_extra(context) - await self._get_total_expenses(context)
                if acc <= 0:
                    await self._reply(update, "❌ Итоговая сумма <= 0.")
                else:
                    result = calculate(acc, self.percent)
                    await self._reply(update, f"📊 Начислено: {result['accrued']:.2f} руб.\nУдержание {result['percent']:.1f}%: {result['deductions']:.2f} руб.\n💵 К выдаче: {result['net']:.2f} руб.\n\n✅ Расчёт выполнен (без сохранения).")
                context.user_data.pop("wait_calc_only", None)
                return

            if context.user_data.get("wait_extra_income"):
                parts = text.split(maxsplit=1)
                if len(parts) < 2:
                    await self._reply(update, "Введите сумму и описание через пробел, например:\n5000 Премия")
                    return
                try:
                    amount = float(parts[0])
                    if amount <= 0:
                        await self._reply(update, "❌ Сумма должна быть положительной.")
                        return
                    description = parts[1]
                    context.user_data.setdefault("extra_incomes", []).append({"amount": amount, "description": description})
                    context.user_data.pop("wait_extra_income", None)
                    await self._reply(update, f"✅ Добавлен дополнительный доход: {amount:.2f} руб. ({description})")
                except ValueError:
                    await self._reply(update, "❌ Сумма должна быть числом.")
                return

            if context.user_data.get("wait_expense"):
                parts = text.split(maxsplit=1)
                if len(parts) < 2:
                    await self._reply(update, "Введите сумму и описание через пробел, например:\n1500 Материалы")
                    return
                try:
                    amount = float(parts[0])
                    if amount <= 0:
                        await self._reply(update, "❌ Сумма должна быть положительной.")
                        return
                    description = parts[1]
                    context.user_data.setdefault("expenses", []).append({"amount": amount, "description": description})
                    context.user_data.pop("wait_expense", None)
                    await self._reply(update, f"✅ Добавлен расход: {amount:.2f} руб. ({description})")
                except ValueError:
                    await self._reply(update, "❌ Сумма должна быть числом.")
                return

            if context.user_data.get("wait_master_name"):
                await self._process_add_master_name(update, context, text.strip())
                return

            if context.user_data.get("wait_branch_name"):
                name = text.strip()
                if not name:
                    await self._reply(update, "Название не может быть пустым.")
                    return
                if name in self.data.branches:
                    await self._reply(update, f"Филиал '{name}' уже существует.")
                else:
                    self.data.branches.append(name)
                    self.data.save_all()
                    await self._reply(update, f"✅ Филиал '{name}' добавлен.")
                context.user_data.pop("wait_branch_name", None)
                return

            if context.user_data.get("wait_rename"):
                old = context.user_data.get("rename_old")
                new = text.strip()
                if not new:
                    await self._reply(update, "Имя не может быть пустым.")
                    return
                if new in self.data.get_all_master_names() and new != old:
                    await self._reply(update, f"Мастер {new} уже существует.")
                    self._clear_state(context, "wait_rename", "rename_old")
                    return
                for m in self.data.masters:
                    if m["name"] == old:
                        m["name"] = new
                        break
                for inc in self.data.incomes:
                    if inc["master"] == old:
                        inc["master"] = new
                for uid, m in self.data.users.items():
                    if m == old:
                        self.data.users[uid] = new
                self.data.save_all()
                self._clear_state(context, "wait_rename", "rename_old")
                await self._reply(update, f"✅ Переименован: {old} → {new}")
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
                            branch = context.user_data.get("stats_branch")
                            if branch:
                                context.user_data.pop("wait_custom_dates", None)
                                await self._show_branch_stats(update, context, branch, period)
                            else:
                                await self._reply(update, "Ошибка: филиал не выбран.")
                        else:
                            context.user_data.pop("wait_rating_dates", None)
                            await self._show_rating(update, context, period)
                        return
                    except Exception as e:
                        logger.error(f"Ошибка парсинга дат: {e}")
                        await self._reply(update, "Ошибка формата. Введите даты как ГГГГ-ММ-ДД ГГГГ-ММ-ДД")
                else:
                    await self._reply(update, "Введите две даты через пробел.")
                return

            # Основной расчёт
            extra = await self._get_total_extra(context)
            expenses = await self._get_total_expenses(context)
            acc = parse_numbers(text) + extra - expenses
            if acc <= 0:
                await self._reply(update, "❌ Итоговая сумма <= 0.")
                return

            result = calculate(acc, self.percent)
            master = self.data.users.get(str(update.effective_user.id))
            if master:
                branch = self.data.get_branch(master)
                self.data.incomes.append({
                    "master": master,
                    "branch": branch,
                    "amount": result["net"],
                    "date": datetime.now().isoformat(),
                    "text": text + (f" (доп. доходы: {extra:.2f}, расходы: {expenses:.2f})" if extra or expenses else "")
                })
                self.data.save_all()
                await self._reply(update, f"📊 Начислено: {result['accrued']:.2f} руб.\nУдержание {result['percent']:.1f}%: {result['deductions']:.2f} руб.\n💵 К выдаче: {result['net']:.2f} руб.\n\n✅ Доход записан на мастера {master} (филиал {branch}).")
            else:
                context.user_data["last_result"] = result
                context.user_data["last_text"] = text + (f" (доп. доходы: {extra:.2f}, расходы: {expenses:.2f})" if extra or expenses else "")
                keyboard = [[InlineKeyboardButton(f"{m['name']} ({m['branch']})", callback_data=f"master_{m['name']}")] for m in self.data.masters]
                keyboard.append([InlineKeyboardButton("❌ Не записывать", callback_data="skip_master")])
                await self._reply(update,
                    f"📊 Начислено: {result['accrued']:.2f} руб.\nУдержание {result['percent']:.1f}%: {result['deductions']:.2f} руб.\n💵 К выдаче: {result['net']:.2f} руб.\n\nВы не зарегистрированы. Выберите мастера для записи дохода:",
                    reply_markup=InlineKeyboardMarkup(add_back(keyboard)))

        except Exception as e:
            logger.error(f"Критическая ошибка в handle_message: {e}", exc_info=True)
            await self._reply(update, f"❌ Ошибка: {str(e)}")

    def run(self):
        logger.info("Бот запущен")
        self.app.run_polling()

if __name__ == "__main__":
    token = os.environ.get("BOT_TOKEN") or input("Введите токен: ").strip()
    if not token:
        print("Токен не введён.")
        exit()
    bot = SalaryBot(token)
    bot.run()
                         
