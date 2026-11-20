"""Telegram Bot API ga to'g'ridan-to'g'ri HTTP chaqiruvlari.

NEGA aiogram `Bot` obyekti emas: xabar admin botdan kuryerga (user boti
orqali) yuborilishi kerak bo'lgan joylar bor — ya'ni handler ishlayotgan
botdan BOSHQA token bilan. Har safar `Bot` instansiyasi yaratib yopish
o'rniga bitta yengil HTTP qatlami.

Barcha xatolar yutiladi va `None`/`False` qaytariladi: bildirishnoma
yuborilmagani asosiy oqimni (tranzaksiya, holat o'zgarishi) to'xtatmasligi
shart.
"""
import logging

import aiohttp

logger = logging.getLogger(__name__)

_TIMEOUT = aiohttp.ClientTimeout(total=8)


async def _post(token: str, method: str, payload: dict) -> dict | None:
    if not token:
        logger.error("Telegram %s: token yo'q — xabar yuborilmadi", method)
        return None
    url = f"https://api.telegram.org/bot{token}/{method}"
    try:
        async with aiohttp.ClientSession(timeout=_TIMEOUT) as session:
            async with session.post(url, json=payload) as resp:
                body = await resp.json()
                if not body.get("ok"):
                    logger.warning(
                        "Telegram %s rad etdi: %s", method, body.get("description")
                    )
                    return None
                return body.get("result")
    except Exception as e:
        logger.warning("Telegram %s xatosi: %s", method, e)
        return None


async def send_message(
    token: str, chat_id: str | int, text: str, reply_markup: dict | None = None
) -> int | None:
    """Xabar yuboradi. Yuborilgan xabarning `message_id` sini qaytaradi.

    `message_id` MUHIM: taklif yuborilganda uni saqlab qo'yamiz, keyin
    g'olib aniqlangach qolgan kuryerlarning tugmasini o'chirish uchun
    aynan shu ID kerak bo'ladi.
    """
    payload = {
        "chat_id": str(chat_id),
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup
    result = await _post(token, "sendMessage", payload)
    return result.get("message_id") if result else None


async def edit_message(
    token: str,
    chat_id: str | int,
    message_id: int,
    text: str,
    reply_markup: dict | None = None,
) -> bool:
    """Mavjud xabarni almashtiradi. Tugmani olib tashlash uchun
    `reply_markup` berilmaydi.

    Kuryer xabarni o'chirgan bo'lishi mumkin — bunday holatda Telegram xato
    qaytaradi va biz `False` bilan davom etamiz.
    """
    payload = {
        "chat_id": str(chat_id),
        "message_id": int(message_id),
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup
    return await _post(token, "editMessageText", payload) is not None
