import streamlit as st
import subprocess
import threading

# Function to run the background script
def run_troy_script():
    subprocess.Popen(["python", "troy.py"])

# Run the background script in a separate thread to avoid blocking
threading.Thread(target=run_troy_script, daemon=True).start()

# Streamlit frontend content
st.title("Demo App")
st.write("This is a demo text shown on the desktop UI.")
# --- Streamlit UI Imports ---
import streamlit as st

# --- Bot Imports ---
import os
import asyncio
import logging
import sys
import threading # Import threading
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any
from concurrent.futures import ThreadPoolExecutor
import weakref

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackQueryHandler, ConversationHandler, ContextTypes
from telegram.constants import ParseMode
import pymongo
from pymongo import MongoClient
import warnings
from cryptography.utils import CryptographyDeprecationWarning

# Fix Windows console encoding issues (Optional)
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except:
        pass

# Suppress warnings
warnings.filterwarnings("ignore", category=CryptographyDeprecationWarning)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.StreamHandler(),
    ]
)
logger = logging.getLogger(__name__)


# --- Streamlit UI Elements ---
st.title("🎈 My Telegram Bot App")
st.write(
    "This page is running a backend Telegram bot. Use the bot directly on Telegram!"
)
st.write("For help and inspiration on building Streamlit apps, head over to [docs.streamlit.io](https://docs.streamlit.io/).")


# --- BOT CONFIGURATION (HARDCODED - INSECURE!) ---
# WARNING: Hardcoding secrets like this is highly insecure.
# DO NOT put this code in a public repository.
# The recommended secure way is to use Streamlit Secrets (.streamlit/secrets.toml).

# Bot configuration (global constants)
API_TOKEN = '8047738165:AAGAU1InodqlYNYxS_ObzoPBWZyqR4FnxiI' # Double-check your token
ADMIN_ID = 5648376510

# MongoDB setup with connection pooling
MONGO_URI = 'mongodb+srv://allowdoctor:T3OtPNZe3wVgGzhQ@tgbotwd.u6kjv.mongodb.net/?retryWrites=true&w=majority&appName=Tgbotwd'


# --- Conversation states and Duration options ---
WAITING_FOR_FILE, CONFIRM_FILE = range(2)
ADMIN_CHAT_ID, ADMIN_DURATION, ADMIN_CONFIRM = range(2, 5)

DURATION_OPTIONS = {
    '1_day': {'days': 1, 'text': '1 Day'},
    '3_days': {'days': 3, 'text': '3 Days'},
    '7_days': {'days': 7, 'text': '7 Days'},
    '15_days': {'days': 15, 'text': '15 Days'},
    '30_days': {'days': 30, 'text': '30 Days'}
}

# Thread pool for CPU-intensive tasks (shared resource)
executor = ThreadPoolExecutor(max_workers=4)

# Global cleanup task reference
cleanup_task_ref = None


# --- DEFINE CLASSES *BEFORE* THEY ARE INSTANTIATED ---

class DatabaseManager:
    """Optimized database manager with connection pooling and caching."""

    def __init__(self, uri: str):
        self.client = MongoClient(
            uri,
            maxPoolSize=50,
            minPoolSize=5,
            maxIdleTimeMS=30000,
            socketTimeoutMS=10000,
            connectTimeoutMS=10000,
            serverSelectionTimeoutMS=5000
        )
        self.db = self.client['cryptbot']
        self.users_collection = self.db['premium_users']

        try:
            self.users_collection.create_index("user_id", unique=True)
            self.users_collection.create_index("expiry_date")
            logger.info("MongoDB indexes ensured.")
        except Exception as e:
             logger.warning(f"Failed to create MongoDB indexes: {e}")

        self._premium_cache = weakref.WeakValueDictionary()
        self._cache_timeout = 300 # 5 minutes cache timeout

    async def is_premium_user(self, user_id: int) -> bool:
        loop = asyncio.get_running_loop()
        try:
            cached_data = self._premium_cache.get(user_id)
            if cached_data and datetime.now(timezone.utc) < cached_data['expires']:
                return cached_data['is_premium']

            user = await loop.run_in_executor(
                executor,
                self.users_collection.find_one,
                {'user_id': user_id},
                {'expiry_date': 1}
            )

            is_premium = False
            if user:
                expiry_date = user.get('expiry_date')
                if expiry_date:
                    if expiry_date.tzinfo is None:
                        expiry_date = expiry_date.replace(tzinfo=timezone.utc)
                    is_premium = datetime.now(timezone.utc) < expiry_date

            cache_data = {
                'is_premium': is_premium,
                'expires': datetime.now(timezone.utc) + timedelta(seconds=self._cache_timeout)
            }
            self._premium_cache[user_id] = cache_data

            return is_premium

        except Exception as e:
            logger.error(f"Error checking premium status for user {user_id}: {e}")
            return False

    async def add_premium_user(self, user_id: int, duration_days: int):
        loop = asyncio.get_running_loop()
        try:
            expiry_date = datetime.now(timezone.utc) + timedelta(days=duration_days)

            await loop.run_in_executor(
                executor,
                self.users_collection.update_one,
                {'user_id': user_id},
                {
                    '$set': {
                        'user_id': user_id,
                        'expiry_date': expiry_date,
                        'added_by': ADMIN_ID, # ADMIN_ID is read from the hardcoded variable
                        'added_at': datetime.now(timezone.utc)
                    }
                },
                True
            )

            cache_data = {
                'is_premium': True,
                'expires': datetime.now(timezone.utc) + timedelta(seconds=self._cache_timeout)
            }
            self._premium_cache[user_id] = cache_data

            logger.info(f"Added premium user {user_id} with {duration_days} days")

        except Exception as e:
            logger.error(f"Error adding premium user {user_id}: {e}")

    async def get_premium_expiry(self, user_id: int) -> Optional[datetime]:
        loop = asyncio.get_running_loop()
        try:
            user = await loop.run_in_executor(
                executor,
                self.users_collection.find_one,
                {'user_id': user_id},
                {'expiry_date': 1}
            )

            if user:
                expiry_date = user.get('expiry_date')
                if expiry_date and expiry_date.tzinfo is None:
                    expiry_date = expiry_date.replace(tzinfo=timezone.utc)
                return expiry_date
            return None

        except Exception as e:
            logger.error(f"Error getting expiry for user {user_id}: {e}")
            return None


class UserSessionManager:
    """Manages user sessions for concurrent operations."""

    def __init__(self):
        self.user_sessions: Dict[int, Dict[str, Any]] = {}
        self.lock = asyncio.Lock()

    async def get_session(self, user_id: int) -> Dict[str, Any]:
        async with self.lock:
            if user_id not in self.user_sessions:
                self.user_sessions[user_id] = {
                    'processing': False,
                    'last_activity': datetime.now(timezone.utc),
                    'data': {}
                }
            return self.user_sessions[user_id]

    async def set_processing(self, user_id: int, processing: bool):
        session = await self.get_session(user_id)
        session['processing'] = processing
        session['last_activity'] = datetime.now(timezone.utc)

    async def is_processing(self, user_id: int) -> bool:
        session = await self.get_session(user_id)
        return session.get('processing', False)

    async def cleanup_old_sessions(self):
        cutoff_time = datetime.now(timezone.utc) - timedelta(hours=1)
        async with self.lock:
            to_remove = [
                user_id for user_id, session in self.user_sessions.items()
                if session['last_activity'] < cutoff_time
            ]
            for user_id in to_remove:
                del self.user_sessions[user_id]
            if to_remove:
                 logger.info(f"Cleaned up {len(to_remove)} old sessions.")


# --- INITIALIZE MANAGERS *AFTER* CLASS DEFINITIONS, USING HARDCODED VARS ---

try:
    db_manager = DatabaseManager(MONGO_URI) # Uses the hardcoded MONGO_URI
    # Optional: Check connection on startup
    db_manager.client.admin.command('ping')
    logger.info("MongoDB connected successfully.")
except Exception as e:
    # Log critical error but maybe allow Streamlit to run if DB is not absolutely essential for the UI part
    st.error(f"Failed to connect to MongoDB: {e}")
    logger.critical(f"MongoDB connection failed: {e}")
    # st.stop() # Uncomment this if the bot cannot function without the database


session_manager = UserSessionManager() # Uses the hardcoded variables internally via handlers

# --- BOT HANDLER FUNCTIONS ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Start command with quick response."""
    user_id = update.effective_user.id

    await update.message.reply_text("🔄 *Loading...*", parse_mode=ParseMode.MARKDOWN)

    if await session_manager.is_processing(user_id):
        await update.message.edit_text(
            "⚠️ *A generation process is already running*\n\n"
            "Please wait until it completes or use ❌ Cancel Job.",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    keyboard = [
        ['🔐 Start Generation', '📅 Subscription'],
        ['🛒 Purchase', '🆘 Need Help'],
        ['❌ Cancel Job']
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

    await update.message.edit_text(
        "🤖 *Welcome to InstallerBot!*\n\n"
        "✨ Use the buttons below to interact with the bot\n"
        "💎 Contact admin for premium access\n\n"
        "⚡ *Multi-user support enabled* - Multiple users can use the bot simultaneously!",
        reply_markup=reply_markup,
        parse_mode=ParseMode.MARKDOWN
    )

async def handle_menu_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle menu buttons with faster routing."""
    text = update.message.text
    user_id = update.effective_user.id

    handlers = {
        '🔐 Start Generation': crypt,
        '📅 Subscription': check,
        '🛒 Purchase': purchase,
        '🆘 Need Help': contact,
        '❌ Cancel Job': cancel
    }

    handler = handlers.get(text)
    if handler:
        await handler(update, context)
    else:
        await update.message.reply_text(
            "❌ Invalid option. Please use the menu buttons.",
            parse_mode=ParseMode.MARKDOWN
        )

async def crypt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Optimized crypt command."""
    user_id = update.effective_user.id

    if await session_manager.is_processing(user_id):
        await update.message.reply_text(
            "⚠️ *Generation already in progress*\n\n"
            "Please wait or use ❌ Cancel Job",
            parse_mode=ParseMode.MARKDOWN
        )
        return ConversationHandler.END

    # Check premium status using db_manager, ADMIN_ID is the hardcoded variable
    if not await db_manager.is_premium_user(user_id) and user_id != ADMIN_ID:
        await update.message.reply_text(
            "🔒 *Premium Access Required*\n\n"
            "💎 You need premium access to use this feature\n"
            "📞 Contact administrator to purchase subscription",
            parse_mode=ParseMode.MARKDOWN
        )
        return ConversationHandler.END

    await session_manager.set_processing(user_id, True)

    await update.message.reply_text(
        "📤 *Ready for file upload*\n\n"
        "📎 Send your file to begin processing\n"
        "❌ Use /cancel to stop anytime",
        parse_mode=ParseMode.MARKDOWN
    )

    return WAITING_FOR_FILE

async def handle_file_upload(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle file upload with async processing."""
    user_id = update.effective_user.id

    processing_msg = await update.message.reply_text(
        "⚡ *Processing file...*\n\n"
        "🔄 Please wait while we process your file\n"
        "⏱️ This may take a few moments",
        parse_mode=ParseMode.MARKDOWN
    )

    try:
        # --- Your actual file processing logic goes here ---
        # Use await loop.run_in_executor(executor, ...) for blocking I/O or CPU tasks
        # Example:
        # if update.message.document:
        #    document = update.message.document
        #    file_id = document.file_id
        #    new_file = await context.bot.get_file(file_id)
        #    file_bytes = await new_file.download_as_bytearray()
        #    # await loop.run_in_executor(executor, your_processing_function, file_bytes)
        # else:
        #    await processing_msg.edit_text("Please send a document file.", parse_mode=ParseMode.MARKDOWN)
        #    return ConversationHandler.END
        # --- End of actual file processing logic placeholder ---

        await asyncio.sleep(5) # Simulate time - REPLACE THIS

        await processing_msg.edit_text(
            "✅ *File processed successfully!*\n\n"
            "🎉 Your file has been processed\n"
            "📥 Ready for next operation",
            parse_mode=ParseMode.MARKDOWN
        )

    except Exception as e:
        logger.error(f"Error processing file for user {user_id}: {e}")
        await processing_msg.edit_text(
            "❌ *Processing failed*\n\n"
            "🔄 Please try again or contact support\n"
            f"Error: {e}",
            parse_mode=ParseMode.MARKDOWN
        )

    finally:
        await session_manager.set_processing(user_id, False)

    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancel operation."""
    user_id = update.effective_user.id
    await session_manager.set_processing(user_id, False)

    await update.message.reply_text(
        "❌ *Operation cancelled*\n\n"
        "🔄 Use 🔐 Start Generation to begin again",
        parse_mode=ParseMode.MARKDOWN
    )
    return ConversationHandler.END

async def check(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Check subscription status."""
    user_id = update.effective_user.id

    await update.message.reply_text("🔍 *Checking subscription...*", parse_mode=ParseMode.MARKDOWN)

    expiry_date = await db_manager.get_premium_expiry(user_id)

    if expiry_date:
        now_utc = datetime.now(timezone.utc)
        if expiry_date > now_utc:
            time_difference = expiry_date - now_utc
            days_left = time_difference.days
            hours, remainder = divmod(time_difference.seconds, 3600)
            minutes, seconds = divmod(remainder, 60)

            await update.message.edit_text(
                f"✅ *Premium Active*\n\n"
                f"📅 Expires: `{expiry_date.strftime('%Y-%m-%d %H:%M:%S')} UTC`\n"
                f"⏰ Time remaining: *{days_left} days, {hours} hours, {minutes} minutes*",
                parse_mode=ParseMode.MARKDOWN
            )
        else:
             await update.message.edit_text(
                "❌ *Premium Expired*\n\n"
                f"📅 Expired on: `{expiry_date.strftime('%Y-%m-%d %H:%M:%S')} UTC`\n"
                "🛒 Use Purchase button to renew subscription",
                parse_mode=ParseMode.MARKDOWN
            )
    else:
        await update.message.edit_text(
            "❌ *No Active Subscription*\n\n"
            "💎 Premium access required\n"
            "🛒 Use Purchase button to buy subscription",
            parse_mode=ParseMode.MARKDOWN
        )

async def purchase(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Purchase handler."""
    keyboard = [
        [InlineKeyboardButton("🛒 Purchase Premium", url="https://example.com")] # Replace with your actual link
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        "💎 *Premium Access*\n\n"
        "🚀 Unlock unlimited features\n"
        "⚡ Priority processing\n"
        "🔒 Secure file handling\n\n"
        "👇 Click below to purchase:",
        reply_markup=reply_markup,
        parse_mode=ParseMode.MARKDOWN
    )

async def contact(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Contact information."""
    await update.message.reply_text(
        "📞 *Need Help?*\n\n"
        "💬 Contact our support team:\n"
        "📧 Email: support@example.com\n" # Replace with your actual email
        "🆔 Telegram: @admin_username\n\n" # Replace with your actual username
        "⚡ We respond within 24 hours!",
        parse_mode=ParseMode.MARKDOWN
    )

# Admin handlers
async def admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Admin command."""
    user_id = update.effective_user.id
    # Uses the hardcoded ADMIN_ID variable
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ *Unauthorized*", parse_mode=ParseMode.MARKDOWN)
        return ConversationHandler.END

    await update.message.reply_text(
        "🔧 *Admin Panel*\n\n"
        "👤 Enter user ID to grant premium access:",
        parse_mode=ParseMode.MARKDOWN
    )
    return ADMIN_CHAT_ID

async def admin_chat_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle admin chat ID input."""
    try:
        chat_id = int(update.message.text)
        context.user_data['premium_chat_id'] = chat_id

        keyboard = [
            [InlineKeyboardButton(data['text'], callback_data=f"duration_{key}")]
            for key, data in DURATION_OPTIONS.items()
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await update.message.reply_text(
            f"⏰ *Select Duration*\n\n"
            f"👤 User ID: `{chat_id}`\n"
            f"📅 Choose premium duration:",
            reply_markup=reply_markup,
            parse_mode=ParseMode.MARKDOWN
        )
        return ADMIN_DURATION
    except ValueError:
        await update.message.reply_text("❌ Invalid ID. Enter a number:")
        return ADMIN_CHAT_ID

async def admin_duration(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle duration selection."""
    query = update.callback_query
    await query.answer()

    duration_key = query.data.replace('duration_', '')
    chat_id = context.user_data['premium_chat_id']
    duration_data = DURATION_OPTIONS[duration_key]
    context.user_data['duration_days'] = duration_data['days']

    keyboard = [
        [
            InlineKeyboardButton("✅ Confirm", callback_data="confirm_yes"),
            InlineKeyboardButton("❌ Cancel", callback_data="confirm_no")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text(
        f"✅ *Confirm Premium Grant*\n\n"
        f"👤 User ID: `{chat_id}`\n"
        f"⏰ Duration: *{duration_data['text']}*\n\n"
        f"❓ Proceed with granting premium access?",
        reply_markup=reply_markup,
        parse_mode=ParseMode.MARKDOWN
    )
    return ADMIN_CONFIRM

async def admin_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle admin confirmation."""
    query = update.callback_query
    await query.answer()

    if query.data == "confirm_yes":
        chat_id = context.user_data['premium_chat_id']
        duration_days = context.user_data['duration_days']

        await db_manager.add_premium_user(chat_id, duration_days)
        expiry_date = datetime.now(timezone.utc) + timedelta(days=duration_days)

        await query.edit_message_text(
            f"✅ *Premium Granted Successfully!*\n\n"
            f"👤 User ID: `{chat_id}`\n"
            f"⏰ Duration: *{duration_days} days*\n"
            f"📅 Expires: `{expiry_date.strftime('%Y-%m-%d %H:%M:%S')} UTC`",
            parse_mode=ParseMode.MARKDOWN
        )
    else:
        await query.edit_message_text("❌ *Operation cancelled*", parse_mode=ParseMode.MARKDOWN)

    return ConversationHandler.END


async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Enhanced error handler."""
    logger.error(f'Error: {context.error}', exc_info=True)

    if update and update.effective_chat:
        try:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="❌ *An internal error occurred*\n\n"
                     "🔄 Please try again or contact support",
                parse_mode=ParseMode.MARKDOWN
            )
        except Exception as e:
            logger.error(f"Failed to send error message to chat {update.effective_chat.id}: {e}")

# Periodic cleanup task
async def cleanup_task():
    """Periodic cleanup of old sessions."""
    while True:
        try:
            await session_manager.cleanup_old_sessions()
            await asyncio.sleep(1800) # Run every 30 minutes
        except Exception as e:
            logger.error(f"Error in cleanup task: {e}")
            await asyncio.sleep(1800)

# Helper function to run the bot in a separate thread
def run_bot_polling():
    """Builds and runs the Telegram bot application in its own asyncio loop within a thread."""
    global cleanup_task_ref
    
    logger.info("Setting up asyncio loop for Telegram bot thread...")
    # Create a new event loop for this thread
    loop = asyncio.new_event_loop()
    # Set it as the current event loop for this thread
    asyncio.set_event_loop(loop)
    logger.info("Asyncio loop created and set for bot thread.")

    try:
        # Create application using the hardcoded API_TOKEN
        application = Application.builder().token(API_TOKEN).build()

        # --- Add Handlers ---
        # Fixed: Removed per_message=True to avoid warnings
        admin_handler = ConversationHandler(
            entry_points=[CommandHandler('admin', admin)],
            states={
                ADMIN_CHAT_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_chat_id)],
                ADMIN_DURATION: [CallbackQueryHandler(admin_duration, pattern=r'^duration_')],
                ADMIN_CONFIRM: [CallbackQueryHandler(admin_confirm, pattern=r'^confirm_')]
            },
            fallbacks=[CommandHandler('cancel', cancel)]
        )

        conv_handler = ConversationHandler(
            entry_points=[
                CommandHandler('crypt', crypt),
                MessageHandler(filters.Text(['🔐 Start Generation']), crypt)
            ],
            states={
                WAITING_FOR_FILE: [
                    MessageHandler(filters.Document.ALL, handle_file_upload),
                ]
            },
            fallbacks=[CommandHandler('cancel', cancel)]
        )

        application.add_handler(admin_handler)
        application.add_handler(conv_handler)
        application.add_handler(CommandHandler('start', start))
        application.add_handler(CommandHandler('check', check))
        application.add_handler(CommandHandler('cancel', cancel))
        application.add_handler(CommandHandler('contact', contact))
        application.add_handler(CommandHandler('purchase', purchase))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_menu_buttons))
        application.add_error_handler(error_handler)

        # --- Post Init/Shutdown Hooks ---
        async def post_init(app):
            global cleanup_task_ref
            logger.info("Bot post_init hook running in bot loop...")
            # Store cleanup task reference globally instead of on app object
            cleanup_task_ref = asyncio.create_task(cleanup_task())
            logger.info("Cleanup task scheduled in bot loop.")

        application.post_init = post_init

        async def post_shutdown(app):
            global cleanup_task_ref
            logger.info("Bot post_shutdown hook running in bot loop...")
            if cleanup_task_ref:
                cleanup_task_ref.cancel()
                try:
                    await cleanup_task_ref
                except asyncio.CancelledError:
                    logger.info("Cleanup task cancelled successfully.")
            logger.info("Bot finished shutting down.")

        application.post_shutdown = post_shutdown

        logger.info("Bot application built. Running polling inside loop...")
        # Run the application's polling async method until it completes
        # This will block THIS thread until the bot stops
        loop.run_until_complete(application.run_polling(allowed_updates=Update.ALL_TYPES, stop_signals=None))

    except Exception as e:
        logger.critical(f"Telegram bot thread encountered a critical error: {e}", exc_info=True)
    finally:
        # Clean up the loop when run_polling finishes (e.g., on shutdown)
        loop.close()
        logger.info("Bot thread event loop closed.")


# --- Main Execution Block ---
# Global variables to track bot thread state
_bot_thread_started = False
_bot_thread = None

if __name__ == '__main__':
    logger.info("Streamlit script started.")

    if not _bot_thread_started:
        logger.info("Starting bot thread...")
        # Start run_bot_polling in a new thread
        _bot_thread = threading.Thread(target=run_bot_polling, daemon=True)
        _bot_thread.start()
        _bot_thread_started = True
        logger.info("Bot thread started.")
    else:
        logger.info("Bot thread is already running.")

    st.write("Bot backend is running in a separate thread.")
    st.write("Check your Telegram bot for functionality.")
