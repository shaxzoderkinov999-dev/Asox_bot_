"""Kuryer zakasi bo'yicha qarorlar — I/O YO'Q.

Bu ajratish `functions/bts_shipment_guard.js` dagi bilan bir xil sababga
ega: qaror mantig'i Firestore'siz to'liq test qilinadi, tranzaksiya esa
faqat shu qarorni bajaradigan yupqa qobiq bo'lib qoladi.
"""


def claim_decision(order: dict | None) -> str:
    """Kuryer bu zakasni olishi mumkinmi.

    `"won"` — olishi mumkin; `"taken"` — kimdir ulgurgan;
    `"not_found"` — zakas yo'q.

    `courierStatus` maydoni umuman bo'lmasa ham `"taken"` qaytadi: bu zakas
    kuryer oqimiga kirmagan (masalan BTS jo'natmasi yoki filialdan olib
    ketish) degani, uni kuryer olib qo'ymasligi kerak.
    """
    if order is None:
        return "not_found"
    if order.get("courierStatus") != "searching":
        return "taken"
    return "won"


def advance_decision(order: dict | None, courier_id, expected: str) -> str:
    """Kuryer zakas holatini `expected` dan keyingisiga o'tkaza oladimi.

    `"ok"` / `"not_found"` / `"not_yours"` / `"wrong_state"`.

    `courierId` Firestore'da satr, aiogram esa `int` beradi — shu sababli
    taqqoslash ikkala tomonni satrga keltirib bajariladi.
    """
    if order is None:
        return "not_found"
    if str(order.get("courierId") or "") != str(courier_id):
        return "not_yours"
    if order.get("courierStatus") != expected:
        return "wrong_state"
    return "ok"
