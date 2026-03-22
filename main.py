import asyncio
import logging
import os
import time
from html import escape

from aiogram import Bot, Dispatcher, F
from aiogram.enums import ParseMode, ChatType
from aiogram.filters import CommandStart
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID"))
TARGET_CHANNEL_ID = int(os.getenv("TARGET_CHANNEL_ID", "0"))

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN eksik")
if not ADMIN_ID:
    raise ValueError("ADMIN_ID eksik")

logging.basicConfig(level=logging.INFO)

bot = Bot(token=BOT_TOKEN, parse_mode=ParseMode.HTML)
dp = Dispatcher()

pending_confessions = {}
user_last_sent = {}

COOLDOWN_SECONDS = 60
MAX_TEXT_LENGTH = 1500

BANNED_WORDS = [
    "http://",
    "https://",
    "t.me/",
    "telegram.me/",
    "join my channel",
    "onlyfans",
    "spam",
]

def is_spam(text: str) -> bool:
    lower_text = text.lower()
    return any(word in lower_text for word in BANNED_WORDS)

def is_on_cooldown(user_id: int) -> bool:
    now = time.time()
    last_time = user_last_sent.get(user_id, 0)
    return now - last_time < COOLDOWN_SECONDS

def set_cooldown(user_id: int):
    user_last_sent[user_id] = time.time()

def admin_keyboard(confession_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Onayla", callback_data=f"approve:{confession_id}"),
                InlineKeyboardButton(text="❌ Reddet", callback_data=f"reject:{confession_id}")
            ]
        ]
    )

@dp.message(CommandStart())
async def start_handler(message: Message):
    await message.answer(
        "👋 Merhaba!\n\n"
        "Bu bot ile anonim itiraf gönderebilirsin.\n"
        "Gönderdiğin mesaj admin onayına gider.\n"
        "Onaylanırsa KANALDA anonim paylaşılır.\n\n"
        "✍️ Bana itirafını gönder."
    )

@dp.message(F.chat.type == ChatType.PRIVATE)
async def confession_handler(message: Message):
    if message.text and message.text.startswith("/"):
        return

    if not message.text:
        await message.answer("⚠️ Şimdilik sadece metin itiraf kabul ediliyor.")
        return

    user_id = message.from_user.id

    if is_on_cooldown(user_id):
        await message.answer(f"⏳ Çok hızlı gönderim yapıyorsun. {COOLDOWN_SECONDS} saniye bekle.")
        return

    confession_text = message.text.strip()

    if len(confession_text) < 3:
        await message.answer("⚠️ İtiraf çok kısa.")
        return

    if len(confession_text) > MAX_TEXT_LENGTH:
        await message.answer(f"⚠️ İtiraf çok uzun. En fazla {MAX_TEXT_LENGTH} karakter olabilir.")
        return

    if is_spam(confession_text):
        await message.answer("🚫 Mesaj spam/link/reklam içeriyor gibi görünüyor.")
        return

    set_cooldown(user_id)

    confession_id = str(int(time.time() * 1000))
    pending_confessions[confession_id] = {
        "user_id": user_id,
        "text": confession_text,
        "created_at": time.time(),
    }

    safe_text = escape(confession_text)

    admin_text = (
        f"📥 <b>Yeni İtiraf Bekliyor</b>\n\n"
        f"<b>ID:</b> <code>{confession_id}</code>\n"
        f"<b>Gönderen Kullanıcı ID:</b> <code>{user_id}</code>\n\n"
        f"<b>Mesaj:</b>\n{safe_text}"
    )

    await bot.send_message(
        chat_id=ADMIN_ID,
        text=admin_text,
        reply_markup=admin_keyboard(confession_id)
    )

    await message.answer("✅ İtirafın alındı. Admin onayından sonra kanalda paylaşılabilir.")

@dp.callback_query(F.data.startswith("approve:"))
async def approve_confession(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Yetkin yok.", show_alert=True)
        return

    confession_id = callback.data.split(":")[1]
    confession = pending_confessions.get(confession_id)

    if not confession:
        await callback.answer("Bu itiraf bulunamadı veya zaten işlendi.", show_alert=True)
        return

    if TARGET_CHANNEL_ID == 0:
        await callback.message.answer("❌ TARGET_CHANNEL_ID ayarlanmamış.")
        await callback.answer("Hata", show_alert=True)
        return

    safe_text = escape(confession["text"])

    public_text = (
        f"📩 <b>Yeni İtiraf</b>\n"
        f"🆔 <code>#{confession_id[-6:]}</code>\n\n"
        f"{safe_text}"
    )

    try:
        await bot.send_message(
            chat_id=TARGET_CHANNEL_ID,
            text=public_text
        )
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.message.answer(
            f"✅ İtiraf onaylandı ve KANALA gönderildi.\n"
            f"ID: <code>{confession_id}</code>"
        )
        pending_confessions.pop(confession_id, None)
        await callback.answer("Onaylandı.")
    except Exception as e:
        await callback.message.answer(
            f"❌ Kanal gönderim hatası:\n<code>{escape(str(e))}</code>"
        )
        await callback.answer("Hata oluştu", show_alert=True)

@dp.callback_query(F.data.startswith("reject:"))
async def reject_confession(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Yetkin yok.", show_alert=True)
        return

    confession_id = callback.data.split(":")[1]
    confession = pending_confessions.get(confession_id)

    if not confession:
        await callback.answer("Bu itiraf bulunamadı veya zaten işlendi.", show_alert=True)
        return

    pending_confessions.pop(confession_id, None)

    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer(f"❌ İtiraf reddedildi.\nID: <code>{confession_id}</code>")
    await callback.answer("Reddedildi.")

# Kanal ID öğrenmek için:
# Bot kanala admin olarak ekliyse ve kanalda yeni post atılırsa,
# bot admin'e kanal id'yi yollar.
@dp.channel_post()
async def channel_post_handler(message: Message):
    try:
        await bot.send_message(
            chat_id=ADMIN_ID,
            text=(
                f"📢 <b>Kanal bilgisi alındı</b>\n\n"
                f"<b>Kanal adı:</b> {escape(message.chat.title or 'Bilinmiyor')}\n"
                f"<b>Kanal ID:</b> <code>{message.chat.id}</code>"
            )
        )
    except Exception as e:
        logging.error(f"Kanal bilgisi gönderilemedi: {e}")

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
