"""Kuryer dispetcher oqimi.

Ishga tushirish:  python3 tests/run_all.py
"""
import asyncio
import sys

from _harness import Results  # sys.path ni sozlaydi
from fake_firestore import FakeFirestore

import database.db as dbmod

_r = Results()
check = _r.check

SEED = {
    "courier_requests": {
        "cou_1": {
            "userId": "555", "fullName": "Otabek Fayzullayev",
            "phoneNumber": "+998901234567", "telegramUsername": "otabek",
            "city": "Samarqand shahri", "vehicle": "Skuter / Moped",
            "status": "pending",
        },
        # userId yo'q — eski yoki buzilgan ariza
        "cou_bad": {"fullName": "Nomsiz", "status": "pending"},
    },
    "couriers": {},
    "orders": {},
}


async def main():
    fake = FakeFirestore(SEED)
    dbmod._db_client = fake
    db = dbmod.Database()

    print("\n[1] Ariza tasdiqlansa couriers hujjati yaratiladimi?")
    courier = await db.approve_courier_request("cou_1")
    check("kuryer qaytdi", courier is not None)
    check("id = telegramChatId", courier and courier["id"] == "555")
    check("couriers/555 yaratildi", "555" in fake.data["couriers"])
    doc = fake.data["couriers"].get("555", {})
    check("isActive = True", doc.get("isActive") is True)
    check("activeOrderId = None", doc.get("activeOrderId") is None)
    check("completedCount = 0", doc.get("completedCount") == 0)
    check("ism ko'chirildi", doc.get("fullName") == "Otabek Fayzullayev")
    check("telefon ko'chirildi", doc.get("phoneNumber") == "+998901234567")
    check("requestId saqlandi", doc.get("requestId") == "cou_1")
    check("ariza statusi approved",
          fake.data["courier_requests"]["cou_1"]["status"] == "approved")

    print("\n[1] Ikki marta tasdiqlash bitta hujjat qoldiradimi?")
    before = len(fake.data["couriers"])
    again = await db.approve_courier_request("cou_1")
    check("qayta tasdiqlash ishladi", again is not None)
    check("yangi hujjat yaratilmadi", len(fake.data["couriers"]) == before)

    print("\n[1] userId yo'q ariza — hujjat yaratilmaydi, status pending qoladi")
    bad = await db.approve_courier_request("cou_bad")
    check("None qaytdi", bad is None)
    check("couriers ga yozilmadi", len(fake.data["couriers"]) == before)
    check("ariza pending qoldi",
          fake.data["courier_requests"]["cou_bad"]["status"] == "pending")

    print("\n[1] Mavjud bo'lmagan ariza")
    check("None qaytdi", await db.approve_courier_request("yo-q") is None)

    print("\n[1] get_courier")
    found = await db.get_courier("555")
    check("topildi", found is not None and found["fullName"] == "Otabek Fayzullayev")
    check("id maydoni bor", found and found["id"] == "555")
    check("noma'lum chat_id -> None", await db.get_courier("999") is None)

    print("\n[2] Sof qaror mantig'i")
    from utils.courier_claim import advance_decision, claim_decision

    check("bo'sh zakas -> won", claim_decision({"courierStatus": "searching"}) == "won")
    check("olingan zakas -> taken", claim_decision({"courierStatus": "accepted"}) == "taken")
    check("yetkazilgan -> taken", claim_decision({"courierStatus": "delivered"}) == "taken")
    check("courierStatus yo'q -> taken", claim_decision({}) == "taken")
    check("zakas yo'q -> not_found", claim_decision(None) == "not_found")

    ORDER = {"courierId": "555", "courierStatus": "accepted"}
    check("o'z zakasi, to'g'ri holat -> ok",
          advance_decision(ORDER, "555", "accepted") == "ok")
    check("raqam/satr farqi muhim emas",
          advance_decision(ORDER, 555, "accepted") == "ok")
    check("begona kuryer -> not_yours",
          advance_decision(ORDER, "777", "accepted") == "not_yours")
    check("noto'g'ri holat -> wrong_state",
          advance_decision(ORDER, "555", "picked_up") == "wrong_state")
    check("zakas yo'q -> not_found",
          advance_decision(None, "555", "accepted") == "not_found")

    print("\n[3] Poyga: ikki kuryer bir zakasni olmoqchi")
    fake.data["orders"]["ORD-1"] = {
        "orderNumber": "1042", "courierStatus": "searching",
        "shopId": "shop_a", "telegramUserId": 900,
    }
    fake.data["couriers"]["777"] = {
        "fullName": "Jasur", "phoneNumber": "+998907654321",
        "telegramChatId": "777", "isActive": True, "activeOrderId": None,
        "completedCount": 0,
    }
    c555 = await db.get_courier("555")
    c777 = await db.get_courier("777")

    first = await db.claim_order_for_courier("ORD-1", c555)
    second = await db.claim_order_for_courier("ORD-1", c777)
    check("birinchi kuryer yutdi", first == "won")
    check("ikkinchi kuryer kechikdi", second == "taken")

    order = fake.data["orders"]["ORD-1"]
    check("zakas birinchi kuryerda", order["courierId"] == "555")
    check("ism yozildi", order["courierName"] == "Otabek Fayzullayev")
    check("telefon yozildi", order["courierPhone"] == "+998901234567")
    check("chat id yozildi", order["courierChatId"] == "555")
    check("holat accepted", order["courierStatus"] == "accepted")
    check("g'olibda activeOrderId bor",
          fake.data["couriers"]["555"]["activeOrderId"] == "ORD-1")
    check("yutqazganda activeOrderId bo'sh",
          fake.data["couriers"]["777"]["activeOrderId"] is None)

    check("mavjud bo'lmagan zakas -> not_found",
          await db.claim_order_for_courier("YO-Q", c555) == "not_found")

    print("\n[4] Holatni oldinga surish")
    check("begona kuryer rad etiladi",
          await db.advance_courier_order("ORD-1", "777", "accepted",
                                         "picked_up", "shipped") == "not_yours")
    check("o'z kuryeri o'tkazdi",
          await db.advance_courier_order("ORD-1", "555", "accepted",
                                         "picked_up", "shipped") == "ok")
    check("courierStatus picked_up", order["courierStatus"] == "picked_up")
    check("order status shipped", order["status"] == "shipped")
    check("takroriy bosish rad etiladi",
          await db.advance_courier_order("ORD-1", "555", "accepted",
                                         "picked_up", "shipped") == "wrong_state")

    check("yetkazildi",
          await db.advance_courier_order("ORD-1", "555", "picked_up",
                                         "delivered", "delivered") == "ok")
    check("kuryer bo'shadi",
          fake.data["couriers"]["555"]["activeOrderId"] is None)
    check("hisob oshdi", fake.data["couriers"]["555"]["completedCount"] == 1)

    print("\n[5] Qabul qilingan zakas matni")
    from handlers.courier import build_accepted_text, build_taken_text

    accepted = build_accepted_text({
        "orderNumber": "1042", "courierName": "Otabek",
        "customerName": "Ali Valiyev", "customerPhone": "+998901112233",
        "address": "Gagarin 45", "distanceKm": 5.2, "courierFee": 24600,
        "location": {"latitude": 39.69, "longitude": 66.96},
    })
    check("zakas raqami bor", "1042" in accepted)
    check("mijoz ismi KO'RINADI", "Ali Valiyev" in accepted)
    check("telefon ko'rinadi", "+998901112233" in accepted)
    check("xarita havolasi bor", "google.com/maps" in accepted)
    check("masofa va haq bor", "5.2" in accepted and "24 600" in accepted)

    no_geo = build_accepted_text({"orderNumber": "7", "address": "Chilonzor"})
    check("koordinatasiz ham qulamaydi", "7" in no_geo)
    check("koordinatasiz xarita havolasi yo'q", "google.com/maps" not in no_geo)

    taken = build_taken_text("1042", "Otabek")
    check("yutqazganga g'olib ismi ko'rinadi", "Otabek" in taken and "1042" in taken)

    print("\n[6] Bosqich matnlari")
    from handlers.courier import build_progress_text

    picked = build_progress_text({"orderNumber": "1042", "address": "Gagarin 45"}, "picked_up")
    check("olib ketildi matni", "1042" in picked and "yo'lda" in picked.lower())
    done = build_progress_text({"orderNumber": "1042", "courierFee": 24600}, "delivered")
    check("yetkazildi matni", "24 600" in done)

    print("\n[7] Admin: kuryerlar ro'yxati va o'chirish")
    couriers = await db.list_couriers()
    ids = [c["id"] for c in couriers]
    check("ikkala kuryer ham ro'yxatda", "555" in ids and "777" in ids)
    check("id maydoni bor", all("id" in c for c in couriers))

    check("o'chirish ishladi", await db.set_courier_active("777", False))
    check("isActive False bo'ldi", fake.data["couriers"]["777"]["isActive"] is False)
    check("qayta yoqish ishladi", await db.set_courier_active("777", True))
    check("isActive True bo'ldi", fake.data["couriers"]["777"]["isActive"] is True)
    check("mavjud bo'lmagan kuryer -> False",
          not await db.set_courier_active("yo-q", False))

    return _r.summary("Natija")


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
