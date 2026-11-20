"""Barcha test to'plamlarini ishga tushiradi:  python3 tests/run_all.py"""
import asyncio
import sys

# _harness sys.path ga repo ildizini qo'shadi — test modullaridan OLDIN
# import qilinishi shart (nojo'ya ta'sir uchun).
import _harness  # noqa: F401
import test_admin_notifications
import test_courier
import test_courier_invite
import test_firestore_layer
import test_middleware
import test_normalization
import test_rendering

SUITES = [
    ("Normalizatsiya va xavfsizlik", test_normalization),
    ("Firestore qatlami", test_firestore_layer),
    ("Render, kirish tekshiruvi, xatoliklar", test_rendering),
    ("Throttling va telefon", test_middleware),
    ("Admin bildirishnomalari", test_admin_notifications),
    ("Kuryer dispetcheri", test_courier),
    ("Kuryer taklifi (do'kon paneli)", test_courier_invite),
]

def main() -> int:
    failures = 0
    for title, module in SUITES:
        print(f"\n{'#' * 46}\n#  {title}\n{'#' * 46}")
        failures += asyncio.run(module.main())
    print("\n🎉 Barcha to'plamlar o'tdi." if not failures else f"\n💥 {failures} ta to'plam yiqildi.")
    return failures

if __name__ == "__main__":
    sys.exit(main())
