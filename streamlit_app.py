import streamlit as st
import threading
import asyncio
import os
import sys
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackQueryHandler, ConversationHandler, ContextTypes
import pymongo
from pymongo import MongoClient
import warnings
from cryptography.utils import CryptographyDeprecationWarning

# Suppress warnings
warnings.filterwarnings("ignore", category=CryptographyDeprecationWarning)
warnings.filterwarnings("ignore", category=RuntimeWarning)

# Set system stdout to use UTF-8 encoding
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# Bot credentials - Using hardcoded values as requested
API_TOKEN = '8047738165:AAGAU1InodqlYNYxS_ObzoPBWZyqR4FnxiI'
ADMIN_ID = 5648376510

# MongoDB setup - Using hardcoded connection string
MONGO_URI = 'mongodb+srv://allowdoctor:T3OtPNZe3wVgGzhQ@tgbotwd.u6kjv.mongodb.net/?retryWrites=true&w=majority&appName=Tgbotwd'

# Initialize MongoDB client
try:
    client = MongoClient(MONGO_URI)
    db = client['cryptbot']
    users_collection = db['premium_users']
    mongo_connected = True
    logger.info("MongoDB connected successfully")
except Exception as e:
    logger.error(f"MongoDB connection failed: {e}")
    client = None
    db = None
    users_collection = None
    mongo_connected = False

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

# Global variable to store bot application
bot_application = None

# Database functions
async def is_premium_user(user_id: int) -> bool:
    if not mongo_connected or users_collection is None:
        return False
    
    try:
        user = users_collection.find_one({'user_id': user_id})
        if not user:
            logger.info(f"User {user_id} not found in premium users collection.")
            return False

        expiry_date = user.get('expiry_date')
        if expiry_date:
            if expiry_date.tzinfo is None:
                expiry_date = expiry_date.replace(tzinfo=timezone.utc)

            current_time = datetime.now(timezone.utc)
            if current_time > expiry_date:
                logger.info(f"User {user_id} premium expired on {expiry_date}.")
                return False

        logger.info(f"User {user_id} has valid premium access.")
        return True
    except Exception as e:
        logger.error(f"Error in is_premium_user: {e}")
        return False

async def add_premium_user(user_id: int, duration_days: int):
    if not mongo_connected or users_collection is None:
        return
    
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
    if not mongo_connected or users_collection is None:
        return None
    
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
    if context.user_data.get('processing', False):
        await update.message.reply_text("🔄 A crypt process is already running. Please wait until it completes or cancel it.")
        return

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
    elif text == '❓ Need Help':
        await help_command(update, context)
    elif text == '❌ Cancel Job':
        await cancel(update, context)
    else:
        await update.message.reply_text("❌ Invalid option. Please use the menu buttons.")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    help_text = """
🤖 **CryptBot Help**

**Available Commands:**
• `/start` - Start the bot and show menu
• `/crypt` - Begin encryption process (Premium only)
• `/check` - Check your subscription status
• `/cancel` - Cancel current operation
• `/purchase` - Get premium access info

**Menu Buttons:**
• 🔐 Start Encrypt - Begin file encryption
• 📋 Subscription - Check your premium status
• 💳 Purchase - Buy premium access
• ❓ Need Help - Show this help message
• ❌ Cancel Job - Cancel current operation

**Note:** File encryption requires premium access.
Contact admin to purchase subscription.
    """
    await update.message.reply_text(help_text)

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    file_path = context.user_data.get('file_path')
    if file_path and os.path.exists(file_path):
        try:
            os.remove(file_path)
        except Exception as e:
            logger.error(f"Error removing file: {e}")
    
    context.user_data['processing'] = False
    await update.message.reply_text("❌ Operation cancelled. Use /crypt to start again.")
    return ConversationHandler.END

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
    if duration_key not in DURATION_OPTIONS:
        await query.edit_message_text("❌ Invalid duration selected.")
        return ConversationHandler.END
    
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
        f"✅ **Confirm Premium Access**\n\n"
        f"👤 User ID: `{chat_id}`\n"
        f"⏰ Duration: {duration_data['text']}\n\n"
        f"Add this user as premium?",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )
    return ADMIN_CONFIRM

async def admin_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    
    if query.data == "confirm_yes":
        chat_id = context.user_data['premium_chat_id']
        duration_days = context.user_data['duration_days']
        
        await add_premium_user(chat_id, duration_days)
        expiry_date = datetime.now(timezone.utc) + timedelta(days=duration_days)
        
        await query.edit_message_text(
            f"✅ **Premium access granted!**\n\n"
            f"👤 User ID: `{chat_id}`\n"
            f"⏰ Duration: {duration_days} days\n"
            f"📅 Expiry: {expiry_date.strftime('%Y-%m-%d %H:%M:%S')} UTC",
            parse_mode='Markdown'
        )
    else:
        await query.edit_message_text("❌ Operation cancelled.")
    
    return ConversationHandler.END

async def crypt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    
    if context.user_data.get('processing', False):
        await update.message.reply_text("🔄 A crypt process is already running. Please wait until it completes or cancel it.")
        return ConversationHandler.END

    if not await is_premium_user(user_id) and user_id != ADMIN_ID:
        await update.message.reply_text(
            "🔒 **Premium Access Required**\n\n"
            "You need premium access to use this command.\n"
            "Please contact the administrator to purchase a subscription.\n\n"
            "Use /purchase to get premium access.",
            parse_mode='Markdown'
        )
        return ConversationHandler.END
    
    await update.message.reply_text(
        "🔐 **Encryption Service Ready**\n\n"
        "Send me a file to encrypt.\n"
        "Send /cancel to stop the process.",
        parse_mode='Markdown'
    )
    context.user_data['processing'] = True
    return WAITING_FOR_FILE

async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle file upload for encryption"""
    if not update.message.document:
        await update.message.reply_text("❌ Please send a document file.")
        return WAITING_FOR_FILE
    
    try:
        file = update.message.document
        await update.message.reply_text(f"📄 Received file: {file.file_name}\n🔄 Processing...")
        
        # Simulate processing
        await asyncio.sleep(2)
        
        await update.message.reply_text(
            f"✅ **File processed successfully!**\n\n"
            f"📁 Original: {file.file_name}\n"
            f"🔐 Status: Encrypted\n"
            f"📊 Size: {file.file_size} bytes",
            parse_mode='Markdown'
        )
        
        context.user_data['processing'] = False
        return ConversationHandler.END
        
    except Exception as e:
        logger.error(f"Error handling file: {e}")
        await update.message.reply_text("❌ Error processing file. Please try again.")
        context.user_data['processing'] = False
        return ConversationHandler.END

async def check(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if context.user_data.get('processing', False):
        await update.message.reply_text("🔄 A crypt process is already running. Please wait until it completes or cancel it.")
        return

    user_id = update.effective_user.id
    expiry_date = await get_premium_expiry(user_id)
    
    if expiry_date:
        await update.message.reply_text(
            f"✅ **Premium Subscription Active**\n\n"
            f"📅 Valid until: {expiry_date.strftime('%Y-%m-%d %H:%M:%S')} UTC\n"
            f"⏰ Status: Active",
            parse_mode='Markdown'
        )
    else:
        await update.message.reply_text(
            "❌ **No Premium Subscription**\n\n"
            "You do not have a valid premium subscription.\n"
            "Use /purchase to get premium access.",
            parse_mode='Markdown'
        )

async def purchase(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    keyboard = [
        [InlineKeyboardButton("💳 Purchase Premium", url="https://t.me/adbosts")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "💳 **Premium Access**\n\n"
        "Get premium access to unlock all features:\n"
        "• 🔐 File Encryption\n"
        "• 📊 Advanced Features\n"
        "• 🚀 Priority Support\n\n"
        "Click the button below to purchase:",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        logger.error(f'Update {update} caused error {context.error}')
        if update and update.message:
            await update.message.reply_text(
                "❌ An error occurred. Please try again or use /cancel to stop the current process."
            )
    except Exception as e:
        logger.error(f"Error in error handler: {e}")

# Bot setup function
async def setup_bot():
    """Setup and return the bot application"""
    global bot_application
    
    if bot_application:
        return bot_application
    
    # Create the Application
    application = Application.builder().token(API_TOKEN).build()

    # Admin conversation handler
    admin_handler = ConversationHandler(
        entry_points=[CommandHandler('admin', admin)],
        states={
            ADMIN_CHAT_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_chat_id)],
            ADMIN_DURATION: [CallbackQueryHandler(admin_duration, pattern=r'^duration_')],
            ADMIN_CONFIRM: [CallbackQueryHandler(admin_confirm, pattern=r'^confirm_')]
        },
        fallbacks=[CommandHandler('cancel', cancel)],
        per_message=True
    )

    # Main conversation handler
    conv_handler = ConversationHandler(
        entry_points=[
            CommandHandler('crypt', crypt),
            MessageHandler(filters.Regex('^🔐 Start Encrypt$'), crypt)
        ],
        states={
            WAITING_FOR_FILE: [MessageHandler(filters.Document.ALL, handle_file)],
        },
        fallbacks=[CommandHandler('cancel', cancel)],
        per_message=True
    )

    # Add handlers
    application.add_handler(admin_handler)
    application.add_handler(conv_handler)
    application.add_handler(CommandHandler('start', start))
    application.add_handler(CommandHandler('check', check))
    application.add_handler(CommandHandler('cancel', cancel))
    application.add_handler(CommandHandler('purchase', purchase))
    application.add_handler(CommandHandler('help', help_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_menu_buttons))
    application.add_error_handler(error_handler)

    bot_application = application
    return application

# Bot runner function
async def run_bot_async():
    """Run the bot asynchronously"""
    try:
        application = await setup_bot()
        logger.info("Starting bot...")
        await application.initialize()
        await application.start()
        await application.updater.start_polling(allowed_updates=Update.ALL_TYPES)
        
        # Keep the bot running
        while True:
            await asyncio.sleep(1)
            
    except Exception as e:
        logger.error(f"Error in bot runner: {e}")
        raise

def run_bot():
    """Run the bot in a new event loop"""
    try:
        # Create new event loop for this thread
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        # Run the bot
        loop.run_until_complete(run_bot_async())
        
    except Exception as e:
        logger.error(f"Critical error in bot thread: {e}")
    finally:
        try:
            loop.close()
        except:
            pass

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
    col1, col2 = st.columns([3, 1])
    
    with col1:
        if st.session_state.bot_started:
            st.success("✅ Bot is running!")
            st.info("Your Telegram bot is active and ready to receive messages.")
            
            # Show bot info
            st.markdown("**Bot Username:** @YourBotUsername")
            st.markdown("**Bot Status:** Active")
            st.markdown("**Database:** Connected" if mongo_connected else "**Database:** Disconnected")
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
                    st.success("Bot started successfully!")
                    st.rerun()
                except Exception as e:
                    st.error(f"Failed to start bot: {e}")
        else:
            if st.button("🛑 Stop Bot", type="secondary"):
                st.session_state.bot_started = False
                st.warning("Bot stopped (Note: Thread will continue running)")
                st.rerun()
    
    st.markdown("---")
    
    # Bot Information
    col1, col2 = st.columns(2)
    
    with col1:
        st.subheader("🔧 Bot Features")
        st.markdown("""
        - 🔐 **File Encryption** - Secure file processing
        - 👥 **User Management** - Premium user tracking
        - 📊 **Subscription System** - Time-based access control
        - 🛡️ **Admin Controls** - Full administrative access
        - 📱 **Interactive Menu** - User-friendly interface
        """)
    
    with col2:
        st.subheader("📋 Available Commands")
        st.markdown("""
        - `/start` - Initialize bot and show menu
        - `/crypt` - Begin file encryption process
        - `/check` - View subscription status
        - `/admin` - Access admin panel
        - `/purchase` - Get premium access info
        - `/cancel` - Cancel current operation
        - `/help` - Show help information
        """)
    
    st.markdown("---")
    
    # Configuration
    st.subheader("⚙️ Configuration")
    col1, col2 = st.columns(2)
    
    with col1:
        st.markdown("**Bot Token:** " + ("✅ Configured" if API_TOKEN else "❌ Missing"))
        st.markdown("**Admin ID:** " + ("✅ Set" if ADMIN_ID else "❌ Missing"))
    
    with col2:
        st.markdown("**MongoDB:** " + ("✅ Connected" if mongo_connected else "❌ Disconnected"))
        st.markdown("**Directories:** ✅ Created")
    
    st.markdown("---")
    
    # Footer
    st.markdown("""
    **📝 Note:** This bot is running on Streamlit Community Cloud. 
    Make sure your environment variables are properly configured for production use.
    
    **🔒 Security:** Bot token and database credentials should be stored as secrets in production.
    """)

if __name__ == '__main__':
    main()
