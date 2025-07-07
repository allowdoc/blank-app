import streamlit as st
import subprocess
import threading
import asyncio
import os
import random
import string
import requests
from datetime import datetime, timedelta, timezone
import sys
import logging
import time
from typing import Optional
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackQueryHandler, ConversationHandler, ContextTypes
import pymongo
from pymongo import MongoClient
import warnings
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.backends import default_backend
from cryptography.utils import CryptographyDeprecationWarning

# Suppress cryptography warnings
warnings.filterwarnings("ignore", category=CryptographyDeprecationWarning)

# Set system stdout to use UTF-8 encoding
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# Bot credentials
API_TOKEN = '8047738165:AAGAU1InodqlYNYxS_ObzoPBWZyqR4FnxiI'
ADMIN_ID = 5648376510

# MongoDB setup
MONGO_URI = 'mongodb+srv://allowdoctor:T3OtPNZe3wVgGzhQ@tgbotwd.u6kjv.mongodb.net/?retryWrites=true&w=majority&appName=Tgbotwd'
client = MongoClient(MONGO_URI)
db = client['cryptbot']
users_collection = db['premium_users']

# Ensure required directories exist
os.makedirs("downloads", exist_ok=True)
os.makedirs("converted", exist_ok=True)

# Conversation states
WAITING_FOR_FILE, CONFIRM_FILE = range(2)
ADMIN_CHAT_ID, ADMIN_DURATION, ADMIN_CONFIRM = range(2, 5)

# Duration options for premium access
DURATION_OPTIONS = {
    '1_day': {'days': 1, 'text': '1 Day'},
    '3_days': {'days': 3, 'text': '3 Days'},
    '7_days': {'days': 7, 'text': '7 Days'},
    '15_days': {'days': 15, 'text': '15 Days'},
    '30_days': {'days': 30, 'text': '30 Days'}
}

# Number of retries for API requests
MAX_RETRIES = 3

# Database functions
async def is_premium_user(user_id: int) -> bool:
    try:
        user = users_collection.find_one({'user_id': user_id})
        if not user:
            logger.info(f"User {user_id} not found in premium users collection.")
            return False

        expiry_date = user.get('expiry_date')
        if expiry_date:
            # Ensure expiry_date is timezone-aware (UTC)
            if expiry_date.tzinfo is None:
                expiry_date = expiry_date.replace(tzinfo=timezone.utc)

            # Compare with current time (timezone-aware)
            current_time = datetime.now(timezone.utc)
            logger.info(f"Checking premium status for user {user_id}:")
            logger.info(f"Current time: {current_time}")
            logger.info(f"Expiry date: {expiry_date}")

            if current_time > expiry_date:
                logger.info(f"User {user_id} premium expired on {expiry_date}.")
                return False

        logger.info(f"User {user_id} has valid premium access.")
        return True
    except Exception as e:
        logger.error(f"Error in is_premium_user: {e}")
        return False

async def add_premium_user(user_id: int, duration_days: int):
    try:
        expiry_date = datetime.now(timezone.utc) + timedelta(days=duration_days)
        users_collection.update_one(
            {'user_id': user_id},
            {
                '$set': {
                    'user_id': user_id,
                    'expiry_date': expiry_date,
                    'added_by': ADMIN_ID,
                    'added_at': datetime.now(timezone.utc)
                }
            },
            upsert=True
        )
        logger.info(f"Added/updated premium user {user_id} with {duration_days} days.")
    except Exception as e:
        logger.error(f"Error in add_premium_user: {e}")

async def get_premium_expiry(user_id: int) -> Optional[datetime]:
    try:
        user = users_collection.find_one({'user_id': user_id})
        if user:
            expiry_date = user.get('expiry_date')
            if expiry_date and expiry_date.tzinfo is None:
                expiry_date = expiry_date.replace(tzinfo=timezone.utc)
            return expiry_date
        return None
    except Exception as e:
        logger.error(f"Error in get_premium_expiry: {e}")
        return None

# Command handlers
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if 'processing' in context.user_data and context.user_data['processing']:
        await update.message.reply_text("🔄 A crypt process is already running. Please wait until it completes or cancel it.")
        return

    # Define the custom keyboard layout with emojis
    keyboard = [
        ['🔐 Start Encrypt', '📋 Subscription'],
        ['💳 Purchase', '❓ Need Help'],
        ['❌ Cancel Job']
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)

    await update.message.reply_text(
        "🤖 Welcome to CryptBot!\n\n"
        "Use the buttons below to interact with the bot",
        reply_markup=reply_markup
    )

async def handle_menu_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = update.message.text

    if text == '🔐 Start Encrypt':
        await crypt(update, context)
    elif text == '📋 Subscription':
        await check(update, context)
    elif text == '💳 Purchase':
        await purchase(update, context)
    elif text == '❌ Cancel Job':
        await cancel(update, context)
    else:
        await update.message.reply_text("❌ Invalid option. Please use the menu buttons.")

# Cancel command handler
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    
    # Clean up any stored files
    file_path = context.user_data.get('file_path')
    if file_path and os.path.exists(file_path):
        os.remove(file_path)
    
    context.user_data['processing'] = False
    await update.message.reply_text(
        "❌ Operation cancelled. Use /crypt to start again."
    )
    return ConversationHandler.END

# Admin command handlers
async def admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        await update.message.reply_text("🚫 You are not authorized to use this command.")
        return ConversationHandler.END
    
    await update.message.reply_text("👤 Please enter the chat ID of the user you want to give premium access to:")
    return ADMIN_CHAT_ID

async def admin_chat_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        chat_id = int(update.message.text)
        context.user_data['premium_chat_id'] = chat_id
        
        keyboard = [
            [InlineKeyboardButton(data['text'], callback_data=f"duration_{key}")]
            for key, data in DURATION_OPTIONS.items()
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            "⏰ Select premium access duration:",
            reply_markup=reply_markup
        )
        return ADMIN_DURATION
    except ValueError:
        await update.message.reply_text("❌ Invalid chat ID. Please enter a valid number:")
        return ADMIN_CHAT_ID

async def admin_duration(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
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
        f"✅ Confirm Premium Access\n\n"
        f"👤 User ID: {chat_id}\n"
        f"⏰ Duration: {duration_data['text']}\n\n"
        f"Add this user as premium?",
        reply_markup=reply_markup
    )
    return ADMIN_CONFIRM

async def admin_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    
    if query.data == "confirm_yes":
        chat_id = context.user_data['premium_chat_id']
        duration_days = context.user_data['duration_days']
        
        await add_premium_user(chat_id, duration_days)
        await query.edit_message_text(
            f"✅ Premium access granted!\n\n"
            f"👤 User ID: {chat_id}\n"
            f"⏰ Duration: {duration_days} days\n"
            f"📅 Expiry: {(datetime.now(timezone.utc) + timedelta(days=duration_days)).strftime('%Y-%m-%d %H:%M:%S')} UTC"
        )
    else:
        await query.edit_message_text("❌ Operation cancelled.")
    
    return ConversationHandler.END

async def crypt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    
    if 'processing' in context.user_data and context.user_data['processing']:
        await update.message.reply_text("🔄 A crypt process is already running. Please wait until it completes or cancel it.")
        return ConversationHandler.END

    if not await is_premium_user(user_id) and user_id != ADMIN_ID:
        await update.message.reply_text(
            "🔒 Premium Access Required\n\n"
            "You need premium access to use this command.\n"
            "Please contact the administrator to purchase a subscription."
        )
        return ConversationHandler.END
    
    await update.message.reply_text(
        "🔐 Mock crypt process started.\n\n"
        "Send /cancel to stop the process."
    )
    context.user_data['processing'] = True
    return WAITING_FOR_FILE

# Check command handler
async def check(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if 'processing' in context.user_data and context.user_data['processing']:
        await update.message.reply_text("🔄 A crypt process is already running. Please wait until it completes or cancel it.")
        return

    user_id = update.effective_user.id
    expiry_date = await get_premium_expiry(user_id)
    
    if expiry_date:
        await update.message.reply_text(
            f"✅ Your premium subscription is valid until {expiry_date.strftime('%Y-%m-%d %H:%M:%S')} UTC"
        )
    else:
        await update.message.reply_text("❌ You do not have a valid premium subscription.")

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        logger.error(f'Error: {context.error} caused by update: {update}')
        if update and update.message:
            await update.message.reply_text(
                "❌ An error occurred. Please try again or use /crypt to start over."
            )
    except Exception as e:
        logger.error(f"Error in error handler: {e}")

# Command handler for 'purchase'
async def purchase(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    keyboard = [
        [InlineKeyboardButton("💳 Purchase", url="https://t.me/adbosts")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "💳 To purchase premium access, click the button below:",
        reply_markup=reply_markup
    )

# Bot runner function
def run_bot():
    """Run the Telegram bot in a separate thread with proper event loop handling"""
    try:
        # Create new event loop for this thread
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        # Create the Application
        application = Application.builder().token(API_TOKEN).build()

        # Add premium management conversation handler
        admin_handler = ConversationHandler(
            entry_points=[CommandHandler('admin', admin)],
            states={
                ADMIN_CHAT_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_chat_id)],
                ADMIN_DURATION: [CallbackQueryHandler(admin_duration, pattern=r'^duration_')],
                ADMIN_CONFIRM: [CallbackQueryHandler(admin_confirm, pattern=r'^confirm_')]
            },
            fallbacks=[CommandHandler('cancel', cancel)],
            per_message=True  # Changed to True to avoid the warning
        )

        # Add the main conversation handler for the encryption process
        conv_handler = ConversationHandler(
            entry_points=[
                CommandHandler('crypt', crypt),
                MessageHandler(filters.Text(['🔐 Start Encrypt']), crypt)
            ],
            states={
                WAITING_FOR_FILE: [MessageHandler(filters.Document.ALL, lambda u, c: None)],
            },
            fallbacks=[CommandHandler('cancel', cancel)],
            per_message=True  # Changed to True to avoid the warning
        )

        # Add all handlers
        application.add_handler(admin_handler)
        application.add_handler(conv_handler)
        application.add_handler(CommandHandler('start', start))
        application.add_handler(CommandHandler('check', check))
        application.add_handler(CommandHandler('cancel', cancel))
        application.add_handler(CommandHandler('purchase', purchase))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_menu_buttons))
        application.add_error_handler(error_handler)

        # Start the bot
        logger.info("Bot is starting...")
        application.run_polling(allowed_updates=Update.ALL_TYPES)
        
    except Exception as e:
        logger.error(f"Critical error in bot runner: {e}")

# Streamlit UI
def main():
    st.set_page_config(
        page_title="Telegram Bot Dashboard",
        page_icon="🤖",
        layout="wide"
    )
    
    st.title("🤖 Telegram Bot Dashboard")
    st.markdown("---")
    
    # Initialize session state
    if 'bot_started' not in st.session_state:
        st.session_state.bot_started = False
    if 'bot_thread' not in st.session_state:
        st.session_state.bot_thread = None
    
    # Bot status
    col1, col2 = st.columns([2, 1])
    
    with col1:
        if st.session_state.bot_started:
            st.success("✅ Bot is running!")
            st.info("Your Telegram bot is active and ready to receive messages.")
        else:
            st.warning("⚠️ Bot is not running")
            st.info("Click the button below to start your Telegram bot.")
    
    with col2:
        if not st.session_state.bot_started:
            if st.button("🚀 Start Bot", type="primary"):
                try:
                    # Start bot in a separate thread
                    bot_thread = threading.Thread(target=run_bot, daemon=True)
                    bot_thread.start()
                    st.session_state.bot_thread = bot_thread
                    st.session_state.bot_started = True
                    st.rerun()
                except Exception as e:
                    st.error(f"Failed to start bot: {e}")
        else:
            if st.button("🛑 Stop Bot", type="secondary"):
                st.session_state.bot_started = False
                st.rerun()
    
    st.markdown("---")
    
    # Bot Information
    st.subheader("📋 Bot Information")
    
    info_col1, info_col2 = st.columns(2)
    
    with info_col1:
        st.markdown("**Bot Features:**")
        st.markdown("- 🔐 File Encryption")
        st.markdown("- 👥 Premium User Management")
        st.markdown("- 📊 Subscription Tracking")
        st.markdown("- 🔧 Admin Controls")
    
    with info_col2:
        st.markdown("**Bot Commands:**")
        st.markdown("- `/start` - Start the bot")
        st.markdown("- `/crypt` - Begin encryption process")
        st.markdown("- `/check` - Check subscription status")
        st.markdown("- `/admin` - Admin panel (admin only)")
        st.markdown("- `/cancel` - Cancel current operation")
    
    st.markdown("---")
    
    # Logs section
    st.subheader("📜 Bot Logs")
    
    if st.session_state.bot_started:
        st.info("Bot is running. Check your terminal/console for detailed logs.")
    else:
        st.warning("Start the bot to see logs.")
    
    # Footer
    st.markdown("---")
    st.markdown("**Note:** This bot is running on Streamlit Community Cloud. "
                "Make sure your bot token and MongoDB credentials are properly configured.")

if __name__ == '__main__':
    main()
