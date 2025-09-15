import os
import re
import logging
import datetime
import json
import difflib
from typing import Set
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters
from telegram.helpers import mention_html

# Google APIs
import gspread
from google.oauth2 import service_account
from googleapiclient.discovery import build

# Configure logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ---------------------------
# Helper functions
# ---------------------------

def check_negative_content(text: str, negative_words: Set[str]) -> bool:
    """Check if a message contains negative/inappropriate words"""
    text_lower = text.lower()
    return any(re.search(r'\b' + re.escape(word) + r'\b', text_lower) for word in negative_words)

async def get_knowledge_response(query: str, docs_service, knowledge_doc_id: str) -> str:
    """Search Google Docs for a response with fuzzy matching"""
    try:
        doc = docs_service.documents().get(documentId=knowledge_doc_id).execute()
        content = doc.get('body', {}).get('content', [])
        full_text = ""
        for element in content:
            if 'paragraph' in element:
                for para in element['paragraph']['elements']:
                    if 'textRun' in para:
                        full_text += para['textRun']['content'] + "\n"

        # Exact match first
        query_lower = query.lower()
        for line in full_text.split("\n"):
            if any(word in line.lower() for word in query_lower.split()):
                return line.strip()

        # Fuzzy match
        lines_lower = [line.lower() for line in full_text.split("\n")]
        matches = difflib.get_close_matches(query_lower, lines_lower, n=1, cutoff=0.6)
        if matches:
            match_index = lines_lower.index(matches[0])
            return full_text.split("\n")[match_index].strip()

        return "I don't have information about that yet. I'll save it to learn more."
    except Exception as e:
        logger.error(f"Error accessing knowledge base: {e}")
        return "Sorry, I'm having trouble accessing my knowledge base right now."

async def save_to_learning_sheet(phrase: str, gc, learning_sheet_id: str, context: str = ""):
    """Save unknown phrases to Google Sheets for later learning"""
    try:
        sheet = gc.open_by_key(learning_sheet_id).sheet1
        existing_data = sheet.get_all_values()
        existing_phrases = [row[0].lower() for row in existing_data if row]
        if phrase.lower() not in existing_phrases:
            sheet.append_row([phrase, context, str(datetime.datetime.now())])
            logger.info(f"Saved new phrase: {phrase}")
        else:
            logger.info(f"Phrase already exists: {phrase}")
    except Exception as e:
        logger.error(f"Error saving to learning sheet: {e}")

# ---------------------------
# Bot Handlers
# ---------------------------

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle messages with moderation and knowledge response"""
    if not update.message or not update.message.text:
        return

    if update.message.from_user.id == context.bot.id:
        return

    bot_data = context.bot_data
    negative_words = bot_data.get('negative_words', set())

    # Check negative content
    if check_negative_content(update.message.text, negative_words):
        try:
            warning = f"⚠️ Warning: {mention_html(update.message.from_user.id, update.message.from_user.first_name)} used inappropriate language."
            await update.message.reply_html(warning)
            await update.message.delete()
            logger.info(f"Flagged and deleted message from {update.message.from_user.id}: {update.message.text}")
        except Exception as e:
            logger.warning(f"Could not delete/reply message: {e}")
        return

    # Private or mention
    bot_username = getattr(context.bot, "username", "")
    is_private = update.message.chat.type == 'private'
    mentioned = bot_username and bot_username.lower() in update.message.text.lower()

    if is_private or mentioned:
        query = re.sub(r'@' + re.escape(bot_username) + r'\s*', '', update.message.text, flags=re.IGNORECASE).strip()
        if not query:
            return

        response = await get_knowledge_response(query, bot_data.get('docs_service'), bot_data.get('knowledge_doc_id'))
        await update.message.reply_text(response)

        if "I don't have information" in response:
            await save_to_learning_sheet(
                query,
                bot_data.get('gc'),
                bot_data.get('learning_sheet_id'),
                f"User: {update.message.from_user.id}, Chat: {update.message.chat.id}"
            )

async def welcome_new_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send welcome message when bot is added to a group"""
    for member in update.message.new_chat_members:
        if member.id == context.bot.id:
            chat_id = update.message.chat_id
            welcome_message = context.bot_data['welcome_messages'].get(
                chat_id,
                "Hello! I'm here to help maintain a positive environment. "
                "I can detect inappropriate language and answer questions based on my knowledge base. "
                "Use /help to see what I can do."
            )
            await update.message.reply_text(welcome_message)
            break

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Hi! I'm a moderation and assistance bot. "
        "I can detect inappropriate language and answer questions based on my knowledge base. "
        "Use /help to see all commands."
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 Available commands:\n"
        "/start - Start interacting with the bot\n"
        "/help - Show this help message\n"
        "I automatically monitor messages for inappropriate content and can answer questions "
        "when you mention me or message me directly."
    )

# ---------------------------
# Post-initialization
# ---------------------------

async def post_init(application):
    """Initialize bot data and Google services"""
    bot_data = application.bot_data

    # Negative words
    bot_data['negative_words'] = {
        'hate','stupid','idiot','moron','retard','shit','fuck','asshole',
        'bastard','bitch','cunt','damn','hell','dumb','loser','fucking',
        'ass','dick','piss','cock','pussy','fag','faggot','whore','slut',
        'nigger','nigga','chink','spic','kike','terrorist'
    }

    # Google Docs & Sheets IDs
    bot_data['knowledge_doc_id'] = os.environ.get('KNOWLEDGE_DOC_ID', '')
    bot_data['learning_sheet_id'] = os.environ.get('LEARNING_SHEET_ID', '')

    # Google credentials
    google_creds_json = os.environ.get('GOOGLE_CREDS_JSON')
    if not google_creds_json:
        logger.error("GOOGLE_CREDS_JSON not set")
        return

    try:
        creds_dict = json.loads(google_creds_json)
        credentials = service_account.Credentials.from_service_account_info(
            creds_dict,
            scopes=[
                'https://www.googleapis.com/auth/documents',
                'https://www.googleapis.com/auth/spreadsheets'
            ]
        )
        bot_data['google_creds'] = credentials
        bot_data['docs_service'] = build('docs', 'v1', credentials=credentials)
        bot_data['sheets_service'] = build('sheets', 'v4', credentials=credentials)
        bot_data['gc'] = gspread.authorize(credentials)
        bot_data['welcome_messages'] = {}
        logger.info("Google services initialized successfully")
    except Exception as e:
        logger.error(f"Error initializing Google services: {e}")

# ---------------------------
# Main function
# ---------------------------

def main():
    BOT_TOKEN = os.environ.get('BOT_TOKEN')
    if not BOT_TOKEN:
        logger.error("BOT_TOKEN environment variable is required")
        exit(1)

    application = Application.builder().token(BOT_TOKEN).post_init(post_init).build()

    # Handlers
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, welcome_new_member))

    logger.info("Bot is starting...")
    application.run_polling()

if __name__ == "__main__":
    main()
