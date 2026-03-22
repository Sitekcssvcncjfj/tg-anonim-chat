import asyncio
import logging
import os
import time
from html import escape
from typing import Optional, Any

import aiosqlite
from aiogram import Bot, Dispatcher, F
from aiogram.enums import ParseMode, ChatType, ContentType
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO)

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_IDS = [int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()]
TARGET_CHANNEL_ID = os.getenv("TARGET_CHANNEL_ID", "0")
SUPPORT_URL = os.getenv("SUPPORT_URL", "https://t.me/KGBotomasyon")
DB_PATH = os.getenv("DB_PATH", "/data/bot.db")
PUBLISH_INTERVAL_SECONDS = int(os.getenv("PUBLISH_INTERVAL_SECONDS", "30"))
BANNED_WORDS = [x.strip().lower() for x in os.getenv(
    "BANNED_WORDS",
    "http://,https://,t.me/,telegram.me/,onlyfans,join my channel,spam"
).split(",") if x.strip()]

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN eksik")
if not ADMIN_IDS:
    raise ValueError("ADMIN_IDS eksik")

bot = Bot(token=BOT_TOKEN, parse_mode=ParseMode.HTML)
dp = Dispatcher()

broadcast_waiting_admins = set()
edit_waiting_admins = {}  # admin_id -> confession_db_id

CONFIG = {
    "cooldown_seconds": 60,
    "max_text_length": 3000,
    "auto_publish": True,
}

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

def user_display_name(message: Message) -> str:
    first = escape(message.from_user.first_name or "Dostum")
    username = f"@{escape(message.from_user.username)}" if message.from_user.username else ""
    return f"{first} {username}".strip()

def now_ts() -> int:
    return int(time.time())

def format_content_type_label(content_type: str) -> str:
    mapping = {
        "text": "Yazı",
        "photo": "Fotoğraf",
        "video": "Video",
        "document": "Belge",
        "audio": "Ses Dosyası",
        "voice": "Ses Kaydı",
        "sticker": "Sticker",
    }
    return mapping.get(content_type, content_type)

def start_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✍️ İtiraf Gönder", callback_data="start_confess")],
        [
            InlineKeyboardButton(text="✨ Nasıl Çalışır?", callback_data="how_it_works"),
            InlineKeyboardButton(text="🛡 Destek", url=SUPPORT_URL),
        ]
    ])

def admin_panel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="⏳ Bekleyenler", callback_data="panel_pending"),
            InlineKeyboardButton(text="✅ Onaylananlar", callback_data="panel_approved"),
        ],
        [
            InlineKeyboardButton(text="❌ Reddedilenler", callback_data="panel_rejected"),
            InlineKeyboardButton(text="🚫 Ban Listesi", callback_data="panel_bans"),
        ],
        [
            InlineKeyboardButton(text="⚙️ Filtreler", callback_data="panel_filters"),
            InlineKeyboardButton(text="📊 İstatistik", callback_data="panel_stats"),
        ],
        [
            InlineKeyboardButton(text="🔄 Ayar Yenile", callback_data="panel_reload"),
        ]
    ])

def confession_admin_keyboard(db_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Onayla", callback_data=f"approve:{db_id}"),
            InlineKeyboardButton(text="✏️ Düzenle", callback_data=f"edit:{db_id}"),
        ],
        [
            InlineKeyboardButton(text="❌ Reddet", callback_data=f"reject:{db_id}"),
        ]
    ])

def report_keyboard(confession_no: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚨 Şikayet Et", callback_data=f"report:{confession_no}")]
    ])

def premium_user_received(confession_no: int) -> str:
    return (
        "╭━━━ ✨ <b>İtiraf Alındı</b> ✨ ━━━╮\n"
        f"┃ 🆔 Numaran: <code>#{confession_no}</code>\n"
        "┃ 📬 İçeriğin güvenle alındı\n"
        "┃ 🛡 Kimliğin anonim kalacak\n"
        "┃ ⏳ Admin onayından sonra sıraya alınacak\n"
        "╰━━━━━━━━━━━━━━━━━━━━━━╯"
    )

def premium_admin_preview(
    db_id: int,
    confession_no: int,
    user_id: int,
    content_type: str,
    text: str
) -> str:
    preview = escape(text or "(Metinsiz medya)")
    return (
        "╔════════════════════╗\n"
        "║ 📨 <b>YENİ İTİRAF BEKLİYOR</b>\n"
        "╚════════════════════╝\n\n"
        f"🗂 <b>DB ID:</b> <code>{db_id}</code>\n"
        f"🆔 <b>İtiraf No:</b> <code>#{confession_no}</code>\n"
        f"👤 <b>Gönderen ID:</b> <code>{user_id}</code>\n"
        f"🎞 <b>Tür:</b> <b>{format_content_type_label(content_type)}</b>\n"
        f"📡 <b>Hedef:</b> <code>{escape(TARGET_CHANNEL_ID)}</code>\n\n"
        f"💬 <b>İçerik:</b>\n{preview}"
    )

def premium_channel_caption(confession_no: int, text: str) -> str:
    safe = escape(text or "")
    return (
        "┏━━━━━━━━━━━━━━━━━━┓\n"
        "┃ ✨ <b>ANONİM İTİRAF</b>\n"
        "┗━━━━━━━━━━━━━━━━━━┛\n\n"
        f"🆔 <b>No:</b> <code>#{confession_no}</code>\n"
        "┄┄┄┄┄┄┄┄┄┄\n"
        f"{safe}\n"
        "┄┄┄┄┄┄┄┄┄┄\n"
        "🤍 <i>Anonim olarak gönderildi</i>"
    )

async def send_to_all_admins(text: str, reply_markup=None):
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(admin_id, text, reply_markup=reply_markup)
        except Exception as e:
            logging.error(f"Admin {admin_id} mesaj hatası: {e}")

async def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            is_banned INTEGER DEFAULT 0,
            created_at INTEGER,
            last_seen_at INTEGER
        )
        """)
        await db.execute("""
        CREATE TABLE IF NOT EXISTS confessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            confession_no INTEGER UNIQUE,
            user_id INTEGER,
            content_type TEXT,
            text TEXT,
            media_file_id TEXT,
            media_file_unique_id TEXT,
            status TEXT,
            admin_id INTEGER,
            channel_message_id INTEGER,
            created_at INTEGER,
            updated_at INTEGER,
            queued_at INTEGER,
            published_at INTEGER
        )
        """)
        await db.execute("""
        CREATE TABLE IF NOT EXISTS reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            confession_no INTEGER,
            reporter_user_id INTEGER,
            reporter_username TEXT,
            created_at INTEGER
        )
        """)
        await db.execute("""
        CREATE TABLE IF NOT EXISTS seq (
            name TEXT PRIMARY KEY,
            value INTEGER
        )
        """)
        await db.execute("INSERT OR IGNORE INTO seq (name, value) VALUES ('confession_no', 0)")
        await db.commit()

async def get_next_confession_no() -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE seq SET value = value + 1 WHERE name='confession_no'")
        await db.commit()
        cur = await db.execute("SELECT value FROM seq WHERE name='confession_no'")
        row = await cur.fetchone()
        return row[0]

async def upsert_user(message: Message):
    user = message.from_user
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO users (user_id, username, first_name, is_banned, created_at, last_seen_at)
            VALUES (?, ?, ?, 0, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                username=excluded.username,
                first_name=excluded.first_name,
                last_seen_at=excluded.last_seen_at
        """, (
            user.id,
            user.username,
            user.first_name,
            now_ts(),
            now_ts()
        ))
        await db.commit()

async def is_banned_user(user_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT is_banned FROM users WHERE user_id=?", (user_id,))
        row = await cur.fetchone()
        return bool(row and row[0] == 1)

async def ban_user(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO users (user_id, username, first_name, is_banned, created_at, last_seen_at)
            VALUES (?, '', '', 1, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET is_banned=1
        """, (user_id, now_ts(), now_ts()))
        await db.commit()

async def unban_user(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET is_banned=0 WHERE user_id=?", (user_id,))
        await db.commit()

async def count_recent_confessions(user_id: int, seconds: int) -> int:
    threshold = now_ts() - seconds
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("""
            SELECT COUNT(*) FROM confessions
            WHERE user_id=? AND created_at >= ?
        """, (user_id, threshold))
        row = await cur.fetchone()
        return row[0] if row else 0

def is_spam_text(text: str) -> bool:
    text = (text or "").lower()
    return any(word in text for word in BANNED_WORDS)

async def create_confession_record(
    user_id: int,
    content_type: str,
    text: str,
    media_file_id: Optional[str] = None,
    media_file_unique_id: Optional[str] = None
) -> tuple[int, int]:
    confession_no = await get_next_confession_no()
    ts = now_ts()
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("""
            INSERT INTO confessions (
                confession_no, user_id, content_type, text, media_file_id, media_file_unique_id,
                status, admin_id, channel_message_id, created_at, updated_at, queued_at, published_at
            ) VALUES (?, ?, ?, ?, ?, ?, 'pending', NULL, NULL, ?, ?, NULL, NULL)
        """, (
            confession_no, user_id, content_type, text, media_file_id, media_file_unique_id,
            ts, ts
        ))
        await db.commit()
        return cur.lastrowid, confession_no

async def get_confession_by_id(db_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("""
            SELECT id, confession_no, user_id, content_type, text, media_file_id, media_file_unique_id,
                   status, admin_id, channel_message_id, created_at, updated_at, queued_at, published_at
            FROM confessions WHERE id=?
        """, (db_id,))
        return await cur.fetchone()

async def update_confession_text(db_id: int, new_text: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            UPDATE confessions
            SET text=?, updated_at=?
            WHERE id=?
        """, (new_text, now_ts(), db_id))
        await db.commit()

async def set_confession_status(db_id: int, status: str, admin_id: Optional[int] = None):
    async with aiosqlite.connect(DB_PATH) as db:
        if status == "approved":
            await db.execute("""
                UPDATE confessions SET status=?, admin_id=?, updated_at=?, queued_at=?
                WHERE id=?
            """, (status, admin_id, now_ts(), now_ts(), db_id))
        else:
            await db.execute("""
                UPDATE confessions SET status=?, admin_id=?, updated_at=?
                WHERE id=?
            """, (status, admin_id, now_ts(), db_id))
        await db.commit()

async def set_confession_published(db_id: int, channel_message_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            UPDATE confessions
            SET status='published', channel_message_id=?, updated_at=?, published_at=?
            WHERE id=?
        """, (channel_message_id, now_ts(), now_ts(), db_id))
        await db.commit()

async def get_counts():
    async with aiosqlite.connect(DB_PATH) as db:
        total = (await (await db.execute("SELECT COUNT(*) FROM confessions")).fetchone())[0]
        pending = (await (await db.execute("SELECT COUNT(*) FROM confessions WHERE status='pending'")).fetchone())[0]
        approved = (await (await db.execute("SELECT COUNT(*) FROM confessions WHERE status='approved'")).fetchone())[0]
        rejected = (await (await db.execute("SELECT COUNT(*) FROM confessions WHERE status='rejected'")).fetchone())[0]
        published = (await (await db.execute("SELECT COUNT(*) FROM confessions WHERE status='published'")).fetchone())[0]
        banned = (await (await db.execute("SELECT COUNT(*) FROM users WHERE is_banned=1")).fetchone())[0]
        return total, pending, approved, rejected, published, banned

async def get_pending_list(limit: int = 10):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("""
            SELECT id, confession_no, user_id, content_type, text, created_at
            FROM confessions
            WHERE status='pending'
            ORDER BY id ASC
            LIMIT ?
        """, (limit,))
        return await cur.fetchall()

async def get_status_list(status: str, limit: int = 10):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("""
            SELECT confession_no, user_id, content_type, text, updated_at
            FROM confessions
            WHERE status=?
            ORDER BY id DESC
            LIMIT ?
        """, (status, limit))
        return await cur.fetchall()

async def get_ban_list(limit: int = 30):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("""
            SELECT user_id, username, first_name
            FROM users
            WHERE is_banned=1
            ORDER BY user_id DESC
            LIMIT ?
        """, (limit,))
        return await cur.fetchall()

async def save_report(confession_no: int, reporter_user_id: int, reporter_username: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO reports (confession_no, reporter_user_id, reporter_username, created_at)
            VALUES (?, ?, ?, ?)
        """, (confession_no, reporter_user_id, reporter_username, now_ts()))
        await db.commit()

async def report_exists(confession_no: int, reporter_user_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("""
            SELECT 1 FROM reports WHERE confession_no=? AND reporter_user_id=? LIMIT 1
        """, (confession_no, reporter_user_id))
        row = await cur.fetchone()
        return row is not None

async def broadcast_to_all_users(text: str) -> tuple[int, int]:
    sent = 0
    failed = 0
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT user_id FROM users")
        rows = await cur.fetchall()
    for (uid,) in rows:
        try:
            await bot.send_message(uid, text)
            sent += 1
        except Exception:
            failed += 1
    return sent, failed

async def send_confession_to_channel(row: Any):
    db_id, confession_no, user_id, content_type, text, media_file_id, media_file_unique_id, status, admin_id, channel_message_id, created_at, updated_at, queued_at, published_at = row
    caption = premium_channel_caption(confession_no, text or "")

    if content_type == "text":
        sent = await bot.send_message(TARGET_CHANNEL_ID, caption, reply_markup=report_keyboard(confession_no))
    elif content_type == "photo":
        sent = await bot.send_photo(TARGET_CHANNEL_ID, photo=media_file_id, caption=caption, reply_markup=report_keyboard(confession_no))
    elif content_type == "video":
        sent = await bot.send_video(TARGET_CHANNEL_ID, video=media_file_id, caption=caption, reply_markup=report_keyboard(confession_no))
    elif content_type == "document":
        sent = await bot.send_document(TARGET_CHANNEL_ID, document=media_file_id, caption=caption, reply_markup=report_keyboard(confession_no))
    elif content_type == "audio":
        sent = await bot.send_audio(TARGET_CHANNEL_ID, audio=media_file_id, caption=caption, reply_markup=report_keyboard(confession_no))
    elif content_type == "voice":
        sent = await bot.send_voice(TARGET_CHANNEL_ID, voice=media_file_id, caption=caption, reply_markup=report_keyboard(confession_no))
    elif content_type == "sticker":
        sent = await bot.send_sticker(TARGET_CHANNEL_ID, sticker=media_file_id)
        await bot.send_message(TARGET_CHANNEL_ID, premium_channel_caption(confession_no, text or "(Sticker itirafı)"), reply_markup=report_keyboard(confession_no))
    else:
        sent = await bot.send_message(TARGET_CHANNEL_ID, caption, reply_markup=report_keyboard(confession_no))

    msg_id = sent.message_id if hasattr(sent, "message_id") else 0
    await set_confession_published(db_id, msg_id)

    try:
        await bot.send_message(
            user_id,
            "╭━━━ 🚀 <b>İtirafın Yayınlandı</b> 🚀 ━━━╮\n"
            f"┃ 🆔 Numaran: <code>#{confession_no}</code>\n"
            "┃ 📢 Kanalda başarıyla paylaşıldı\n"
            "╰━━━━━━━━━━━━━━━━━━━━━━╯"
        )
    except Exception:
        pass

async def publisher_loop():
    await asyncio.sleep(5)
    while True:
        try:
            if TARGET_CHANNEL_ID != "0" and CONFIG["auto_publish"]:
                async with aiosqlite.connect(DB_PATH) as db:
                    cur = await db.execute("""
                        SELECT id, confession_no, user_id, content_type, text, media_file_id, media_file_unique_id,
                               status, admin_id, channel_message_id, created_at, updated_at, queued_at, published_at
                        FROM confessions
                        WHERE status='approved'
                        ORDER BY queued_at ASC, id ASC
                        LIMIT 1
                    """)
                    row = await cur.fetchone()
                if row:
                    await send_confession_to_channel(row)
            await asyncio.sleep(PUBLISH_INTERVAL_SECONDS)
        except Exception as e:
            logging.exception(f"publisher_loop hata: {e}")
            await asyncio.sleep(5)

@dp.message(CommandStart())
async def start_handler(message: Message):
    await upsert_user(message)
    welcome = (
        f"✨ <b>Hoş geldin {user_display_name(message)}</b>\n\n"
        "Anonim itirafını güvenli şekilde gönderebilirsin.\n"
        "Mesajın admin kontrolünden geçer, sonra yayın sırasına alınır.\n\n"
        f"🛡 <b>Destek Kanalı:</b> <a href=\"{SUPPORT_URL}\">KGBotomasyon</a>\n"
        "Aşağıdaki menüyü kullanabilirsin."
    )
    await message.answer(welcome, reply_markup=start_keyboard(), disable_web_page_preview=True)

@dp.callback_query(F.data == "start_confess")
async def cb_start_confess(callback: CallbackQuery):
    await callback.message.answer(
        "✍️ <b>İtiraf Gönderimi</b>\n\n"
        "Bana şunlardan birini gönderebilirsin:\n"
        "• Yazı\n"
        "• Fotoğraf\n"
        "• Video\n"
        "• Belge\n"
        "• Ses / Voice\n"
        "• Sticker\n\n"
        "İstersen medyaya açıklama da ekleyebilirsin.\n"
        "Kimliğin anonim kalır."
    )
    await callback.answer()

@dp.callback_query(F.data == "how_it_works")
async def cb_how(callback: CallbackQuery):
    await callback.message.answer(
        "✨ <b>Sistem Nasıl Çalışır?</b>\n\n"
        "1) İçeriğini bana gönderirsin\n"
        "2) Adminler inceler\n"
        "3) Gerekirse düzenler\n"
        "4) Onaylanırsa sıraya alınır\n"
        "5) Kanalda anonim paylaşılır"
    )
    await callback.answer()

@dp.message(Command("help"))
async def help_handler(message: Message):
    await upsert_user(message)
    if is_admin(message.from_user.id):
        text = (
            "🛠 <b>Admin Komutları</b>\n\n"
            "/panel - Butonlu panel\n"
            "/pending - Bekleyenler\n"
            "/stats - İstatistik\n"
            "/broadcast - Duyuru modu\n"
            "/reload - Ayar yenile\n"
            "/ban USER_ID - Banla\n"
            "/unban USER_ID - Ban kaldır\n"
            "/cancel - Bekleyen admin işlemini iptal et"
        )
    else:
        text = (
            "ℹ️ <b>Kullanıcı Yardım</b>\n\n"
            "/start - Başlangıç menüsü\n"
            "Bana direkt itirafını gönder.\n"
            f"Destek: <a href=\"{SUPPORT_URL}\">KGBotomasyon</a>"
        )
    await message.answer(text, disable_web_page_preview=True)

@dp.message(Command("panel"))
async def panel_handler(message: Message):
    await upsert_user(message)
    if not is_admin(message.from_user.id):
        return await message.answer("⛔ Bu komut sadece adminler içindir.")
    await message.answer("🧩 <b>Admin Paneli</b>", reply_markup=admin_panel_keyboard())

@dp.callback_query(F.data == "panel_pending")
async def panel_pending(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return await callback.answer("Yetkin yok", show_alert=True)
    rows = await get_pending_list(10)
    if not rows:
        await callback.message.answer("⏳ Bekleyen itiraf yok.")
    else:
        text = "⏳ <b>Bekleyen İtiraflar</b>\n\n"
        for row in rows:
            db_id, no, user_id, ctype, txt, created_at = row
            preview = escape((txt or "(medya)").replace("\n", " ")[:60])
            text += f"• ID:{db_id} | #{no} | {format_content_type_label(ctype)} | {preview}\n"
        await callback.message.answer(text)
    await callback.answer()

@dp.callback_query(F.data == "panel_approved")
async def panel_approved(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return await callback.answer("Yetkin yok", show_alert=True)
    rows = await get_status_list("approved", 10)
    if not rows:
        await callback.message.answer("✅ Kuyrukta onaylı itiraf yok.")
    else:
        text = "✅ <b>Onaylı / Kuyruktaki İtiraflar</b>\n\n"
        for no, user_id, ctype, txt, updated_at in rows:
            preview = escape((txt or "(medya)").replace("\n", " ")[:60])
            text += f"• #{no} | {format_content_type_label(ctype)} | {preview}\n"
        await callback.message.answer(text)
    await callback.answer()

@dp.callback_query(F.data == "panel_rejected")
async def panel_rejected(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return await callback.answer("Yetkin yok", show_alert=True)
    rows = await get_status_list("rejected", 10)
    if not rows:
        await callback.message.answer("❌ Reddedilmiş itiraf yok.")
    else:
        text = "❌ <b>Reddedilen İtiraflar</b>\n\n"
        for no, user_id, ctype, txt, updated_at in rows:
            preview = escape((txt or "(medya)").replace("\n", " ")[:60])
            text += f"• #{no} | {format_content_type_label(ctype)} | {preview}\n"
        await callback.message.answer(text)
    await callback.answer()

@dp.callback_query(F.data == "panel_bans")
async def panel_bans(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return await callback.answer("Yetkin yok", show_alert=True)
    rows = await get_ban_list()
    if not rows:
        await callback.message.answer("🚫 Banlı kullanıcı yok.")
    else:
        text = "🚫 <b>Ban Listesi</b>\n\n"
        for uid, username, first_name in rows:
            uname = f"@{escape(username)}" if username else "-"
            text += f"• <code>{uid}</code> | {escape(first_name or '-') } | {uname}\n"
        await callback.message.answer(text)
    await callback.answer()

@dp.callback_query(F.data == "panel_filters")
async def panel_filters(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return await callback.answer("Yetkin yok", show_alert=True)
    text = (
        "⚙️ <b>Filtre Ayarları</b>\n\n"
        f"Cooldown: <code>{CONFIG['cooldown_seconds']}</code> sn\n"
        f"Max metin: <code>{CONFIG['max_text_length']}</code>\n"
        f"Otomatik yayın: <code>{CONFIG['auto_publish']}</code>\n"
        f"Yayın aralığı: <code>{PUBLISH_INTERVAL_SECONDS}</code> sn\n"
        f"Yasaklı kelimeler:\n<code>{escape(', '.join(BANNED_WORDS))}</code>"
    )
    await callback.message.answer(text)
    await callback.answer()

@dp.callback_query(F.data == "panel_stats")
async def panel_stats(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return await callback.answer("Yetkin yok", show_alert=True)
    total, pending, approved, rejected, published, banned = await get_counts()
    text = (
        "📊 <b>İstatistikler</b>\n\n"
        f"Toplam: <code>{total}</code>\n"
        f"Bekleyen: <code>{pending}</code>\n"
        f"Onaylı Kuyruk: <code>{approved}</code>\n"
        f"Reddedilen: <code>{rejected}</code>\n"
        f"Yayınlanan: <code>{published}</code>\n"
        f"Banlı kullanıcı: <code>{banned}</code>"
    )
    await callback.message.answer(text)
    await callback.answer()

@dp.callback_query(F.data == "panel_reload")
async def panel_reload(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return await callback.answer("Yetkin yok", show_alert=True)
    CONFIG["cooldown_seconds"] = int(os.getenv("COOLDOWN_SECONDS", "60"))
    CONFIG["max_text_length"] = int(os.getenv("MAX_TEXT_LENGTH", "3000"))
    CONFIG["auto_publish"] = os.getenv("AUTO_PUBLISH", "true").lower() == "true"
    await callback.message.answer("🔄 Ayarlar env üzerinden yeniden yüklendi.")
    await callback.answer("Yenilendi")

@dp.message(Command("pending"))
async def pending_handler(message: Message):
    await upsert_user(message)
    if not is_admin(message.from_user.id):
        return await message.answer("⛔ Bu komut sadece adminler içindir.")
    rows = await get_pending_list(15)
    if not rows:
        return await message.answer("⏳ Bekleyen itiraf yok.")
    text = "⏳ <b>Bekleyen İtiraflar</b>\n\n"
    for row in rows:
        db_id, no, user_id, ctype, txt, created_at = row
        preview = escape((txt or "(medya)").replace("\n", " ")[:80])
        text += f"• DB:{db_id} | #{no} | {format_content_type_label(ctype)} | {preview}\n"
    await message.answer(text)

@dp.message(Command("stats"))
async def stats_handler(message: Message):
    await upsert_user(message)
    if not is_admin(message.from_user.id):
        return await message.answer("⛔ Bu komut sadece adminler içindir.")
    total, pending, approved, rejected, published, banned = await get_counts()
    await message.answer(
        "📊 <b>İstatistikler</b>\n\n"
        f"Toplam: <code>{total}</code>\n"
        f"Bekleyen: <code>{pending}</code>\n"
        f"Onaylı Kuyruk: <code>{approved}</code>\n"
        f"Reddedilen: <code>{rejected}</code>\n"
        f"Yayınlanan: <code>{published}</code>\n"
        f"Banlı kullanıcı: <code>{banned}</code>"
    )

@dp.message(Command("reload"))
async def reload_handler(message: Message):
    await upsert_user(message)
    if not is_admin(message.from_user.id):
        return await message.answer("⛔ Bu komut sadece adminler içindir.")
    CONFIG["cooldown_seconds"] = int(os.getenv("COOLDOWN_SECONDS", "60"))
    CONFIG["max_text_length"] = int(os.getenv("MAX_TEXT_LENGTH", "3000"))
    CONFIG["auto_publish"] = os.getenv("AUTO_PUBLISH", "true").lower() == "true"
    await message.answer("🔄 Ayarlar env üzerinden yeniden yüklendi.")

@dp.message(Command("broadcast"))
async def broadcast_handler(message: Message):
    await upsert_user(message)
    if not is_admin(message.from_user.id):
        return await message.answer("⛔ Bu komut sadece adminler içindir.")
    broadcast_waiting_admins.add(message.from_user.id)
    await message.answer(
        "📣 <b>Duyuru modu açıldı</b>\n\n"
        "Bir sonraki mesajın tüm kullanıcılara gönderilecek.\n"
        "İptal için /cancel yaz."
    )

@dp.message(Command("cancel"))
async def cancel_handler(message: Message):
    await upsert_user(message)
    broadcast_waiting_admins.discard(message.from_user.id)
    edit_waiting_admins.pop(message.from_user.id, None)
    await message.answer("❎ Bekleyen işlem iptal edildi.")

@dp.message(Command("ban"))
async def ban_handler(message: Message):
    await upsert_user(message)
    if not is_admin(message.from_user.id):
        return await message.answer("⛔ Bu komut sadece adminler içindir.")
    parts = (message.text or "").split()
    if len(parts) != 2 or not parts[1].isdigit():
        return await message.answer("Kullanım: <code>/ban USER_ID</code>")
    uid = int(parts[1])
    await ban_user(uid)
    await message.answer(f"🚫 Kullanıcı banlandı: <code>{uid}</code>")

@dp.message(Command("unban"))
async def unban_handler(message: Message):
    await upsert_user(message)
    if not is_admin(message.from_user.id):
        return await message.answer("⛔ Bu komut sadece adminler içindir.")
    parts = (message.text or "").split()
    if len(parts) != 2 or not parts[1].isdigit():
        return await message.answer("Kullanım: <code>/unban USER_ID</code>")
    uid = int(parts[1])
    await unban_user(uid)
    await message.answer(f"✅ Kullanıcı banı kaldırıldı: <code>{uid}</code>")

@dp.callback_query(F.data.startswith("edit:"))
async def edit_confession(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return await callback.answer("Yetkin yok", show_alert=True)

    db_id = int(callback.data.split(":")[1])
    row = await get_confession_by_id(db_id)
    if not row:
        return await callback.answer("İtiraf bulunamadı", show_alert=True)

    if row[7] != "pending":
        return await callback.answer("Sadece bekleyen itiraf düzenlenebilir", show_alert=True)

    edit_waiting_admins[callback.from_user.id] = db_id
    current_text = row[4] or ""
    await callback.message.answer(
        "✏️ <b>Düzenleme Modu</b>\n\n"
        f"İtiraf: <code>#{row[1]}</code>\n"
        "Lütfen yeni metni tek mesaj olarak gönder.\n"
        "İptal için /cancel yaz.\n\n"
        f"Mevcut metin:\n<code>{escape(current_text[:1500])}</code>"
    )
    await callback.answer("Düzenleme modu açıldı")

@dp.callback_query(F.data.startswith("approve:"))
async def approve_confession(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return await callback.answer("Yetkin yok.", show_alert=True)

    db_id = int(callback.data.split(":")[1])
    row = await get_confession_by_id(db_id)

    if not row:
        return await callback.answer("İtiraf bulunamadı.", show_alert=True)

    if row[7] != "pending":
        return await callback.answer("Bu itiraf zaten işlendi.", show_alert=True)

    if TARGET_CHANNEL_ID == "0":
        await callback.message.answer("❌ TARGET_CHANNEL_ID ayarlanmamış.")
        return await callback.answer("Hata", show_alert=True)

    await set_confession_status(db_id, "approved", callback.from_user.id)

    user_id = row[2]
    confession_no = row[1]
    try:
        await bot.send_message(
            user_id,
            "╭━━━ ✅ <b>İtirafın Onaylandı</b> ✅ ━━━╮\n"
            f"┃ 🆔 Numaran: <code>#{confession_no}</code>\n"
            "┃ ⏳ Yayın sırasına alındı\n"
            "╰━━━━━━━━━━━━━━━━━━━━━━╯"
        )
    except Exception:
        pass

    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer(
        "✅ <b>Onaylandı</b>\n"
        f"İtiraf <code>#{confession_no}</code> yayın kuyruğuna alındı."
    )
    await callback.answer("Onaylandı")

@dp.callback_query(F.data.startswith("reject:"))
async def reject_confession(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return await callback.answer("Yetkin yok.", show_alert=True)

    db_id = int(callback.data.split(":")[1])
    row = await get_confession_by_id(db_id)

    if not row:
        return await callback.answer("İtiraf bulunamadı.", show_alert=True)

    if row[7] != "pending":
        return await callback.answer("Bu itiraf zaten işlendi.", show_alert=True)

    await set_confession_status(db_id, "rejected", callback.from_user.id)

    user_id = row[2]
    confession_no = row[1]
    try:
        await bot.send_message(
            user_id,
            "╭━━━ ❌ <b>İtirafın Reddedildi</b> ❌ ━━━╮\n"
            f"┃ 🆔 Numaran: <code>#{confession_no}</code>\n"
            "╰━━━━━━━━━━━━━━━━━━━━━━╯"
        )
    except Exception:
        pass

    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer(
        f"❌ İtiraf reddedildi.\n🆔 <code>#{confession_no}</code>"
    )
    await callback.answer("Reddedildi")

@dp.callback_query(F.data.startswith("report:"))
async def report_confession(callback: CallbackQuery):
    confession_no = int(callback.data.split(":")[1])
    reporter = callback.from_user

    exists = await report_exists(confession_no, reporter.id)
    if exists:
        return await callback.answer("Bu itirafı zaten şikayet ettin.", show_alert=True)

    await save_report(confession_no, reporter.id, reporter.username or "")

    username_text = f"@{escape(reporter.username)}" if reporter.username else "yok"

    report_text = (
        "🚨 <b>Yeni Şikayet Geldi</b>\n\n"
        f"🆔 İtiraf No: <code>#{confession_no}</code>\n"
        f"👤 Şikayet Eden ID: <code>{reporter.id}</code>\n"
        f"🙍 Ad: {escape(reporter.first_name or '')}\n"
        f"🔗 Kullanıcı Adı: {username_text}"
    )
    await send_to_all_admins(report_text)
    await callback.answer("Şikayetin adminlere iletildi.", show_alert=True)

async def handle_broadcast_message(message: Message) -> bool:
    if message.from_user.id not in broadcast_waiting_admins:
        return False
    if not is_admin(message.from_user.id):
        broadcast_waiting_admins.discard(message.from_user.id)
        return False
    if message.text and message.text.startswith("/"):
        return False

    text = message.text or message.caption
    if not text:
        await message.answer("❌ Duyuru olarak şimdilik sadece metin destekleniyor.")
        return True

    sent, failed = await broadcast_to_all_users(
        "╔══════════════╗\n"
        "║ 📣 <b>DUYURU</b>\n"
        "╚══════════════╝\n\n"
        f"{escape(text)}"
    )
    broadcast_waiting_admins.discard(message.from_user.id)
    await message.answer(f"✅ Duyuru tamamlandı.\nGönderilen: <code>{sent}</code>\nBaşarısız: <code>{failed}</code>")
    return True

async def handle_edit_message(message: Message) -> bool:
    admin_id = message.from_user.id
    if admin_id not in edit_waiting_admins:
        return False
    if not is_admin(admin_id):
        edit_waiting_admins.pop(admin_id, None)
        return False
    if message.chat.type != ChatType.PRIVATE:
        return True
    if not message.text or message.text.startswith("/"):
        await message.answer("⚠️ Lütfen yeni metni düz yazı olarak gönder.")
        return True

    db_id = edit_waiting_admins[admin_id]
    row = await get_confession_by_id(db_id)
    if not row:
        edit_waiting_admins.pop(admin_id, None)
        await message.answer("❌ İtiraf bulunamadı.")
        return True

    if row[7] != "pending":
        edit_waiting_admins.pop(admin_id, None)
        await message.answer("❌ Bu itiraf artık beklemede değil.")
        return True

    new_text = message.text.strip()
    if len(new_text) < 3:
        await message.answer("⚠️ Yeni metin çok kısa.")
        return True
    if len(new_text) > CONFIG["max_text_length"]:
        await message.answer(f"⚠️ Yeni metin çok uzun. En fazla {CONFIG['max_text_length']} karakter.")
        return True

    await update_confession_text(db_id, new_text)
    edit_waiting_admins.pop(admin_id, None)

    confession_no = row[1]
    user_id = row[2]
    content_type = row[3]

    await message.answer(
        "✏️ <b>İtiraf güncellendi</b>\n\n"
        f"🆔 <code>#{confession_no}</code>\n"
        "Şimdi yeniden inceleyip onaylayabilirsin."
    )

    admin_text = premium_admin_preview(
        db_id=db_id,
        confession_no=confession_no,
        user_id=user_id,
        content_type=content_type,
        text=new_text
    )

    await send_to_all_admins(admin_text, reply_markup=confession_admin_keyboard(db_id))
    return True

async def process_confession_submission(message: Message):
    await upsert_user(message)

    if message.chat.type != ChatType.PRIVATE:
        return

    if message.text and message.text.startswith("/"):
        return

    if await handle_broadcast_message(message):
        return

    if await handle_edit_message(message):
        return

    if await is_banned_user(message.from_user.id):
        return await message.answer("🚫 Bu botu kullanman engellenmiş.")

    recent = await count_recent_confessions(message.from_user.id, CONFIG["cooldown_seconds"])
    if recent > 0:
        return await message.answer(f"⏳ Çok hızlı gönderim yapıyorsun. {CONFIG['cooldown_seconds']} saniye bekle.")

    content_type = None
    text = ""
    media_file_id = None
    media_file_unique_id = None

    if message.content_type == ContentType.TEXT:
        content_type = "text"
        text = (message.text or "").strip()
    elif message.content_type == ContentType.PHOTO:
        content_type = "photo"
        photo = message.photo[-1]
        media_file_id = photo.file_id
        media_file_unique_id = photo.file_unique_id
        text = (message.caption or "").strip()
    elif message.content_type == ContentType.VIDEO:
        content_type = "video"
        media_file_id = message.video.file_id
        media_file_unique_id = message.video.file_unique_id
        text = (message.caption or "").strip()
    elif message.content_type == ContentType.DOCUMENT:
        content_type = "document"
        media_file_id = message.document.file_id
        media_file_unique_id = message.document.file_unique_id
        text = (message.caption or "").strip()
    elif message.content_type == ContentType.AUDIO:
        content_type = "audio"
        media_file_id = message.audio.file_id
        media_file_unique_id = message.audio.file_unique_id
        text = (message.caption or "").strip()
    elif message.content_type == ContentType.VOICE:
        content_type = "voice"
        media_file_id = message.voice.file_id
        media_file_unique_id = message.voice.file_unique_id
        text = (message.caption or "").strip()
    elif message.content_type == ContentType.STICKER:
        content_type = "sticker"
        media_file_id = message.sticker.file_id
        media_file_unique_id = message.sticker.file_unique_id
        text = "(Sticker itirafı)"
    else:
        return await message.answer("⚠️ Desteklenmeyen içerik türü.")

    if content_type == "text":
        if len(text) < 3:
            return await message.answer("⚠️ İtiraf çok kısa.")
        if len(text) > CONFIG["max_text_length"]:
            return await message.answer(f"⚠️ İtiraf çok uzun. En fazla {CONFIG['max_text_length']} karakter olabilir.")

    if text and is_spam_text(text):
        return await message.answer("🚫 Mesajın spam/link/reklam içeriyor gibi görünüyor.")

    db_id, confession_no = await create_confession_record(
        user_id=message.from_user.id,
        content_type=content_type,
        text=text,
        media_file_id=media_file_id,
        media_file_unique_id=media_file_unique_id
    )

    admin_text = premium_admin_preview(
        db_id=db_id,
        confession_no=confession_no,
        user_id=message.from_user.id,
        content_type=content_type,
        text=text
    )

    for admin_id in ADMIN_IDS:
        try:
            if content_type == "text":
                await bot.send_message(admin_id, admin_text, reply_markup=confession_admin_keyboard(db_id))
            elif content_type == "photo":
                await bot.send_photo(admin_id, media_file_id, caption=admin_text, reply_markup=confession_admin_keyboard(db_id))
            elif content_type == "video":
                await bot.send_video(admin_id, media_file_id, caption=admin_text, reply_markup=confession_admin_keyboard(db_id))
            elif content_type == "document":
                await bot.send_document(admin_id, media_file_id, caption=admin_text, reply_markup=confession_admin_keyboard(db_id))
            elif content_type == "audio":
                await bot.send_audio(admin_id, media_file_id, caption=admin_text, reply_markup=confession_admin_keyboard(db_id))
            elif content_type == "voice":
                await bot.send_voice(admin_id, media_file_id, caption=admin_text, reply_markup=confession_admin_keyboard(db_id))
            elif content_type == "sticker":
                await bot.send_sticker(admin_id, media_file_id)
                await bot.send_message(admin_id, admin_text, reply_markup=confession_admin_keyboard(db_id))
            else:
                await bot.send_message(admin_id, admin_text, reply_markup=confession_admin_keyboard(db_id))
        except Exception as e:
            logging.error(f"Admin forward hatası: {e}")

    await message.answer(premium_user_received(confession_no))

@dp.channel_post()
async def channel_post_handler(message: Message):
    try:
        info_text = (
            "📢 <b>Kanal Bilgisi Alındı</b>\n\n"
            f"Kanal adı: {escape(message.chat.title or 'Bilinmiyor')}\n"
            f"Kanal ID: <code>{message.chat.id}</code>"
        )
        await send_to_all_admins(info_text)
    except Exception as e:
        logging.error(f"Kanal bilgisi gönderilemedi: {e}")

@dp.message()
async def all_private_content_handler(message: Message):
    await process_confession_submission(message)

async def main():
    await init_db()
    CONFIG["cooldown_seconds"] = int(os.getenv("COOLDOWN_SECONDS", "60"))
    CONFIG["max_text_length"] = int(os.getenv("MAX_TEXT_LENGTH", "3000"))
    CONFIG["auto_publish"] = os.getenv("AUTO_PUBLISH", "true").lower() == "true"
    asyncio.create_task(publisher_loop())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
