import asyncio
import logging

from aiogram import Bot, F, Router
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from config import BOT_TOKEN, is_admin_user
from database.db import db
from keyboards.inline import get_admin_order_keyboard, get_categories_keyboard
from keyboards.reply import get_admin_keyboard
from states.states import AdminStates
from utils.formatters import esc, format_price, get_order_status_badge
from utils.notify import send_message
from utils.validators import require_text

logger = logging.getLogger(__name__)

router = Router()

# Telegram limiti ~30 xabar/sekund. 20/s bilan ishlaymiz — bot bloklanmasin.
BROADCAST_DELAY_SECONDS = 0.05
BROADCAST_PROGRESS_EVERY = 25

def get_admin_panel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📦 Barcha buyurtmalar", callback_data="admin_view_orders")],
            [InlineKeyboardButton(text="🎨 Dizaynerlar arizalari", callback_data="admin_view_designers")],
            [InlineKeyboardButton(text="🛵 Kuryerlar arizalari", callback_data="admin_view_couriers")],
            [InlineKeyboardButton(text="👥 Kuryerlar ro'yxati", callback_data="admin_view_courier_list")],
            [InlineKeyboardButton(text="➕ Yangi mahsulot qo'shish", callback_data="admin_add_product")],
            [InlineKeyboardButton(text="📊 Tizim statistikasi", callback_data="admin_stats")],
            [InlineKeyboardButton(text="📢 Xabar tarqatish (Broadcast)", callback_data="admin_broadcast")]
        ]
    )

@router.message(CommandStart())
async def admin_cmd_start(message: Message, state: FSMContext):
    await state.clear()
    user_id = message.from_user.id
    username = message.from_user.username
    full_name = message.from_user.full_name or "Admin"

    if not is_admin_user(user_id, username):
        await message.answer("⛔️ <b>Kechirasiz, bu bot faqat ASOX Market ma'murlari (admin) uchun mo'ljallangan.</b>", parse_mode="HTML")
        return

    welcome_text = (
        f"👑 <b>Assalomu alaykum, {full_name}!</b>\n\n"
        f"🛠 <b>ASOX Market Admin Boshqaruv Boti</b>ga xush kelibsiz.\n\n"
        f"Quyidagi bo'limlar orqali buyurtmalarni boshqarishingiz, statistika ko'rishingiz va yangi mahsulotlar qo'shishingiz mumkin:"
    )
    await message.answer(welcome_text, reply_markup=get_admin_keyboard(), parse_mode="HTML")
    await message.answer("⚙️ <b>Boshqaruv paneli:</b>", reply_markup=get_admin_panel_keyboard(), parse_mode="HTML")

@router.message(F.text.in_(["⚙️ Admin Panel", "🛠 Boshqaruv"]))
async def show_admin_panel(message: Message):
    if not is_admin_user(message.from_user.id, message.from_user.username):
        await message.answer("⛔️ Kechirasiz, sizda admin huquqlari yo'q.")
        return

    await message.answer("⚙️ <b>ASOX Market Admin Paneli</b>", reply_markup=get_admin_panel_keyboard(), parse_mode="HTML")

@router.message(F.text == "📊 Tizim statistikasi")
async def admin_stats_text(message: Message):
    if not is_admin_user(message.from_user.id, message.from_user.username):
        return
    users_count = await db.get_users_count()
    all_orders = await db.get_all_orders()
    total_revenue = sum(o.get("total_amount", 0) for o in all_orders if o.get("status") == "delivered")
    stats_text = (
        f"📊 <b>ASOX Market Statistikasi</b>\n\n"
        f"👥 <b>Foydalanuvchilar soni:</b> {users_count} ta\n"
        f"📦 <b>Jami buyurtmalar:</b> {len(all_orders)} ta\n"
        f"💰 <b>Jami tushum (Bajarilgan):</b> {format_price(total_revenue)}"
    )
    await message.answer(stats_text, reply_markup=get_admin_panel_keyboard(), parse_mode="HTML")

@router.message(F.text == "📦 Barcha buyurtmalar")
async def admin_view_orders_text(message: Message):
    if not is_admin_user(message.from_user.id, message.from_user.username):
        return
    orders = await db.get_all_orders()
    if not orders:
        await message.answer("Hozircha buyurtmalar yo'q.")
        return
    for order in orders[:5]:
        items_summary = "".join(
            f"• {esc(i.get('name', 'Tovar'))} ({i.get('quantity', 1)}x)\n" for i in order.get("items", [])
        )
        text = (
            f"🆔 <b>Order #{esc(order.get('id', ''))}</b> ({esc(order.get('created_at', ''))})\n"
            f"👤 <b>Mijoz:</b> {esc(order.get('user_name', ''))} (<code>{order.get('user_id', '')}</code>)\n"
            f"📱 <b>Tel:</b> <code>{esc(order.get('phone_number', ''))}</code>\n"
            f"📍 <b>Manzil:</b> {esc(order.get('address', ''))}\n"
            f"💰 <b>Summa:</b> {format_price(order.get('total_amount', 0))}\n"
            f"📊 <b>Holati:</b> {get_order_status_badge(order.get('status', 'pending'))}\n\n"
            f"📦 <b>Mahsulotlar:</b>\n{items_summary}"
        )
        await message.answer(
            text,
            reply_markup=get_admin_order_keyboard(order.get('id', ''), order.get('status', 'pending')),
            parse_mode="HTML"
        )

@router.message(F.text == "➕ Yangi mahsulot qo'shish")
async def admin_add_product_text(message: Message, state: FSMContext):
    if not is_admin_user(message.from_user.id, message.from_user.username):
        return
    await state.set_state(AdminStates.waiting_for_product_name)
    await message.answer("📝 <b>Yangi mahsulot nomini kiriting:</b>", parse_mode="HTML")

@router.message(F.text == "📢 Xabar tarqatish")
async def admin_broadcast_text(message: Message, state: FSMContext):
    if not is_admin_user(message.from_user.id, message.from_user.username):
        return
    await state.set_state(AdminStates.waiting_for_broadcast_text)
    await message.answer(
        "📢 <b>Barcha foydalanuvchilarga yuboriladigan xabarni yuboring:</b>\n\n"
        "(Matn, rasm yoki media yuborishingiz mumkin. Bekor qilish uchun ❌ Bekor qilish tugmasini bosing)",
        parse_mode="HTML"
    )

@router.callback_query(F.data == "admin_stats")
async def admin_stats_cb(callback: CallbackQuery):
    if not is_admin_user(callback.from_user.id, callback.from_user.username):
        return

    users_count = await db.get_users_count()
    all_orders = await db.get_all_orders()
    total_revenue = sum(o.get("total_amount", 0) for o in all_orders if o.get("status") == "delivered")

    stats_text = (
        f"📊 <b>ASOX Market Statistikasi</b>\n\n"
        f"👥 <b>Foydalanuvchilar soni:</b> {users_count} ta\n"
        f"📦 <b>Jami buyurtmalar:</b> {len(all_orders)} ta\n"
        f"💰 <b>Jami tushum (Bajarilgan):</b> {format_price(total_revenue)}"
    )
    await callback.message.edit_text(stats_text, reply_markup=get_admin_panel_keyboard(), parse_mode="HTML")
    await callback.answer()

@router.callback_query(F.data == "admin_view_orders")
async def admin_view_orders_cb(callback: CallbackQuery):
    if not is_admin_user(callback.from_user.id, callback.from_user.username):
        return

    orders = await db.get_all_orders()
    if not orders:
        await callback.answer("Hozircha buyurtmalar yo'q.", show_alert=True)
        return

    await callback.answer()
    for order in orders[:5]: # Show latest 5
        items_summary = "".join(
            f"• {esc(i.get('name', 'Tovar'))} ({i.get('quantity', 1)}x)\n" for i in order.get("items", [])
        )
        text = (
            f"🆔 <b>Order #{esc(order.get('id', ''))}</b> ({esc(order.get('created_at', ''))})\n"
            f"👤 <b>Mijoz:</b> {esc(order.get('user_name', ''))} (<code>{order.get('user_id', '')}</code>)\n"
            f"📱 <b>Tel:</b> <code>{esc(order.get('phone_number', ''))}</code>\n"
            f"📍 <b>Manzil:</b> {esc(order.get('address', ''))}\n"
            f"💰 <b>Summa:</b> {format_price(order.get('total_amount', 0))}\n"
            f"📊 <b>Holati:</b> {get_order_status_badge(order.get('status', 'pending'))}\n\n"
            f"📦 <b>Mahsulotlar:</b>\n{items_summary}"
        )
        await callback.message.answer(
            text,
            reply_markup=get_admin_order_keyboard(order.get('id', ''), order.get('status', 'pending')),
            parse_mode="HTML"
        )

@router.callback_query(F.data.startswith("adm_ord:"))
async def admin_change_order_status(callback: CallbackQuery, bot: Bot):
    if not is_admin_user(callback.from_user.id, callback.from_user.username):
        return

    _, order_id, new_status = callback.data.split(":")
    success = await db.update_order_status(order_id, new_status)

    if success:
        order = await db.get_order_by_id(order_id)
        status_badge = get_order_status_badge(new_status)
        await callback.answer(f"Buyurtma #{order_id} holati o'zgartirildi: {status_badge}")

        # Update user about status change
        if order:
            try:
                await bot.send_message(
                    chat_id=order["user_id"],
                    text=(
                        f"🔔 <b>Buyurtmangiz holati o'zgardi!</b>\n\n"
                        f"Buyurtma ID: <code>{esc(order_id)}</code>\n"
                        f"Yangi holat: <b>{status_badge}</b>"
                    ),
                    parse_mode="HTML"
                )
            except Exception:
                pass
    else:
        await callback.answer("Buyurtma topilmadi", show_alert=True)

# Add product flow
@router.callback_query(F.data == "admin_add_product")
async def admin_add_product_start(callback: CallbackQuery, state: FSMContext):
    if not is_admin_user(callback.from_user.id, callback.from_user.username):
        return

    await state.set_state(AdminStates.waiting_for_product_name)
    await callback.message.answer("📝 <b>Yangi mahsulot nomini kiriting:</b>", parse_mode="HTML")
    await callback.answer()

@router.message(AdminStates.waiting_for_product_name)
async def process_prod_name(message: Message, state: FSMContext):
    name = await require_text(message, "❌ Mahsulot nomini <b>matn</b> ko'rinishida yuboring:")
    if name is None:
        return
    await state.update_data(name=name)
    await state.set_state(AdminStates.waiting_for_product_price)
    await message.answer("💰 <b>Mahsulot narxini kiriting</b> (faqat raqam, masalan: 1500000):", parse_mode="HTML")

@router.message(AdminStates.waiting_for_product_price)
async def process_prod_price(message: Message, state: FSMContext):
    price_text = await require_text(message, "❌ Narxni <b>raqam</b> ko'rinishida yuboring:")
    if price_text is None:
        return
    # bo'sh joy va ajratgichlarga toqat qilamiz: "1 500 000" / "1,500,000"
    digits = price_text.replace(" ", "").replace(",", "").replace("_", "")
    if not digits.isdigit():
        await message.answer("❌ Iltimos, faqat raqam kiriting (masalan: 1500000):")
        return

    await state.update_data(price=int(digits))
    await state.set_state(AdminStates.waiting_for_product_description)
    await message.answer("📝 <b>Mahsulot haqida qisqacha tavsif kiriting:</b>", parse_mode="HTML")

@router.message(AdminStates.waiting_for_product_description)
async def process_prod_desc(message: Message, state: FSMContext):
    description = await require_text(message, "❌ Tavsifni <b>matn</b> ko'rinishida yuboring:")
    if description is None:
        return
    await state.update_data(description=description)
    categories = await db.get_categories()
    await state.set_state(AdminStates.waiting_for_product_category)
    await message.answer("📂 <b>Mahsulot qaysi kategoriyaga tegishli?</b>", reply_markup=get_categories_keyboard(categories), parse_mode="HTML")

@router.callback_query(AdminStates.waiting_for_product_category, F.data.startswith("cat:"))
async def process_prod_cat(callback: CallbackQuery, state: FSMContext):
    cat_id = callback.data.split(":")[1]
    await state.update_data(category_id=cat_id)

    await state.set_state(AdminStates.waiting_for_product_image)
    await callback.message.answer(
        "🖼 <b>Mahsulot rasmini yuboring</b> (fayl yoki rasm ko'rinishida)\n"
        "yoki standart rasm ishlatish uchun <code>otish</code> deb yozing:",
        parse_mode="HTML"
    )
    await callback.answer()

@router.message(AdminStates.waiting_for_product_image)
async def process_prod_image(message: Message, state: FSMContext):
    image_url = "https://images.unsplash.com/photo-1511707171634-5f897ff02aa9?q=80&w=800"
    if message.photo:
        image_url = message.photo[-1].file_id

    data = await state.get_data()
    new_prod = await db.add_product({
        "category_id": data.get("category_id", ""),
        "name": data.get("name", "Mahsulot"),
        "price": data.get("price", 0),
        "description": data.get("description", ""),
        "image_url": image_url
    })

    await state.clear()
    await message.answer(
        f"✅ <b>Yangi mahsulot qo'shildi!</b>\n\n"
        f"ID: <code>{esc(new_prod['id'])}</code>\n"
        f"Nomi: <b>{esc(new_prod['name'])}</b>\n"
        f"Narxi: {format_price(new_prod['price'])}",
        parse_mode="HTML"
    )

# Broadcast functionality
@router.callback_query(F.data == "admin_broadcast")
async def admin_broadcast_start(callback: CallbackQuery, state: FSMContext):
    if not is_admin_user(callback.from_user.id, callback.from_user.username):
        return

    await state.set_state(AdminStates.waiting_for_broadcast_text)
    await callback.message.answer(
        "📢 <b>Barcha foydalanuvchilarga yuboriladigan xabarni yuboring:</b>\n\n"
        "(Matn, rasm yoki media yuborishingiz mumkin. Bekor qilish uchun ❌ Bekor qilish tugmasini bosing)",
        parse_mode="HTML"
    )
    await callback.answer()

@router.message(AdminStates.waiting_for_broadcast_text)
async def process_broadcast_message(message: Message, state: FSMContext, bot: Bot):
    if message.text == "❌ Bekor qilish":
        await state.clear()
        await message.answer("❌ Xabar tarqatish bekor qilindi.")
        return

    # Auditoriya Firestore `bot_users` dan olinadi — xotiradagi ro'yxat
    # bot qayta ishga tushganda bo'shab qolardi.
    users = await db.get_broadcast_user_ids()
    await state.clear()

    if not users:
        await message.answer("⚠️ Xabar tarqatish uchun foydalanuvchilar ro'yxati bo'sh.")
        return

    count_success = 0
    count_fail = 0
    total = len(users)
    status_msg = await message.answer(f"⏳ Xabar yuborilmoqda... (0/{total})")

    for index, user_id in enumerate(users, 1):
        try:
            await message.copy_to(chat_id=user_id)
            count_success += 1
        except Exception:
            count_fail += 1
        # Telegram flood limitiga urilmaslik uchun
        await asyncio.sleep(BROADCAST_DELAY_SECONDS)
        if index % BROADCAST_PROGRESS_EVERY == 0:
            try:
                await status_msg.edit_text(f"⏳ Xabar yuborilmoqda... ({index}/{total})")
            except Exception:
                pass

    logger.info("Broadcast yakunlandi: %s ta yuborildi, %s ta xato", count_success, count_fail)
    try:
        await status_msg.edit_text(
            f"✅ <b>Xabarnoma yakunlandi!</b>\n\n"
            f"👥 Auditoriya: {total} ta\n"
            f"🟢 Yuborildi: {count_success} ta\n"
            f"🔴 Xatolik (Bloklangan): {count_fail} ta",
            parse_mode="HTML"
        )
    except Exception:
        pass

# 🎨 Dizaynerlar arizalari
@router.message(F.text == "🎨 Dizaynerlar arizalari")
@router.callback_query(F.data == "admin_view_designers")
async def admin_view_designers_handler(event: Message | CallbackQuery):
    user_id = event.from_user.id
    username = event.from_user.username
    if not is_admin_user(user_id, username):
        return

    msg = event if isinstance(event, Message) else event.message
    if isinstance(event, CallbackQuery):
        await event.answer()

    requests = await db.get_designer_requests()
    if not requests:
        await msg.answer("🎨 <b>Hozircha yangi dizaynerlik arizalari yo'q.</b>", parse_mode="HTML")
        return

    await msg.answer(f"🎨 <b>Jami dizaynerlik arizalari ({len(requests)} ta):</b>", parse_mode="HTML")
    for req in requests[:10]:
        status_badge = "⏳ Ko'rib chiqilmoqda" if req.get("status") == "pending" else ("✅ Tasdiqlangan" if req.get("status") == "approved" else "❌ Rad etilgan")
        portfolio_url = esc(req.get("portfolioUrl"))
        portfolio = f"<a href='{portfolio_url}'>{portfolio_url}</a>" if portfolio_url else "Ko'rsatilmagan"
        tg = f"@{esc(req.get('telegramUsername'))}" if req.get("telegramUsername") else "Ko'rsatilmagan"
        full_name = esc(req.get("fullName") or "Noma'lum")
        phone = esc(req.get("phoneNumber") or "")
        specialty = esc(req.get("specialty") or "Umumiy")
        text = (
            f"🎨 <b>Dizayner: {full_name}</b>\n"
            f"📞 <b>Tel:</b> <code>{phone}</code>\n"
            f"💬 <b>Telegram:</b> {tg}\n"
            f"🎯 <b>Yo'nalish:</b> {specialty}\n"
            f"🌐 <b>Portfolio:</b> {portfolio}\n"
            f"📊 <b>Holati:</b> {status_badge}"
        )
        buttons = []
        if req.get("status") == "pending":
            buttons.append([
                InlineKeyboardButton(text="✅ Tasdiqlash", callback_data=f"appr_des_{req.get('id')}"),
                InlineKeyboardButton(text="❌ Rad etish", callback_data=f"rejc_des_{req.get('id')}")
            ])
        await msg.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons) if buttons else None, parse_mode="HTML")

# 🛵 Samarqand kuryerlari arizalari
@router.message(F.text == "🛵 Kuryerlar arizalari")
@router.callback_query(F.data == "admin_view_couriers")
async def admin_view_couriers_handler(event: Message | CallbackQuery):
    user_id = event.from_user.id
    username = event.from_user.username
    if not is_admin_user(user_id, username):
        return

    msg = event if isinstance(event, Message) else event.message
    if isinstance(event, CallbackQuery):
        await event.answer()

    requests = await db.get_courier_requests()
    if not requests:
        await msg.answer("🛵 <b>Hozircha yangi kuryerlik arizalari yo'q.</b>", parse_mode="HTML")
        return

    await msg.answer(f"🛵 <b>Jami Samarqand kuryerlik arizalari ({len(requests)} ta):</b>", parse_mode="HTML")
    for req in requests[:10]:
        status_badge = "⏳ Ko'rib chiqilmoqda" if req.get("status") == "pending" else ("✅ Tasdiqlangan" if req.get("status") == "approved" else "❌ Rad etilgan")
        tg = f"@{esc(req.get('telegramUsername'))}" if req.get("telegramUsername") else "Ko'rsatilmagan"
        full_name = esc(req.get("fullName") or "Noma'lum")
        phone = esc(req.get("phoneNumber") or "")
        city = esc(req.get("city") or "Samarqand")
        vehicle = esc(req.get("vehicle") or "Skuter / Moped")
        text = (
            f"🛵 <b>Kuryer: {full_name}</b>\n"
            f"📞 <b>Tel:</b> <code>{phone}</code>\n"
            f"💬 <b>Telegram:</b> {tg}\n"
            f"📍 <b>Hudud:</b> {city}\n"
            f"🚗 <b>Transport:</b> {vehicle}\n"
            f"📊 <b>Holati:</b> {status_badge}"
        )
        buttons = []
        if req.get("status") == "pending":
            buttons.append([
                InlineKeyboardButton(text="✅ Kuryerlikka qo'shish", callback_data=f"appr_cou_{req.get('id')}"),
                InlineKeyboardButton(text="❌ Rad etish", callback_data=f"rejc_cou_{req.get('id')}")
            ])
        await msg.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons) if buttons else None, parse_mode="HTML")

# Action callbacks
@router.callback_query(F.data.startswith("appr_des_"))
async def approve_designer_cb(callback: CallbackQuery):
    if not is_admin_user(callback.from_user.id, callback.from_user.username):
        await callback.answer("⛔️ Sizda admin huquqlari yo'q.", show_alert=True)
        return

    req_id = callback.data.replace("appr_des_", "")
    await db.update_designer_request_status(req_id, "approved")
    await callback.message.edit_text(callback.message.html_text + "\n\n✅ <b>Dizayner arizasi tasdiqlandi!</b>", parse_mode="HTML")
    await callback.answer("Dizayner tasdiqlandi!")

@router.callback_query(F.data.startswith("rejc_des_"))
async def reject_designer_cb(callback: CallbackQuery):
    if not is_admin_user(callback.from_user.id, callback.from_user.username):
        await callback.answer("⛔️ Sizda admin huquqlari yo'q.", show_alert=True)
        return

    req_id = callback.data.replace("rejc_des_", "")
    await db.update_designer_request_status(req_id, "rejected")
    await callback.message.edit_text(callback.message.html_text + "\n\n❌ <b>Ariza rad etildi.</b>", parse_mode="HTML")
    await callback.answer("Ariza rad etildi.")

@router.callback_query(F.data.startswith("appr_cou_"))
async def approve_courier_cb(callback: CallbackQuery):
    if not is_admin_user(callback.from_user.id, callback.from_user.username):
        await callback.answer("⛔️ Sizda admin huquqlari yo'q.", show_alert=True)
        return

    req_id = callback.data.replace("appr_cou_", "")
    # Ilgari bu yer faqat `status` maydonini "approved" qilardi — kuryer
    # akkaunti, roli va chat bog'lanishi yaratilmasdi, natijada tasdiqlangan
    # kuryer hech qachon zakas olmasdi. Endi `couriers` hujjati yaratiladi.
    courier = await db.approve_courier_request(req_id)

    if courier is None:
        # Ariza statusi ataylab o'zgartirilmadi — qayta urinib ko'rish mumkin.
        await callback.answer(
            "⚠️ Kuryer yaratilmadi. Arizada Telegram ID bo'lmasligi mumkin — "
            "loglarni tekshiring.",
            show_alert=True,
        )
        return

    await callback.message.edit_text(
        callback.message.html_text + "\n\n✅ <b>Kuryer arizasi tasdiqlandi!</b>",
        parse_mode="HTML",
    )
    await callback.answer("Kuryer tasdiqlandi!")

    # Kuryerga xabar USER boti orqali ketadi: u arizani o'sha yerda topshirgan
    # va zakas takliflari ham o'sha yerga keladi. Admin botiga yozsak kuryer
    # umuman ko'rmaydi.
    await send_message(
        BOT_TOKEN,
        courier["id"],
        "🎉 <b>Tabriklaymiz! Kuryerlik arizangiz tasdiqlandi.</b>\n\n"
        "Endi yangi zakaslar shu chatga keladi. Zakas ma'qul bo'lsa "
        "<b>✅ Qabul qildim</b> tugmasini bosing — kim birinchi bossa, "
        "zakas o'shaniki bo'ladi.",
    )

@router.callback_query(F.data.startswith("rejc_cou_"))
async def reject_courier_cb(callback: CallbackQuery):
    if not is_admin_user(callback.from_user.id, callback.from_user.username):
        await callback.answer("⛔️ Sizda admin huquqlari yo'q.", show_alert=True)
        return

    req_id = callback.data.replace("rejc_cou_", "")
    await db.update_courier_request_status(req_id, "rejected")
    await callback.message.edit_text(callback.message.html_text + "\n\n❌ <b>Ariza rad etildi.</b>", parse_mode="HTML")
    await callback.answer("Ariza rad etildi.")




# 👥 Kuryerlar ro'yxati — tasdiqlangan kuryerlarni ko'rish va o'chirish.
# Ariza ro'yxatidan farqi: bu yerda ARIZALAR emas, ishlayotgan KURYERLAR.
@router.message(F.text == "👥 Kuryerlar ro'yxati")
@router.callback_query(F.data == "admin_view_courier_list")
async def admin_courier_list_handler(event: Message | CallbackQuery):
    if not is_admin_user(event.from_user.id, event.from_user.username):
        return

    msg = event if isinstance(event, Message) else event.message
    if isinstance(event, CallbackQuery):
        await event.answer()

    couriers = await db.list_couriers()
    if not couriers:
        await msg.answer(
            "👥 <b>Hali birorta kuryer tasdiqlanmagan.</b>\n"
            "Arizalarni «🛵 Kuryerlar arizalari» bo'limidan tasdiqlang.",
            parse_mode="HTML",
        )
        return

    active = sum(1 for c in couriers if c.get("isActive") is True)
    await msg.answer(
        f"👥 <b>Kuryerlar: {len(couriers)} ta</b> (faol: {active})", parse_mode="HTML"
    )

    for c in couriers:
        is_active = c.get("isActive") is True
        busy = c.get("activeOrderId")
        state = "🟢 Faol" if is_active else "🔴 O'chirilgan"
        if is_active and busy:
            state = f"🟡 Band (zakas <code>{esc(busy)}</code>)"
        # Ism alohida o'zgaruvchida: f-string ifodasi ichida teskari slesh
        # Python 3.11 da sintaksis xatosi beradi (3.12 dan ruxsat).
        name = esc(c.get("fullName") or "Noma'lum")
        text = (
            f"👤 <b>{name}</b>\n"
            f"📞 <code>{esc(c.get('phoneNumber'))}</code>\n"
            f"🚗 {esc(c.get('vehicle'))}   📍 {esc(c.get('city'))}\n"
            f"📦 Yetkazgan: {c.get('completedCount', 0)} ta\n"
            f"📊 {state}"
        )
        toggle = (
            InlineKeyboardButton(
                text="🔴 O'chirish", callback_data=f"cour_off_{c['id']}")
            if is_active else
            InlineKeyboardButton(
                text="🟢 Yoqish", callback_data=f"cour_on_{c['id']}")
        )
        await msg.answer(
            text,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[toggle]]),
            parse_mode="HTML",
        )


async def _toggle_courier(callback: CallbackQuery, courier_id: str, is_active: bool):
    if not is_admin_user(callback.from_user.id, callback.from_user.username):
        await callback.answer("⛔️ Sizda admin huquqlari yo'q.", show_alert=True)
        return

    if not await db.set_courier_active(courier_id, is_active):
        await callback.answer("⚠️ O'zgartirib bo'lmadi.", show_alert=True)
        return

    label = "🟢 Faol" if is_active else "🔴 O'chirilgan"
    await callback.message.edit_text(
        callback.message.html_text + f"\n\n➡️ <b>{label}</b>", parse_mode="HTML"
    )
    await callback.answer("Bajarildi")


@router.callback_query(F.data.startswith("cour_off_"))
async def courier_disable_cb(callback: CallbackQuery):
    await _toggle_courier(callback, callback.data.replace("cour_off_", ""), False)


@router.callback_query(F.data.startswith("cour_on_"))
async def courier_enable_cb(callback: CallbackQuery):
    await _toggle_courier(callback, callback.data.replace("cour_on_", ""), True)
