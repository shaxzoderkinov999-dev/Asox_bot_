import html
import logging
import time
from collections import OrderedDict

import aiohttp
from aiogram import F, Router
from aiogram.filters import CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from config import ADMIN_BOT_TOKEN, ADMIN_IDS, SHOP_LINK_SECRET
from database.db import db
from keyboards.reply import get_main_keyboard, get_phone_keyboard
from states.states import CourierApplyStates, DesignerApplyStates
from utils.security import parse_shop_payload, verify_shop_link_token
from utils.validators import normalize_phone, require_text

logger = logging.getLogger(__name__)

router = Router()


async def _notify_admins(text: str) -> None:
    """Adminlarga admin-bot orqali ZAXIRA xabar yuboradi.

    Odatda ariza haqidagi bildirishnomani Cloud Functions yuboradi
    (`onDesignerRequestCreated` / `onCourierRequestCreated`) — u Firestore
    yozuviga ulangani uchun saytdan kelgan arizalarni ham qamrab oladi va
    xabari kengroq (`about` / `note` maydonlari ham bor).

    Shuning uchun arizalar bo'yicha bu funksiya faqat Firestore yozuvi AMALGA
    OSHMAGANDA chaqiriladi: unday holatda CF umuman ishga tushmaydi va ariza
    izsiz yo'qolardi. Ilgari u har safar chaqirilardi va admin bitta arizani
    IKKI marta olardi.

    Do'kon ulash hodisalari uchun esa Cloud Functions tomonida hech qanday
    trigger yo'q (`shops` hujjati faqat yangilanadi), shuning uchun ular shu
    yerdan yuboriladi — `_alert_admins_throttled()` ga qarang.
    """
    if not ADMIN_BOT_TOKEN or not ADMIN_IDS:
        return
    url = f"https://api.telegram.org/bot{ADMIN_BOT_TOKEN}/sendMessage"
    timeout = aiohttp.ClientTimeout(total=5)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            for admin_id in ADMIN_IDS:
                try:
                    await session.post(url, json={
                        "chat_id": admin_id,
                        "text": text,
                        "parse_mode": "HTML",
                        "disable_web_page_preview": True,
                    })
                except Exception as e:
                    print(f"Error sending admin notification to {admin_id}: {e}")
    except Exception as e:
        print(f"Error opening admin notification session: {e}")


SHOP_LINK_ERRORS = {
    "no_db": (
        "⚠️ <b>Ma'lumotlar bazasi hozir mavjud emas.</b>\n"
        "Iltimos, birozdan so'ng qayta urinib ko'ring."
    ),
    "not_found": (
        "❌ <b>Bunday do'kon topilmadi.</b>\n"
        "Havolani asox.uz kabinetingizdan qaytadan oling."
    ),
    "already_linked": (
        "⛔️ <b>Bu do'kon allaqachon boshqa Telegram akkauntga bog'langan.</b>\n\n"
        "Agar bu siz bo'lmasangiz, zudlik bilan @asox_support ga murojaat qiling."
    ),
    "error": (
        "⚠️ <b>Do'konni ulashda texnik xatolik yuz berdi.</b>\n"
        "Iltimos, keyinroq urinib ko'ring."
    ),
}


# Do'kon ulash hodisalari haqida adminlarga ogohlantirish.
#
# Muvaffaqiyatli ulanish va "band do'kon" urinishi HAR SAFAR yuboriladi —
# ular kam uchraydi va yuqori signalga ega. Yaroqsiz imzo / topilmagan do'kon
# kabi holatlar esa hujumchi tomonidan ketma-ket yuborilishi mumkin, shuning
# uchun ular foydalanuvchi+sabab bo'yicha cooldown bilan cheklanadi: aks holda
# admin bot ogohlantirishlar seli ostida qolardi.
SHOP_LINK_ALERT_COOLDOWN_SECONDS = 900  # 15 daqiqa
_MAX_TRACKED_ALERTS = 5000
_last_shop_link_alert: "OrderedDict[tuple[int, str], float]" = OrderedDict()


def _alert_allowed(user_id: int, reason: str) -> bool:
    """Shu foydalanuvchi+sabab uchun ogohlantirish yuborish mumkinmi."""
    key = (user_id, reason)
    now = time.monotonic()
    previous = _last_shop_link_alert.get(key)
    if previous is not None and (now - previous) < SHOP_LINK_ALERT_COOLDOWN_SECONDS:
        return False
    _last_shop_link_alert[key] = now
    _last_shop_link_alert.move_to_end(key)
    while len(_last_shop_link_alert) > _MAX_TRACKED_ALERTS:
        _last_shop_link_alert.popitem(last=False)
    return True


async def _alert_admins_throttled(user_id: int, reason: str, text: str) -> None:
    if _alert_allowed(user_id, reason):
        await _notify_admins(text)


def _who(user_id: int, username: str | None, full_name: str | None) -> str:
    """Ogohlantirishlar uchun foydalanuvchi identifikatori."""
    handle = f"@{html.escape(username)}" if username else "username yo'q"
    name = html.escape(full_name or "Noma'lum")
    return f"{name} ({handle}, <code>{user_id}</code>)"


async def _handle_shop_link(
    message: Message, payload: str, user_id: int, username: str | None
) -> None:
    """`/start shop_<id>_<imzo>` havolasini imzo bilan tekshirib do'konni ulaydi.

    Imzosiz havolalar rad etiladi: aks holda do'kon ID sini bilgan har kim
    do'konning buyurtma bildirishnomalarini (mijoz ismi, telefoni, manzili)
    o'ziga yo'naltirib olishi mumkin edi.
    """
    shop_id, signature = parse_shop_payload(payload)

    who = _who(user_id, username, message.from_user.full_name)

    if not SHOP_LINK_SECRET:
        logger.error("SHOP_LINK_SECRET sozlanmagan — do'kon ulash o'chirib qo'yildi")
        await _alert_admins_throttled(
            user_id,
            "no_secret",
            "🚨 <b>DO'KON ULASH O'CHIQ — SHOP_LINK_SECRET sozlanmagan!</b>\n\n"
            "Sotuvchilar do'konlarini ulay olmayapti.\n"
            f"👤 <b>Urinib ko'rdi:</b> {who}\n\n"
            "Kalitni bot muhitiga va asox.uz backendiga bir xil qilib qo'ying.",
        )
        await message.answer(
            "⚠️ <b>Do'kon ulash xizmati vaqtincha ishlamayapti.</b>\n"
            "Iltimos, @asox_support ga murojaat qiling.",
            reply_markup=get_main_keyboard(),
            parse_mode="HTML",
        )
        return

    if not shop_id or not verify_shop_link_token(shop_id, signature, SHOP_LINK_SECRET):
        logger.warning(
            "Yaroqsiz do'kon ulash havolasi: payload=%r user_id=%s", payload, user_id
        )
        await _alert_admins_throttled(
            user_id,
            "bad_signature",
            "⚠️ <b>YAROQSIZ DO'KON ULASH URINISHI</b>\n\n"
            f"👤 <b>Kim:</b> {who}\n"
            f"🔗 <b>Payload:</b> <code>{html.escape(payload)}</code>\n\n"
            "<i>Imzo mos kelmadi — havola qo'lda tuzilgan yoki eskirgan bo'lishi mumkin. "
            f"Takroriy urinishlar {SHOP_LINK_ALERT_COOLDOWN_SECONDS // 60} daqiqa davomida "
            "qayta xabar qilinmaydi.</i>",
        )
        await message.answer(
            "❌ <b>Havola yaroqsiz.</b>\n\n"
            "Do'konni ulash havolasini asox.uz saytidagi sotuvchi kabinetingizdan oling.",
            reply_markup=get_main_keyboard(),
            parse_mode="HTML",
        )
        return

    shop, error = await db.link_shop_telegram(shop_id, user_id, username)
    if error:
        if error == "already_linked":
            # Imzo TO'G'RI edi, lekin do'kon boshqa chatga bog'langan. Bu
            # havola sizib chiqqanini yoki egalik nizosini bildiradi —
            # cooldownsiz, har safar xabar qilinadi.
            await _notify_admins(
                "🚨 <b>BAND DO'KONNI ULASHGA URINISH</b>\n\n"
                f"🏪 <b>Do'kon ID:</b> <code>{html.escape(shop_id)}</code>\n"
                f"👤 <b>Kim:</b> {who}\n\n"
                "<i>Imzo to'g'ri edi, lekin do'kon allaqachon boshqa Telegram "
                "chatiga bog'langan. Havola sizib chiqqan bo'lishi mumkin — "
                "do'kon egasi bilan bog'laning.</i>"
            )
        else:
            await _alert_admins_throttled(
                user_id,
                error,
                "⚠️ <b>DO'KON ULASH MUVAFFAQIYATSIZ</b>\n\n"
                f"🏪 <b>Do'kon ID:</b> <code>{html.escape(shop_id)}</code>\n"
                f"👤 <b>Kim:</b> {who}\n"
                f"❗ <b>Sabab:</b> <code>{error}</code>",
            )
        await message.answer(
            SHOP_LINK_ERRORS.get(error, SHOP_LINK_ERRORS["error"]),
            reply_markup=get_main_keyboard(),
            parse_mode="HTML",
        )
        return

    shop_name = (shop.get("name") if shop else None) or shop_id
    logger.info("Do'kon %s -> Telegram chat %s ga bog'landi", shop_id, user_id)
    await message.answer(
        f"✅ <b>Do'koningiz ASOX Market botiga muvaffaqiyatli ulandi!</b>\n\n"
        f"🏪 <b>Do'kon:</b> <b>{html.escape(str(shop_name))}</b>\n"
        f"🆔 <b>Do'kon ID:</b> <code>{html.escape(shop_id)}</code>\n"
        f"👤 <b>Telegram ID:</b> <code>{user_id}</code>\n\n"
        f"🔔 Endi ushbu do'konga yangi buyurtma tushganda, barcha tafsilotlar real vaqtda shu yerga yuboriladi.",
        reply_markup=get_main_keyboard(),
        parse_mode="HTML",
    )

    # Muvaffaqiyatli ulanish — kam uchraydi, shuning uchun cooldownsiz.
    # Ilgari bu hodisa faqat server logiga tushardi va adminlar do'kon
    # bildirishnomalari kimga ketayotganini bilmasdi.
    await _notify_admins(
        "🏪 <b>DO'KON BOTGA ULANDI</b>\n\n"
        f"🏪 <b>Do'kon:</b> {html.escape(str(shop_name))}\n"
        f"🆔 <b>ID:</b> <code>{html.escape(shop_id)}</code>\n"
        f"👤 <b>Kim:</b> {who}\n\n"
        "<i>Endi shu do'konga tushgan buyurtmalar o'sha Telegram chatiga yuboriladi.</i>"
    )


# Taklif rad etilganda ko'rsatiladigan sabablar. HAR BIRI BOSHQACHA: "havola
# ishlamadi" degan yagona xabar bilan kuryer ham, do'kon ham nima qilishni
# bilmasdi.
_COURIER_INVITE_ERRORS = {
    "not_found": (
        "\u274c Bu havola yaroqsiz.\n\n"
        "Do'kondan yangi havola so'rang."
    ),
    "used": (
        "\u2139\ufe0f Bu havola allaqachon ishlatilgan.\n\n"
        "Agar bu siz bo'lmasangiz, do'konga xabar bering."
    ),
    "expired": (
        "\u23f0 Havolaning muddati tugagan.\n\n"
        "Do'kondan yangi havola so'rang."
    ),
    "unavailable": (
        "\u26a0\ufe0f Hozir bog'lab bo'lmadi. Birozdan keyin qayta urinib ko'ring."
    ),
}


async def _handle_courier_invite(
    message: Message, payload: str, user_id: int, username: str | None
) -> None:
    """`/start cour_<token>` — do'kon qo'shgan kuryerni chatga bog'laydi.

    NEGA HAVOLA KERAK: Telegram boti odamga @username orqali yoza olmaydi,
    faqat `chat_id` orqali — u esa odam Start bosgandagina paydo bo'ladi.
    Do'kon admin paneli ism va telefonni biladi, `chat_id` ni esa bilmaydi;
    shu bo'shliqni havola yopadi.

    Token IMZOLANMAGAN, chunki bu yerda tekshiriladigan hujjatning O'ZI bor
    (`courier_invites/{token}`): u 192-bitlik tasodifiy qiymat, bir martalik
    va muddatli. Do'kon havolasida (`shop_<id>_<imzo>`) imzo kerak edi —
    u yerda `shopId` ni har kim taxmin qila olardi.
    """
    token = payload[len("cour_"):].strip()
    if not token:
        await message.answer(_COURIER_INVITE_ERRORS["not_found"], parse_mode="HTML")
        return

    result = await db.redeem_courier_invite(token, user_id, username)
    error = result.get("error")
    if error:
        logger.warning("Kuryer taklifi rad etildi (%s): user=%s", error, user_id)
        await message.answer(
            _COURIER_INVITE_ERRORS.get(error, _COURIER_INVITE_ERRORS["not_found"]),
            parse_mode="HTML",
        )
        return

    shop_name = result.get("shopName") or "Do'kon"
    logger.info("Kuryer %s -> do'kon %s ga bog'landi", user_id, result.get("shopId"))
    await message.answer(
        f"\U0001f389 <b>{html.escape(str(shop_name))}</b> sizni kuryer sifatida qo'shdi.\n\n"
        "Endi shu do'konning zakaslari shu chatga keladi. Zakas ma'qul bo'lsa "
        "<b>\u2705 Qabul qildim</b> tugmasini bosing \u2014 kim birinchi bossa, "
        "zakas o'shaniki bo'ladi.",
        reply_markup=get_main_keyboard(),
        parse_mode="HTML",
    )


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext, command: CommandObject = None):
    await state.clear()
    user_id = message.from_user.id
    username = message.from_user.username
    full_name = message.from_user.full_name or "Foydalanuvchi"

    await db.get_or_create_user(user_id, username, full_name)

    # Sotuvchi do'konini ulash uchun kirgan bo'lsa: /start shop_<id>_<imzo>
    if command and command.args and command.args.startswith("shop_"):
        await _handle_shop_link(message, command.args, user_id, username)
        return

    # Do'kon admin paneli qo'shgan kuryer bo'lsa: /start cour_<token>
    if command and command.args and command.args.startswith("cour_"):
        await _handle_courier_invite(message, command.args, user_id, username)
        return

    welcome_text = (
        f"👋 <b>Xush kelibsiz, {html.escape(full_name)}!</b>\n\n"
        f"🛍 <b>ASOX Market</b> rasmiy botiga xush kelibsiz.\n"
        f"Bu yerda siz mahsulotlar katalogini ko'rishingiz, sotuvchi sifatida o'z do'koningizni ulab buyurtma bildirishnomalarini olishingiz mumkin.\n\n"
        f"🛒 Xaridlarni amalga oshirish uchun rasmiy <a href='https://asox.uz'>asox.uz</a> saytimizga o'ting!"
    )

    await message.answer(welcome_text, reply_markup=get_main_keyboard(), parse_mode="HTML")

@router.message(F.text == "🌐 ASOX Market sayti")
async def process_website_btn(message: Message):
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🌐 ASOX Market saytiga o'tish", url="https://asox.uz/app")]
        ]
    )
    await message.answer(
        "🛒 <b>ASOX Market Rasmiy Sayti</b>\n\n"
        "Mahsulotlarni to'liq ko'rish, savatga qo'shish va onlayn xarid qilish uchun quyidagi tugmani bosing:",
        reply_markup=kb,
        parse_mode="HTML"
    )

@router.message(F.text == "❌ Bekor qilish")
async def process_cancel(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Amal bekor qilindi.", reply_markup=get_main_keyboard())

@router.message(F.text == "👤 Profilim")
async def process_profile(message: Message):
    user_id = message.from_user.id
    user = await db.get_or_create_user(user_id)

    phone = user.get("phone_number") or "Kiritilmagan"
    full_name = html.escape(user.get('full_name') or 'Mavjud emas')
    profile_text = (
        f"👤 <b>Foydalanuvchi Profili</b>\n\n"
        f"🆔 <b>ID:</b> <code>{user_id}</code>\n"
        f"👤 <b>Ism:</b> {full_name}\n"
        f"📱 <b>Telefon:</b> {html.escape(str(phone))}\n"
        f"🌐 <b>Sayt:</b> <a href='https://asox.uz'>asox.uz</a>\n"
    )
    await message.answer(profile_text, parse_mode="HTML")

@router.message(F.text.in_(["📞 Biz bilan bog'lanish", "📞 Biz bilan aloqa", "Biz bilan bog'lanish", "Biz bilan aloqa"]))
async def process_contact(message: Message):
    contact_text = (
        "📞 <b>ASOX Market Qo'llab-quvvatlash Xizmati</b>\n\n"
        "❓ Savollaringiz yoki takliflaringiz bormi? Biz bilan bog'laning:\n\n"
        "☎️ <b>Telefon:</b> +998 (77) 048-37-63\n"
        "💬 <b>Telegram Support:</b> @asox_support\n"
        "🌐 <b>Veb-sayt:</b> <a href='https://asox.uz'>asox.uz</a>\n"
        "📍 <b>Manzil:</b> Oʻzbekiston, Samarqand shahri, Sadaf MFY, Gagarin ko'chasi, 60"
    )
    await message.answer(contact_text, parse_mode="HTML", disable_web_page_preview=True)

# ----------------------------------------------------
# 🎨 DIZAYNER BO'LISH ARIZA OQIMI
# ----------------------------------------------------
@router.message(F.text.in_(["🎨 Dizayner bo'lish", "Dizayner bo'lish", "🎨 Dizayner"]))
async def designer_apply_start(message: Message, state: FSMContext):
    await state.clear()
    await state.set_state(DesignerApplyStates.waiting_for_name)
    user_name = message.from_user.full_name or ""
    await message.answer(
        "🎨 <b>ASOX Market Dizaynerlar Jamoasiga Qabul!</b>\n\n"
        "O'z dizaynlaringiz, illyustratsiyalaringiz va mualliflik merchingizni platformamizda soting!\n\n"
        "1️⃣ <b>Ism va Familiyangizni kiriting:</b>\n"
        f"(Masalan: <i>{user_name or 'Alisher Zokirov'}</i>)",
        parse_mode="HTML"
    )

@router.message(DesignerApplyStates.waiting_for_name)
async def designer_apply_name(message: Message, state: FSMContext):
    name = await require_text(message, "❌ Iltimos, ism va familiyangizni <b>matn</b> ko'rinishida yuboring:")
    if name is None:
        return  # holat saqlanadi — foydalanuvchi shu bosqichda qayta urinadi
    await state.update_data(fullName=name)
    await state.set_state(DesignerApplyStates.waiting_for_phone)
    await message.answer(
        "2️⃣ <b>Bog'lanish uchun telefon raqamingizni yuboring:</b>\n\n"
        "(Pastdagi tugmani bosing yoki <code>+998901234567</code> formatida yozing)",
        reply_markup=get_phone_keyboard(),
        parse_mode="HTML"
    )

@router.message(DesignerApplyStates.waiting_for_phone)
async def designer_apply_phone(message: Message, state: FSMContext):
    if message.contact:
        raw_phone = message.contact.phone_number
    else:
        raw_phone = await require_text(
            message, "❌ Telefon raqamni tugma orqali yoki <code>+998901234567</code> ko'rinishida yuboring:"
        )
        if raw_phone is None:
            return

    phone = normalize_phone(raw_phone)
    if phone is None:
        await message.answer(
            "❌ Raqam noto'g'ri ko'rinadi. <code>+998901234567</code> shaklida yuboring:",
            parse_mode="HTML",
        )
        return

    await state.update_data(phoneNumber=phone)
    await state.set_state(DesignerApplyStates.waiting_for_specialty)
    from aiogram.types import KeyboardButton, ReplyKeyboardMarkup
    spec_kb = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="👕 Merch & Kiyimlar"), KeyboardButton(text="📇 Poligrafiya & Vizitkalar")],
            [KeyboardButton(text="☕ Suvenirlar & Krujkalar"), KeyboardButton(text="🎨 Badiiy Illyustratsiya")],
            [KeyboardButton(text="❌ Bekor qilish")]
        ],
        resize_keyboard=True
    )
    await message.answer(
        "3️⃣ <b>Asosiy dizayn yo'nalishingizni tanlang:</b>",
        reply_markup=spec_kb,
        parse_mode="HTML"
    )

@router.message(DesignerApplyStates.waiting_for_specialty)
async def designer_apply_specialty(message: Message, state: FSMContext):
    if message.text == "❌ Bekor qilish":
        await state.clear()
        await message.answer("Ariza bekor qilindi.", reply_markup=get_main_keyboard())
        return

    specialty = await require_text(message, "❌ Yo'nalishni tugmalardan tanlang yoki <b>matn</b> yozing:")
    if specialty is None:
        return
    await state.update_data(specialty=specialty)
    await state.set_state(DesignerApplyStates.waiting_for_portfolio)
    await message.answer(
        "4️⃣ <b>Portfolio, Behance, Instagram yoki ish namunalaringiz havolasini yuboring:</b>\n\n"
        "(Agar havola bo'lmasa, qisqacha tajribangizni yozib qoldiring)",
        reply_markup=get_main_keyboard(),
        parse_mode="HTML"
    )

@router.message(DesignerApplyStates.waiting_for_portfolio)
async def designer_apply_finish(message: Message, state: FSMContext):
    portfolio = await require_text(
        message, "❌ Portfolio havolasini yoki tajribangizni <b>matn</b> ko'rinishida yuboring:"
    )
    if portfolio is None:
        return
    data = await state.get_data()
    await state.clear()

    full_name = data.get("fullName", message.from_user.full_name or "Foydalanuvchi")
    phone = data.get("phoneNumber", "")
    specialty = data.get("specialty", "Umumiy")
    username = message.from_user.username or ""

    # Save to database
    req_id, saved = await db.add_designer_request({
        "userId": str(message.from_user.id),
        "fullName": full_name,
        "phoneNumber": phone,
        "telegramUsername": username,
        "specialty": specialty,
        "portfolioUrl": portfolio,
        "source": "telegram_bot"
    })

    # Send confirmation to user
    await message.answer(
        "🎉 <b>Arizangiz muvaffaqiyatli qabul qilindi!</b>\n\n"
        f"📋 <b>Ariza raqami:</b> <code>#{req_id}</code>\n"
        "Tez orada moderatorlarimiz portfoliosingizni ko'rib chiqib, siz bilan bog'lanishadi.",
        reply_markup=get_main_keyboard(),
        parse_mode="HTML"
    )

    # Firestore'ga yozilgan bo'lsa, adminlarga xabarni Cloud Functions
    # (`onDesignerRequestCreated`) yuboradi — bu yerdan yuborsak dublikat
    # bo'lardi. Faqat yozuv amalga oshmaganda zaxira xabar yuboramiz.
    if saved:
        return

    tg_display = f"@{html.escape(username)}" if username else "ko'rsatilmagan"
    await _notify_admins(
        "⚠️ <b>ZAXIRA XABAR — ariza bazaga YOZILMADI!</b>\n"
        "<i>Uni qo'lda ko'chirib oling, admin panelda ko'rinmaydi.</i>\n\n"
        "🎨 <b>YANGI DIZAYNERLIK ARIZASI!</b>\n\n"
        f"👤 <b>F.I.Sh:</b> {html.escape(full_name)}\n"
        f"📞 <b>Telefon:</b> <code>{html.escape(phone)}</code>\n"
        f"💬 <b>Telegram:</b> {tg_display}\n"
        f"🎯 <b>Yo'nalish:</b> {html.escape(specialty)}\n"
        f"🌐 <b>Portfolio:</b> {html.escape(portfolio)}\n\n"
        "🌐 <a href='https://asox.uz/app'>ASOX Admin Panel</a>"
    )

# ----------------------------------------------------
# 🛵 KURYER BO'LISH ARIZA OQIMI
# ----------------------------------------------------
@router.message(F.text.in_(["🛵 Kuryer bo'lish", "Kuryer bo'lish", "🛵 Kuryer"]))
async def courier_apply_start(message: Message, state: FSMContext):
    await state.clear()
    await state.set_state(CourierApplyStates.waiting_for_name)
    user_name = message.from_user.full_name or ""
    await message.answer(
        "🛵 <b>ASOX Market Kuryerlik Xizmatiga Qabul!</b>\n\n"
        "Samarqand shahri va boshqa hududlar bo'yicha tezkor yetkazib berish xizmati a'zosi bo'ling!\n\n"
        "1️⃣ <b>Ism va Familiyangizni kiriting:</b>\n"
        f"(Masalan: <i>{user_name or 'Otabek Fayzullayev'}</i>)",
        parse_mode="HTML"
    )

@router.message(CourierApplyStates.waiting_for_name)
async def courier_apply_name(message: Message, state: FSMContext):
    name = await require_text(message, "❌ Iltimos, ism va familiyangizni <b>matn</b> ko'rinishida yuboring:")
    if name is None:
        return  # holat saqlanadi — foydalanuvchi shu bosqichda qayta urinadi
    await state.update_data(fullName=name)
    await state.set_state(CourierApplyStates.waiting_for_phone)
    await message.answer(
        "2️⃣ <b>Bog'lanish uchun telefon raqamingizni yuboring:</b>\n\n"
        "(Pastdagi tugmani bosing yoki <code>+998939876543</code> formatida yozing)",
        reply_markup=get_phone_keyboard(),
        parse_mode="HTML"
    )

@router.message(CourierApplyStates.waiting_for_phone)
async def courier_apply_phone(message: Message, state: FSMContext):
    if message.contact:
        raw_phone = message.contact.phone_number
    else:
        raw_phone = await require_text(
            message, "❌ Telefon raqamni tugma orqali yoki <code>+998901234567</code> ko'rinishida yuboring:"
        )
        if raw_phone is None:
            return

    phone = normalize_phone(raw_phone)
    if phone is None:
        await message.answer(
            "❌ Raqam noto'g'ri ko'rinadi. <code>+998901234567</code> shaklida yuboring:",
            parse_mode="HTML",
        )
        return

    await state.update_data(phoneNumber=phone)
    await state.set_state(CourierApplyStates.waiting_for_city)
    from aiogram.types import KeyboardButton, ReplyKeyboardMarkup
    city_kb = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📍 Samarqand shahri"), KeyboardButton(text="📍 Samarqand Poligrafiyasi")],
            [KeyboardButton(text="📍 Toshkent shahri"), KeyboardButton(text="📍 Boshqa viloyat")],
            [KeyboardButton(text="❌ Bekor qilish")]
        ],
        resize_keyboard=True
    )
    await message.answer(
        "3️⃣ <b>Qaysi hududda yetkazib bera olasiz?</b>",
        reply_markup=city_kb,
        parse_mode="HTML"
    )

@router.message(CourierApplyStates.waiting_for_city)
async def courier_apply_city(message: Message, state: FSMContext):
    if message.text == "❌ Bekor qilish":
        await state.clear()
        await message.answer("Ariza bekor qilindi.", reply_markup=get_main_keyboard())
        return

    city_raw = await require_text(message, "❌ Hududni tugmalardan tanlang yoki <b>matn</b> yozing:")
    if city_raw is None:
        return
    city = city_raw.replace("📍", "").strip()
    await state.update_data(city=city)
    await state.set_state(CourierApplyStates.waiting_for_vehicle)
    from aiogram.types import KeyboardButton, ReplyKeyboardMarkup
    veh_kb = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🚗 Avtomobil"), KeyboardButton(text="🛵 Skuter / Moped")],
            [KeyboardButton(text="🚲 Velosiped"), KeyboardButton(text="🚶 Piyoda")],
            [KeyboardButton(text="❌ Bekor qilish")]
        ],
        resize_keyboard=True
    )
    await message.answer(
        "4️⃣ <b>Transport vositangiz turini tanlang:</b>",
        reply_markup=veh_kb,
        parse_mode="HTML"
    )

@router.message(CourierApplyStates.waiting_for_vehicle)
async def courier_apply_finish(message: Message, state: FSMContext):
    if message.text == "❌ Bekor qilish":
        await state.clear()
        await message.answer("Ariza bekor qilindi.", reply_markup=get_main_keyboard())
        return

    vehicle = await require_text(message, "❌ Transport turini tugmalardan tanlang yoki <b>matn</b> yozing:")
    if vehicle is None:
        return
    data = await state.get_data()
    await state.clear()

    full_name = data.get("fullName", message.from_user.full_name or "Foydalanuvchi")
    phone = data.get("phoneNumber", "")
    city = data.get("city", "Samarqand shahri")
    username = message.from_user.username or ""

    # Save to database
    req_id, saved = await db.add_courier_request({
        "userId": str(message.from_user.id),
        "fullName": full_name,
        "phoneNumber": phone,
        "telegramUsername": username,
        "city": city,
        "vehicle": vehicle,
        "source": "telegram_bot"
    })

    # Send confirmation to user
    await message.answer(
        "🎉 <b>Kuryerlik arizangiz muvaffaqiyatli qabul qilindi!</b>\n\n"
        f"📋 <b>Ariza raqami:</b> <code>#{req_id}</code>\n"
        "Tez orada operatorimiz siz bilan bog'lanib, shartlar va ish tartibini tushuntiradi.",
        reply_markup=get_main_keyboard(),
        parse_mode="HTML"
    )

    # Dizaynerdagi bilan bir xil sabab: CF `onCourierRequestCreated` allaqachon
    # yuboradi, bu yer faqat zaxira.
    if saved:
        return

    tg_display = f"@{html.escape(username)}" if username else "ko'rsatilmagan"
    await _notify_admins(
        "⚠️ <b>ZAXIRA XABAR — ariza bazaga YOZILMADI!</b>\n"
        "<i>Uni qo'lda ko'chirib oling, admin panelda ko'rinmaydi.</i>\n\n"
        "🛵 <b>YANGI KURYERLIK ARIZASI!</b>\n\n"
        f"👤 <b>F.I.Sh:</b> {html.escape(full_name)}\n"
        f"📞 <b>Telefon:</b> <code>{html.escape(phone)}</code>\n"
        f"💬 <b>Telegram:</b> {tg_display}\n"
        f"📍 <b>Hudud:</b> {html.escape(city)}\n"
        f"🚗 <b>Transport turi:</b> {html.escape(vehicle)}\n\n"
        "🌐 <a href='https://asox.uz/app'>ASOX Admin Panel</a>"
    )



