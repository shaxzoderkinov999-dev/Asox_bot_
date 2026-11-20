from aiogram.types import KeyboardButton, ReplyKeyboardMarkup


def get_main_keyboard() -> ReplyKeyboardMarkup:
    buttons = [
        [KeyboardButton(text="🛍 Mahsulotlar Katalogi"), KeyboardButton(text="🔍 Qidiruv")],
        [KeyboardButton(text="🎨 Dizayner bo'lish"), KeyboardButton(text="🛵 Kuryer bo'lish")],
        [KeyboardButton(text="🌐 ASOX Market sayti")],
        [KeyboardButton(text="📞 Biz bilan bog'lanish"), KeyboardButton(text="👤 Profilim")]
    ]
    return ReplyKeyboardMarkup(
        keyboard=buttons,
        resize_keyboard=True,
        persistent=True
    )

def get_admin_keyboard() -> ReplyKeyboardMarkup:
    buttons = [
        [KeyboardButton(text="📦 Barcha buyurtmalar"), KeyboardButton(text="📊 Tizim statistikasi")],
        [KeyboardButton(text="🎨 Dizaynerlar arizalari"), KeyboardButton(text="🛵 Kuryerlar arizalari")],
        [KeyboardButton(text="👥 Kuryerlar ro'yxati")],
        [KeyboardButton(text="➕ Yangi mahsulot qo'shish"), KeyboardButton(text="📢 Xabar tarqatish")]
    ]
    return ReplyKeyboardMarkup(
        keyboard=buttons,
        resize_keyboard=True,
        persistent=True
    )

def get_phone_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📱 Telefon raqamni ulashish", request_contact=True)],
            [KeyboardButton(text="❌ Bekor qilish")]
        ],
        resize_keyboard=True,
        one_time_keyboard=True
    )
