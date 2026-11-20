"""Do'kon admin paneli qo'shgan kuryerni Telegram chatiga bog'lash.

`/start cour_<token>` — havolani `functions/shop_couriers.js` yaratadi, bu yerda
u kuryerga aylantiriladi.

Ishga tushirish:  python3 tests/run_all.py
"""
import asyncio
from datetime import datetime, timedelta, timezone

from _harness import Results  # sys.path ni sozlaydi
from fake_firestore import FakeFirestore

import database.db as dbmod

_r = Results()
check = _r.check

KELAJAK = datetime.now(timezone.utc) + timedelta(days=7)
O_TGAN = datetime.now(timezone.utc) - timedelta(minutes=1)


def seed():
    return {
        "courier_invites": {
            "TOK_OK": {
                "shopId": "s1", "shopName": "Aloqa Print", "mirrorDocId": "m1",
                "fullName": "Otabek", "phoneNumber": "+998901234567",
                "phoneE164": "+998901234567", "vehicleType": "scooter",
                "city": "Samarqand", "expiresAt": KELAJAK,
            },
            "TOK_MUDDAT": {
                "shopId": "s1", "mirrorDocId": "m2", "fullName": "Kech qolgan",
                "expiresAt": O_TGAN,
            },
            "TOK_ISHLATILGAN": {
                "shopId": "s1", "mirrorDocId": "m3", "fullName": "Takror",
                "expiresAt": KELAJAK, "usedAt": "allaqachon", "usedBy": "999",
            },
            "TOK_SHOPSIZ": {"mirrorDocId": "m4", "expiresAt": KELAJAK},
        },
        "couriers": {},
        "shops/s1/couriers": {
            "m1": {"fullName": "Otabek", "status": "invited", "courierId": None},
        },
    }


async def main():
    fake = FakeFirestore(seed())
    dbmod._db_client = fake
    db = dbmod.Database()

    print("\n[1] To'g'ri taklif kuryerga aylanadimi?")
    out = await db.redeem_courier_invite("TOK_OK", 555, "otabek")
    check("xato yo'q", out.get("error") is None)
    check("do'kon nomi qaytdi", out.get("shopName") == "Aloqa Print")

    courier = fake.data["couriers"].get("555", {})
    check("couriers/555 yaratildi", bool(courier))
    check("telegramChatId yozildi", courier.get("telegramChatId") == "555")
    check("telefon taklifdan olindi", courier.get("phoneE164") == "+998901234567")
    check("isActive = True", courier.get("isActive") is True)
    # `source: 'shop'` bo'lmasa, do'kon kuryerni chiqarganda u bo'sh `shopIds`
    # tufayli BARCHA do'konlarning zaxira zakaslarini olardi.
    check("source = shop", courier.get("source") == "shop")
    shop_ids = courier.get("shopIds")
    check("shopIds ga do'kon qo'shildi", getattr(shop_ids, "values", None) == ["s1"])

    print("\n[2] Do'kon paneli ko'zgusi yangilandimi?")
    mirror = fake.data["shops/s1/couriers"].get("m1", {})
    check("status = active", mirror.get("status") == "active")
    check("courierId bog'landi", mirror.get("courierId") == "555")

    print("\n[3] Taklif bir martalikmi?")
    invite = fake.data["courier_invites"]["TOK_OK"]
    check("usedBy yozildi", invite.get("usedBy") == "555")
    again = await db.redeem_courier_invite("TOK_OK", 777, "boshqa")
    check("ikkinchi marta rad etiladi", again.get("error") == "used")
    check("boshqa odam kuryer bo'lmadi", "777" not in fake.data["couriers"])

    print("\n[4] Yaroqsiz takliflar rad etiladimi?")
    check(
        "noma'lum token",
        (await db.redeem_courier_invite("YO_Q", 1, None)).get("error") == "not_found",
    )
    check(
        "muddati o'tgan",
        (await db.redeem_courier_invite("TOK_MUDDAT", 2, None)).get("error") == "expired",
    )
    check(
        "allaqachon ishlatilgan",
        (await db.redeem_courier_invite("TOK_ISHLATILGAN", 3, None)).get("error") == "used",
    )
    check(
        "shopId siz taklif",
        (await db.redeem_courier_invite("TOK_SHOPSIZ", 4, None)).get("error") == "not_found",
    )
    check("rad etilganlarda kuryer yaratilmadi", set(fake.data["couriers"]) == {"555"})

    print("\n[5] Mavjud platforma kuryeri do'konga qulflanmaydimi?")
    # Markaziy admin tasdiqlagan kuryer do'kon havolasini ochsa (telefoni
    # boshqacha yozilgani uchun panel uni topa olmagan bo'lishi mumkin), uning
    # `source` i o'zgarmasligi kerak — aks holda u boshqa do'konlarning
    # zakaslaridan mahrum bo'lardi.
    fake2 = FakeFirestore(seed())
    fake2.data["couriers"]["555"] = {
        "fullName": "Otabek", "telegramChatId": "555", "isActive": True,
    }
    dbmod._db_client = fake2
    db2 = dbmod.Database()
    await db2.redeem_courier_invite("TOK_OK", 555, "otabek")
    check("source yozilmadi", "source" not in fake2.data["couriers"]["555"])
    check(
        "do'kon baribir qo'shildi",
        getattr(fake2.data["couriers"]["555"].get("shopIds"), "values", None) == ["s1"],
    )

    print("\n[6] Firestore yo'q bo'lsa jimgina yiqilmaydimi?")
    dbmod._db_client = None
    check(
        "unavailable qaytadi",
        (await dbmod.Database().redeem_courier_invite("TOK_OK", 5, None)).get("error")
        == "unavailable",
    )
    dbmod._db_client = fake

    return _r.summary("Kuryer taklifi")


if __name__ == "__main__":
    asyncio.run(main())
