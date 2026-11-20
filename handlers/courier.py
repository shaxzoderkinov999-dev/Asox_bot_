"""Kuryer tugmalari — user botida (@AsoxUz_Bot).

Taklif xabarini Cloud Function yuboradi (functions/courier_dispatch.js),
tugma bosilishi esa polling qilib turgan shu botga keladi. Bu taqsimot
loyihadagi onCourierRequestCreated + appr_cou_ juftligi bilan bir xil.

Kuryerlar Flutter ilovasiga kirmaydi — butun ish shu yerda.
"""
import html
import logging

from aiogram import Bot, F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from config import BOT_TOKEN
from database.db import db
from utils.notify import edit_message

logger = logging.getLogger(__name__)

router = Router()


def _esc(value) -> str:
    return html.escape(str(value or ""))


def _fmt_sum(value) -> str:
    try:
        return f"{int(float(value or 0)):,}".replace(",", " ")
    except (TypeError, ValueError):
        return "0"


def _maps_link(location) -> str | None:
    """Xarita havolasi. Koordinata yo'q yoki (0,0) bo'lsa `None`.

    (0,0) — Gvineya ko'rfazi, haqiqiy manzil emas; kuryerni okeanga
    yuborgandan ko'ra havolani umuman ko'rsatmagan afzal.
    """
    if not isinstance(location, dict):
        return None
    lat = location.get("latitude", location.get("lat"))
    lng = location.get("longitude", location.get("lng", location.get("lon")))
    try:
        lat, lng = float(lat), float(lng)
    except (TypeError, ValueError):
        return None
    if lat == 0 and lng == 0:
        return None
    return f"https://www.google.com/maps/search/?api=1&query={lat},{lng}"


def build_accepted_text(order: dict) -> str:
    """Zakasni qabul qilgan kuryer ko'radigan matn.

    Mijozning ismi va telefoni AYNAN shu yerda paydo bo'ladi — taklifda
    ular yo'q edi.
    """
    km = order.get("distanceKm")
    km_text = f"{km} km" if isinstance(km, (int, float)) else "aniqlanmadi"
    lines = [
        f"✅ <b>ZAKAS SIZDA — #{_esc(order.get('orderNumber') or order.get('id'))}</b>",
        "",
        f"🏁 <b>Manzil:</b> {_esc(order.get('address') or order.get('deliveryAddress'))}",
        f"👤 <b>Mijoz:</b>  {_esc(order.get('customerName') or order.get('userName'))}",
        f"📞 <b>Tel:</b>    <code>{_esc(order.get('customerPhone') or order.get('phone'))}</code>",
    ]
    link = _maps_link(order.get("location"))
    if link:
        lines.append(f'🗺 <a href="{link}">Xaritada ochish</a>')
    lines += [
        "",
        f"📏 {km_text}   💰 {_fmt_sum(order.get('courierFee'))} so'm",
    ]
    return "\n".join(lines)


def build_taken_text(order_number, winner_name) -> str:
    """Kechikkan kuryerlarning xabari — tugmasiz."""
    return (
        f"❌ <b>Zakas #{_esc(order_number)}</b>\n"
        f"Bu zakasni {_esc(winner_name) or 'boshqa kuryer'} oldi."
    )


def _pickup_keyboard(order_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="📦 Olib ketdim", callback_data=f"cour_pick:{order_id}")
    ]])


async def _close_other_offers(order: dict, winner_chat_id: str, winner_name: str) -> None:
    """G'olibdan boshqa barcha kuryerning taklif xabaridan tugmani olib
    tashlaydi.

    Xatolar yutiladi: kuryer xabarni o'chirgan bo'lishi mumkin va bu
    g'olibning oqimini to'xtatmasligi shart.
    """
    text = build_taken_text(order.get("orderNumber") or order.get("id"), winner_name)
    for entry in order.get("courierOfferMessages") or []:
        chat_id = str(entry.get("chatId") or "")
        message_id = entry.get("messageId")
        if not chat_id or not message_id or chat_id == str(winner_chat_id):
            continue
        await edit_message(BOT_TOKEN, chat_id, message_id, text)


@router.callback_query(F.data.startswith("cour_take:"))
async def courier_take_cb(callback: CallbackQuery):
    order_id = callback.data.split(":", 1)[1]
    chat_id = str(callback.from_user.id)

    courier = await db.get_courier(chat_id)
    if not courier or courier.get("isActive") is not True:
        await callback.answer(
            "⛔️ Siz kuryer sifatida tasdiqlanmagansiz.", show_alert=True
        )
        return

    result = await db.claim_order_for_courier(order_id, courier)

    if result == "taken":
        await callback.answer(
            "⏱ Kechikdingiz — zakasni boshqa kuryer oldi.", show_alert=True
        )
        # Xabarni ham yopamiz, aks holda tugma bosilaveradi.
        order = await db.get_order_raw(order_id)
        if order:
            await callback.message.edit_text(
                build_taken_text(order.get("orderNumber") or order_id,
                                 order.get("courierName")),
                parse_mode="HTML",
            )
        return

    if result != "won":
        await callback.answer("❌ Zakas topilmadi.", show_alert=True)
        return

    order = await db.get_order_raw(order_id)
    if order is None:
        await callback.answer("❌ Zakas topilmadi.", show_alert=True)
        return

    await callback.message.edit_text(
        build_accepted_text(order),
        reply_markup=_pickup_keyboard(order_id),
        parse_mode="HTML",
    )
    await callback.answer("Zakas sizniki! 🛵")

    await _close_other_offers(order, chat_id, courier.get("fullName"))
    logger.info("Zakas %s kuryer %s ga biriktirildi", order_id, chat_id)


def build_progress_text(order: dict, stage: str) -> str:
    """Kuryerning zakas kartochkasi — bosqichga qarab."""
    number = _esc(order.get("orderNumber") or order.get("id"))
    if stage == "picked_up":
        return (
            f"🛵 <b>Zakas #{number} — yo'lda</b>\n\n"
            f"🏁 <b>Manzil:</b> {_esc(order.get('address') or order.get('deliveryAddress'))}\n"
            f"📞 <b>Tel:</b>    <code>{_esc(order.get('customerPhone') or order.get('phone'))}</code>\n\n"
            "Yetkazgach pastdagi tugmani bosing."
        )
    return (
        f"✅ <b>Zakas #{number} yetkazildi!</b>\n\n"
        f"💰 Sizning ulushingiz: <b>{_fmt_sum(order.get('courierFee'))} so'm</b>\n"
        "Rahmat! Yangi zakaslar shu yerga keladi."
    )


def _done_keyboard(order_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Yetkazdim", callback_data=f"cour_done:{order_id}")
    ]])


# Kuryer tugmasi bosilganda mijozga ketadigan xabarlar. Mijozning Telegram
# ID si faqat botdan tushgan buyurtmalarda bor — saytdan tushganida yo'q,
# shuning uchun yuborish urinishi jimgina o'tkazib yuboriladi.
_CUSTOMER_TEXT = {
    "picked_up": (
        "🛵 <b>Kuryer yo'lga chiqdi!</b>\n\n"
        "Buyurtmangiz yetkazib berilmoqda."
    ),
    "delivered": (
        "✅ <b>Buyurtmangiz yetkazildi!</b>\n\n"
        "Xaridingiz uchun rahmat. ASOX Market 💜"
    ),
}


async def _notify_customer(bot: Bot, order: dict, stage: str) -> None:
    chat_id = order.get("telegramUserId") or order.get("user_id")
    if not chat_id:
        return
    try:
        await bot.send_message(
            chat_id=chat_id, text=_CUSTOMER_TEXT[stage], parse_mode="HTML"
        )
    except Exception as e:
        logger.info("Mijozga (%s) xabar bormadi: %s", chat_id, e)


async def _advance(
    callback: CallbackQuery,
    bot: Bot,
    expected: str,
    courier_status: str,
    order_status: str,
    keyboard: InlineKeyboardMarkup | None,
) -> None:
    """`cour_pick` va `cour_done` uchun umumiy oqim."""
    order_id = callback.data.split(":", 1)[1]
    courier_id = str(callback.from_user.id)

    result = await db.advance_courier_order(
        order_id, courier_id, expected, courier_status, order_status
    )

    if result == "not_yours":
        await callback.answer("⛔️ Bu zakas sizga biriktirilmagan.", show_alert=True)
        return
    if result == "wrong_state":
        await callback.answer("ℹ️ Bu bosqich allaqachon bajarilgan.", show_alert=True)
        return
    if result != "ok":
        await callback.answer("❌ Zakas topilmadi.", show_alert=True)
        return

    order = await db.get_order_raw(order_id) or {}
    await callback.message.edit_text(
        build_progress_text(order, courier_status),
        reply_markup=keyboard,
        parse_mode="HTML",
    )
    await callback.answer()
    await _notify_customer(bot, order, courier_status)
    logger.info("Zakas %s -> %s (kuryer %s)", order_id, courier_status, courier_id)


@router.callback_query(F.data.startswith("cour_pick:"))
async def courier_pick_cb(callback: CallbackQuery, bot: Bot):
    await _advance(
        callback, bot,
        expected="accepted", courier_status="picked_up", order_status="shipped",
        keyboard=_done_keyboard(callback.data.split(":", 1)[1]),
    )


@router.callback_query(F.data.startswith("cour_done:"))
async def courier_done_cb(callback: CallbackQuery, bot: Bot):
    await _advance(
        callback, bot,
        expected="picked_up", courier_status="delivered", order_status="delivered",
        keyboard=None,
    )
