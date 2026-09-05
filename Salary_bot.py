import re,json,os,csv,io
from datetime import datetime,timedelta
from telegram import Update,InlineKeyboardButton,InlineKeyboardMarkup,ReplyKeyboardMarkup
from telegram.ext import Application,CommandHandler,MessageHandler,CallbackQueryHandler,filters,ContextTypes

# ---------- Конфигурация ----------
F=("bot_settings.json","masters.json","incomes.json","users.json")
D=70.0
BACK="back_to_menu"

# ---------- Клавиатуры ----------
KB=ReplyKeyboardMarkup([
    ["➕ Добавить мастера","📋 Список мастеров","🧮 Простой расчёт"],
    ["📊 Статистика","🏆 Рейтинг","📤 Экспорт CSV"],
    ["⚙️ Процент удержания","❓ Помощь"]
],resize_keyboard=True)

def add_back(k): k.append([InlineKeyboardButton("🔙 Назад",callback_data=BACK)]); return k
def period_kb(p):
    return InlineKeyboardMarkup(add_back([
        [InlineKeyboardButton("📅 Сегодня",callback_data=f"{p}_сегодня"),InlineKeyboardButton("📅 Неделя",callback_data=f"{p}_неделя")],
        [InlineKeyboardButton("📅 Месяц",callback_data=f"{p}_месяц"),InlineKeyboardButton("📅 Год",callback_data=f"{p}_год")],
        [InlineKeyboardButton("📅 Вся история",callback_data=f"{p}_все"),InlineKeyboardButton("📅 Произвольный диапазон",callback_data=f"{p}_custom")]
    ]))

# ---------- Данные ----------
class D:
    def __init__(s):
        s.s=json.load(open(F[0],encoding='utf-8')) if os.path.exists(F[0]) else {"deduction_percent":D}
        s.m=json.load(open(F[1],encoding='utf-8')) if os.path.exists(F[1]) else []
        s.i=json.load(open(F[2],encoding='utf-8')) if os.path.exists(F[2]) else []
        s.u=json.load(open(F[3],encoding='utf-8')) if os.path.exists(F[3]) else {}
    def _save(s,f,d): json.dump(d,open(f,'w',encoding='utf-8'),indent=2,ensure_ascii=False)
    def save_all(s): s._save(F[0],s.s); s._save(F[1],s.m); s._save(F[2],s.i); s._save(F[3],s.u)

# ---------- Парсинг ----------
def parse(text):
    if not text: return 0.0
    text=re.sub(r'(?<=\d)\s+(?=\d)','',text); text=re.sub(r'(?<=\d),(?=\d)','.',text)
    return sum(float(n) for n in re.findall(r'-?\d+(?:\.\d+)?',text) if n)

def get_text(u): return u.message.text or u.message.caption or ""

# ---------- Расчёт ----------
def calc(acc,perc):
    ded=acc*(perc/100)
    return {"accrued":acc,"deductions":ded,"net":acc-ded,"percent":perc}

# ---------- Фильтр ----------
def filtr(incomes,name=None,period=None):
    now=datetime.now(); st=et=None
    if period:
        if period in ("день","сегодня"): st=datetime(now.year,now.month,now.day); et=st+timedelta(days=1)-timedelta(seconds=1)
        elif period=="неделя": st=now-timedelta(days=7)
        elif period=="месяц": st=now-timedelta(days=30)
        elif period=="год": st=now-timedelta(days=365)
        elif isinstance(period,tuple) and len(period)==2: st,et=period
    return [inc for inc in incomes if (not name or inc.get("master")==name) and (not st or (st<=datetime.fromisoformat(inc["date"]) and (not et or datetime.fromisoformat(inc["date"])<=et)))]

def parse_period(a):
    if a in ("день","сегодня","неделя","месяц","год","все"): return a
    p=a.split()
    if len(p)==2:
        try:
            d1=datetime.strptime(p[0],"%Y-%m-%d"); d2=datetime.strptime(p[1],"%Y-%m-%d")
            return (d1,d2) if d1<=d2 else (d2,d1)
        except: pass
    return None

# ---------- Бот ----------
class Bot:
    def __init__(s,t):
        s.d=D(); s.p=s.d.s.get("deduction_percent",D)
        s.app=Application.builder().token(t).build()
        for cmd,h in [("start",s.start),("help",s.help),("add_master",s.add),("remove_master",s.rm),("masters",s.list_m),
                      ("incomes",s.list_i),("edit_income",s.edit),("export",s.export),("percent",s.set_p),
                      ("stats",s.stats),("rating",s.rating),("register",s.reg),("unregister",s.unreg),
                      ("calc",s.calc_cmd)]:
            s.app.add_handler(CommandHandler(cmd,h))
        s.app.add_handler(CallbackQueryHandler(s.cb,pattern="^(percent|master_|skip_master|stats_master_|stats_period_|rating_period_|edit_master_|back_to_menu)"))
        s.app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, s.msg))

    # ---------- Команды ----------
    async def start(s,u,c): await u.message.reply_text("👋 Я считаю зарплату по списку работ.\nОтправь текст с числами.",reply_markup=KB)
    async def help(s,u,c): await u.message.reply_text("📖 Справка: /add_master, /masters (редактор), /stats, /rating, /export, /percent, /register <имя>, /unregister, /calc <текст>",reply_markup=KB)

    async def reg(s,u,c):
        if not c.args: return await u.message.reply_text("Укажите имя: /register Иван")
        name=" ".join(c.args).strip()
        if name not in s.d.m: return await u.message.reply_text(f"Мастер '{name}' не существует.")
        if any(m==name and int(uid)!=u.effective_user.id for uid,m in s.d.u.items()): return await u.message.reply_text(f"Имя '{name}' занято.")
        s.d.u[str(u.effective_user.id)]=name; s.d.save_all(); await u.message.reply_text(f"✅ Вы зарегистрированы как '{name}'.")
    async def unreg(s,u,c):
        uid=str(u.effective_user.id)
        if uid in s.d.u: del s.d.u[uid]; s.d.save_all(); await u.message.reply_text("✅ Отвязаны.")
        else: await u.message.reply_text("Вы не зарегистрированы.")

    async def add(s,u,c):
        if c.args:
            name=" ".join(c.args).strip()
            if name in s.d.m: return await u.message.reply_text(f"Мастер {name} уже есть.")
            s.d.m.append(name); s.d.save_all(); await u.message.reply_text(f"✅ Мастер {name} добавлен.")
        else:
            await u.message.reply_text("Введите имя нового мастера:")
            c.user_data["wait_master"]=True
    async def rm(s,u,c):
        name=" ".join(c.args).strip()
        if not name: return await u.message.reply_text("Укажите имя: /remove_master Иван")
        if name not in s.d.m: return await u.message.reply_text(f"Мастер {name} не найден.")
        s.d.m.remove(name); s.d.save_all(); await u.message.reply_text(f"✅ Мастер {name} удалён.")

    async def list_m(s,u,c):
        if not s.d.m: return await u.message.reply_text("Список мастеров пуст.")
        kb=[]
        for m in s.d.m: kb.append([InlineKeyboardButton(m,callback_data="noop"),InlineKeyboardButton("✏️",callback_data=f"edit_master_rename_{m}"),InlineKeyboardButton("❌",callback_data=f"edit_master_delete_{m}")])
        kb.append([InlineKeyboardButton("🔙 Назад",callback_data=BACK)])
        await u.message.reply_text("📋 Список мастеров:",reply_markup=InlineKeyboardMarkup(kb))

    async def list_i(s,u,c):
        if not s.d.i: return await u.message.reply_text("Нет записей.")
        lines=["📋 Доходы:"]
        for i,inc in enumerate(s.d.i,1): lines.append(f"{i}. {inc['master']} – {inc['amount']:.2f} ({inc['date'][:10]})")
        for ch in [lines[i:i+20] for i in range(0,len(lines),20)]: await u.message.reply_text("\n".join(ch))
    async def edit(s,u,c):
        a=c.args
        if len(a)<2: return await u.message.reply_text("/edit_income <номер> <сумма>")
        try:
            idx=int(a[0])-1
            if idx<0 or idx>=len(s.d.i): return await u.message.reply_text("Некорректный номер.")
            new=float(a[1])
            if new<0: return await u.message.reply_text("Сумма не может быть отрицательной.")
            s.d.i[idx]["amount"]=new; s.d.save_all(); await u.message.reply_text(f"✅ Доход №{idx+1} обновлён: {new:.2f}")
        except: await u.message.reply_text("Ошибка ввода.")
    async def export(s,u,c):
        if not s.d.i: return await u.message.reply_text("Нет данных.")
        out=io.StringIO()
        w=csv.writer(out,delimiter=';')
        w.writerow(["Мастер","Сумма","Дата","Текст"])
        for inc in s.d.i: w.writerow([inc["master"],inc["amount"],inc["date"],inc.get("text","")])
        out.seek(0)
        await u.message.reply_document(document=out.getvalue().encode('utf-8-sig'),filename="incomes.csv",caption="📊 Экспорт")

    async def set_p(s,u,c):
        await u.message.reply_text("Выберите процент:",reply_markup=InlineKeyboardMarkup(add_back([
            [InlineKeyboardButton("70%",callback_data="percent_70"),InlineKeyboardButton("60%",callback_data="percent_60")],
            [InlineKeyboardButton("50%",callback_data="percent_50"),InlineKeyboardButton("40%",callback_data="percent_40")],
            [InlineKeyboardButton("Своё...",callback_data="percent_custom")]
        ])))
    async def _p_input(s,u,c):
        try:
            p=float(u.message.text.strip())
            if 0<=p<=100:
                s.p=p; s.d.s["deduction_percent"]=p; s.d.save_all(); await u.message.reply_text(f"✅ Удержание {p:.1f}%")
            else: await u.message.reply_text("❌ От 0 до 100.")
        except: await u.message.reply_text("❌ Введите число.")
        c.user_data["wait_percent"]=False

    # ---------- Команда /calc (простой расчёт без сохранения) ----------
    async def calc_cmd(s,u,c):
        if c.args:
            text=" ".join(c.args)
            acc=parse(text)
            if acc==0: return await u.message.reply_text("❌ Не найдено чисел.")
            res=calc(acc,s.p)
            await u.message.reply_text(f"📊 Начислено: {res['accrued']:.2f} руб.\nУдержание {res['percent']:.1f}%: {res['deductions']:.2f} руб.\n💵 К выдаче: {res['net']:.2f} руб.")
        else:
            await u.message.reply_text("Введите текст с числами: /calc Лексус 1300")

    # ---------- Статистика и рейтинг ----------
    async def stats(s,u,c):
        if c.args:
            name=c.args[0]
            period=parse_period(" ".join(c.args[1:])) if len(c.args)>1 else None
            if name in s.d.m: return await s._show(u,c,"stats",master=name,period=period)
            else: return await s._show(u,c,"stats",master=None,period=name)
        kb=[[InlineKeyboardButton("📊 Все мастера",callback_data="stats_master_все")]]
        for m in s.d.m: kb.append([InlineKeyboardButton(m,callback_data=f"stats_master_{m}")])
        await u.message.reply_text("Выберите мастера:",reply_markup=InlineKeyboardMarkup(add_back(kb)))

    async def rating(s,u,c):
        if c.args:
            period=parse_period(" ".join(c.args))
            if period: return await s._show(u,c,"rating",period=period)
        await u.message.reply_text("Выберите период:",reply_markup=period_kb("rating_period"))

    async def _show(s,u,c,mode,master=None,period=None,target=None):
        if isinstance(period,str) and period in ("день","сегодня","неделя","месяц","год","все"):
            period_spec=None if period=="все" else period
        elif isinstance(period,tuple): period_spec=period
        else: period_spec=None
        filtered=filtr(s.d.i,master_name=master if master!="все" else None,period=period_spec)
        if not filtered:
            msg="Нет данных."
            if target: await target.edit_text(msg,reply_markup=InlineKeyboardMarkup(add_back([])))
            else: await u.message.reply_text(msg)
            return
        period_disp={"сегодня":"Сегодня","неделя":"Неделя","месяц":"Месяц","год":"Год","все":"Всю историю"}.get(period if isinstance(period,str) else "все",str(period)) if period else "весь период"
        if mode=="stats":
            if master and master!="все":
                total=sum(inc["amount"] for inc in filtered); cnt=len(filtered)
                text=f"📊 Статистика мастера *{master}* за {period_disp}\n💰 {total:.2f} руб.\n📦 {cnt} записей"
                if cnt: text+=f"\n📈 Средний чек: {total/cnt:.2f} руб."
            else:
                st={}
                for inc in filtered: st[inc["master"]]=st.get(inc["master"],0)+inc["amount"]
                total_all=sum(st.values())
                text=f"📊 *Общая статистика за {period_disp}*\n"+"\n".join(f"• {m}: {s:.2f} руб." for m,s in st.items())
                text+=f"\n\n🏷 *Итого: {total_all:.2f} руб.*"
        else: # rating
            rd={}
            for inc in filtered: rd[inc["master"]]=rd.get(inc["master"],0)+inc["amount"]
            sorted_rating=sorted(rd.items(),key=lambda x:x[1],reverse=True)
            lines=[f"🏆 *Рейтинг мастеров за {period_disp}*"]
            for i,(m,amt) in enumerate(sorted_rating,1):
                medal="🥇" if i==1 else "🥈" if i==2 else "🥉" if i==3 else f"{i}."
                lines.append(f"{medal} {m} – {amt:.2f} руб.")
            text="\n".join(lines)
        if target:
            await target.edit_text(text,parse_mode="Markdown",reply_markup=InlineKeyboardMarkup(add_back([])))
        else:
            await u.message.reply_text(text,parse_mode="Markdown",reply_markup=InlineKeyboardMarkup(add_back([])))

    # ---------- Callback ----------
    async def cb(s,u,c):
        q=u.callback_query; await q.answer(); data=q.data
        if data==BACK:
            await q.message.delete()
            await q.message.reply_text("Главное меню:",reply_markup=KB)
            return
        if data.startswith("percent_"):
            val=data.split("_")[1]
            if val=="custom":
                await q.edit_message_text("Введите число от 0 до 100:")
                c.user_data["wait_percent"]=True
            else:
                p=float(val); s.p=p; s.d.s["deduction_percent"]=p; s.d.save_all()
                await q.edit_message_text(f"✅ Удержание {p:.1f}%")
            return
        if data.startswith("edit_master_delete_"):
            name=data.replace("edit_master_delete_","")
            if name in s.d.m: s.d.m.remove(name); s.d.save_all(); await s._refresh_master(q)
            else: await q.edit_message_text(f"Мастер {name} уже удалён.")
            return
        if data.startswith("edit_master_rename_"):
            name=data.replace("edit_master_rename_","")
            c.user_data["rename_old"]=name
            await q.edit_message_text(f"Введите новое имя для «{name}»:")
            c.user_data["wait_rename"]=True
            return
        if data.startswith("stats_master_"):
            master=data.replace("stats_master_","")
            c.user_data["stats_master"]=master
            await q.edit_message_text(f"Мастер: {master if master!='все' else 'Все'}\nВыберите период:",reply_markup=period_kb("stats_period"))
            return
        if data.startswith("stats_period_"):
            period=data.replace("stats_period_","")
            if period=="custom":
                await q.edit_message_text("Введите даты ГГГГ-ММ-ДД ГГГГ-ММ-ДД:")
                c.user_data["wait_custom_dates"]=True
                return
            master=c.user_data.get("stats_master","все")
            await s._show(u,c,"stats",master=master,period=period,target=q.message)
            return
        if data.startswith("rating_period_"):
            period=data.replace("rating_period_","")
            if period=="custom":
                await q.edit_message_text("Введите даты ГГГГ-ММ-ДД ГГГГ-ММ-ДД:")
                c.user_data["wait_rating_dates"]=True
                return
            await s._show(u,c,"rating",period=period,target=q.message)
            return
        if data.startswith("master_") or data=="skip_master":
            if data=="skip_master": return await q.edit_message_text("Доход не записан.")
            master=data[7:]
            res=c.user_data.get("last_result"); txt=c.user_data.get("last_text","")
            if not res: return await q.edit_message_text("❌ Ошибка.")
            s.d.i.append({"master":master,"amount":res["net"],"date":datetime.now().isoformat(),"text":txt})
            s.d.save_all()
            await q.edit_message_text(f"✅ Доход {res['net']:.2f} руб. для {master} записан.")

    async def _refresh_master(s,q):
        if not s.d.m: return await q.edit_message_text("Список мастеров пуст.")
        kb=[]
        for m in s.d.m: kb.append([InlineKeyboardButton(m,callback_data="noop"),InlineKeyboardButton("✏️",callback_data=f"edit_master_rename_{m}"),InlineKeyboardButton("❌",callback_data=f"edit_master_delete_{m}")])
        kb.append([InlineKeyboardButton("🔙 Назад",callback_data=BACK)])
        await q.edit_message_text("📋 Список мастеров:",reply_markup=InlineKeyboardMarkup(kb))

    # ---------- Сообщения ----------
    async def msg(s,u,c):
        uid=u.effective_user.id; text=get_text(u)
        if not text: return
        # Кнопки
        btn_map={
            "➕ Добавить мастера":s.add,
            "📋 Список мастеров":s.list_m,
            "🧮 Простой расчёт":s.simple_calc,
            "📊 Статистика":s.stats,
            "🏆 Рейтинг":s.rating,
            "📤 Экспорт CSV":s.export,
            "⚙️ Процент удержания":s.set_p,
            "❓ Помощь":s.help
        }
        if text in btn_map: return await btn_map[text](u,c)

        # Обработка состояния "простой расчёт"
        if c.user_data.get("wait_calc_only"):
            acc=parse(text)
            if acc==0:
                await u.message.reply_text("❌ Не найдено чисел.")
            else:
                res=calc(acc,s.p)
                await u.message.reply_text(
                    f"📊 Начислено: {res['accrued']:.2f} руб.\n"
                    f"Удержание {res['percent']:.1f}%: {res['deductions']:.2f} руб.\n"
                    f"💵 К выдаче: {res['net']:.2f} руб.\n\n"
                    "✅ Расчёт выполнен (без сохранения)."
                )
            c.user_data["wait_calc_only"]=False
            return

        # Остальные ожидания (добавление мастера, переименование, процент, даты)
        if c.user_data.get("wait_master"):
            name=text.strip()
            if name in s.d.m: await u.message.reply_text(f"Мастер {name} уже есть.")
            else: s.d.m.append(name); s.d.save_all(); await u.message.reply_text(f"✅ Мастер {name} добавлен.")
            c.user_data["wait_master"]=False; return
        if c.user_data.get("wait_rename"):
            old=c.user_data.get("rename_old"); new=text.strip()
            if not new: return await u.message.reply_text("Имя не может быть пустым.")
            if new in s.d.m and new!=old: return await u.message.reply_text(f"Мастер {new} уже существует.")
            idx=s.d.m.index(old); s.d.m[idx]=new
            for inc in s.d.i:
                if inc["master"]==old: inc["master"]=new
            for uid2,m in s.d.u.items():
                if m==old: s.d.u[uid2]=new
            s.d.save_all()
            c.user_data["wait_rename"]=False; c.user_data["rename_old"]=None
            await u.message.reply_text(f"✅ Переименован: {old} → {new}")
            await s.list_m(u,c); return
        if c.user_data.get("wait_percent"): return await s._p_input(u,c)
        if c.user_data.get("wait_custom_dates") or c.user_data.get("wait_rating_dates"):
            parts=text.split()
            if len(parts)==2:
                try:
                    d1=datetime.strptime(parts[0],"%Y-%m-%d"); d2=datetime.strptime(parts[1],"%Y-%m-%d")
                    period=(d1,d2) if d1<=d2 else (d2,d1)
                    if c.user_data.get("wait_custom_dates"):
                        master=c.user_data.get("stats_master","все")
                        c.user_data["wait_custom_dates"]=False
                        await s._show(u,c,"stats",master=master,period=period)
                    else:
                        c.user_data["wait_rating_dates"]=False
                        await s._show(u,c,"rating",period=period)
                    return
                except: await u.message.reply_text("Ошибка формата. Введите даты как ГГГГ-ММ-ДД ГГГГ-ММ-ДД")
            else: await u.message.reply_text("Введите две даты через пробел.")
            return

        # Основной расчёт с сохранением (для зарегистрированных или с выбором мастера)
        acc=parse(text)
        if acc==0: return await u.message.reply_text("❌ Не найдено чисел. Пример: 'Лексус 1300'")
        res=calc(acc,s.p)
        master=s.d.u.get(str(uid))
        if master:
            s.d.i.append({"master":master,"amount":res["net"],"date":datetime.now().isoformat(),"text":text})
            s.d.save_all()
            await u.message.reply_text(f"📊 Начислено: {res['accrued']:.2f} руб.\nУдержание {res['percent']:.1f}%: {res['deductions']:.2f} руб.\n💵 К выдаче: {res['net']:.2f} руб.\n\n✅ Доход записан на {master}.")
        else:
            c.user_data["last_result"]=res; c.user_data["last_text"]=text
            kb=[]
            if s.d.m:
                row=[]
                for m in s.d.m:
                    row.append(InlineKeyboardButton(m,callback_data=f"master_{m}"))
                    if len(row)==2: kb.append(row); row=[]
                if row: kb.append(row)
            kb.append([InlineKeyboardButton("❌ Не записывать",callback_data="skip_master")])
            await u.message.reply_text(f"📊 Начислено: {res['accrued']:.2f} руб.\nУдержание {res['percent']:.1f}%: {res['deductions']:.2f} руб.\n💵 К выдаче: {res['net']:.2f} руб.\n\nВыберите мастера для записи:",reply_markup=InlineKeyboardMarkup(add_back(kb)))

    async def simple_calc(s,u,c):
        await u.message.reply_text("Введите список работ с числами для расчёта (без сохранения):")
        c.user_data["wait_calc_only"] = True

    # ---------- Запуск ----------
    def run(s): print("✅ Бот запущен."); s.app.run_polling()

# ---------- Точка входа ----------
if __name__=="__main__":
    t=os.environ.get("BOT_TOKEN")
    if not t:
        try: t=input("Введите токен: ").strip()
        except EOFError: print("Установите BOT_TOKEN"); exit()
    if not t: print("Токен не введён."); exit()
    Bot(t).run()
