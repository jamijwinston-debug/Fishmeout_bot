import os
import re
import logging
import datetime
import json
from typing import Dict, List, Set
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes
from telegram.ext import filters
from telegram.helpers import mention_html

# Google APIs imports
import gspread
from google.oauth2 import service_account
from googleapiclient.discovery import build

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

async def post_init(application):
    """Initialize bot data after startup"""
    bot = application.bot
    bot_data = application.bot_data
    
    # Initialize negative words list
    bot_data['negative_words'] = {
        'hate', 'stupid', 'idiot', 'moron', 'retard', 'shit', 'fuck', 'asshole',
        'bastard', 'bitch', 'cunt', 'damn', 'hell', 'dumb', 'loser', 'shit', 
        'fucking', 'ass', 'dick', 'piss', 'cock', 'pussy', 'fag', 'faggot',
        'whore', 'slut', 'nigger', 'nigga', 'chink', 'spic', 'kike', 'terrorist'
    }
    
    # Google Docs and Sheets IDs
    bot_data['knowledge_doc_id'] = "1uZ0g63V3Zxq8sIXrR3ggGQyArkNiOYseGsaX0hyCr6Y"
    bot_data['learning_sheet_id'] = "1sq4zmYnvyWUymfWvv4sRDFdnj31mQKgkGEpQurHHgYk"
    
    # Initialize Google services
    google_creds_json = os.environ.get('GOOGLE_CREDS_JSON')
    if not google_creds_json:
        logger.error("GOOGLE_CREDS_JSON environment variable is not set")
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
        logger.info("Google services initialized successfully")
    except Exception as e:
        logger.error(f"Error initializing Google services: {e}")
    
    # Store group-specific welcome messages
    bot_data['welcome_messages'] = {}

def check_negative_content(text: str, negative_words: Set[str]) -> bool:
    """Check if message contains negative/inappropriate words"""
    text_lower = text.lower()
    return any(re.search(r'\b' + re.escape(word) + r'\b', text_lower) for word in negative_words)

async def get_knowledge_response(query: str, docs_service, knowledge_doc_id: str) -> str:
    """Search knowledge base in Google Docs for a response"""
    try:
        # Retrieve the document content
        doc = docs_service.documents().get(documentId=knowledge_doc_id).execute()
        content = doc.get('body', {}).get('content', [])
        
        # Extract text from the document
        full_text = ""
        for element in content:
            if 'paragraph' in element:
                for para_element in element['paragraph']['elements']:
                    if 'textRun' in para_element:
                        full_text += para_element['textRun']['content']
        
        # Simple keyword matching (implement more sophisticated parsing as needed)
        query_lower = query.lower()
        lines = full_text.split('\n')
        
        for line in lines:
            line_lower = line.lower()
            if any(word in line_lower for word in query_lower.split()):
                return line.strip()
        
        return "I don't have information about that yet. I'll save it to learn more."
    
    except Exception as e:
        logger.error(f"Error accessing knowledge base: {e}")
        return "Sorry, I'm having trouble accessing my knowledge base right now."

async def save_to_learning_sheet(phrase: str, gc, learning_sheet_id: str, context: str = ""):
    """Save unfamiliar phrases to Google Sheets for later learning"""
    try:
        sheet = gc.open_by_key(learning_sheet_id).sheet1
        
        # Get current data to avoid duplicates
        existing_data = sheet.get_all_values()
        existing_phrases = [row[0].lower() for row in existing_data if row]
        
        if phrase.lower() not in existing_phrases:
            sheet.append_row([phrase, context, str(datetime.datetime.now())])
            logger.info(f"Saved new phrase to learning sheet: {phrase}")
        else:
            logger.info(f"Phrase already exists in learning sheet: {phrase}")
        
    except Exception as e:
        logger.error(f"Error saving to learning sheet: {e}")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle incoming messages"""
    if not update.message or not update.message.text:
        return
    
    # Ignore messages from the bot itself
    if update.message.from_user.id == context.bot.id:
        return
    
    # Check for negative content
    if check_negative_content(update.message.text, context.bot_data['negative_words']):
        # Flag the message
        warning = f"⚠️ Warning: {mention_html(update.message.from_user.id, update.message.from_user.first_name)} used inappropriate language."
        await update.message.reply_html(warning)
        
        # Delete the inappropriate message
        await update.message.delete()
        
        # Log this action
        logger.info(f"Flagged and deleted message from {update.message.from_user.id}: {update.message.text}")
    
    # Otherwise, try to respond using knowledge base if the bot is mentioned
    elif context.bot.username.lower() in update.message.text.lower() or update.message.chat.type == 'private':
        # Extract the query (remove bot mention if present)
        query = re.sub(r'@' + context.bot.username + r'\s*', '', update.message.text, flags=re.IGNORECASE).strip()
        
        if query:
            response = await get_knowledge_response(query, context.bot_data['docs_service'], context.bot_data['knowledge_doc_id'])
            await update.message.reply_text(response)
            
            # If no response found, save for learning
            if "I don't have information" in response:
                await save_to_learning_sheet(
                    query, 
                    context.bot_data['gc'], 
                    context.bot_data['learning_sheet_id'],
                    f"User: {update.message.from_user.id}, Chat: {update.message.chat.id}"
                )

async def welcome_new_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send welcome message when bot is added to a group"""
    for member in update.message.new_chat_members:
        if member.id == context.bot.id:
            # Bot was added to the group
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
    """Handle /start command"""
    await update.message.reply_text(
        "Hi! I'm a moderation and assistance bot. "
        "I can detect inappropriate language and answer questions based on my knowledge base. "
        "Use /help to see all available commands."
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /help command"""
    await update.message.reply_text(
        "🤖 Available commands:\n\n"
        "/start - Start interacting with the bot\n"
        "/help - Show this help message\n"
        "I automatically monitor messages for inappropriate content and can answer questions "
        "when you mention me in a group chat or message me directly."
    )

def main():
    """Start the bot"""
    # Get bot token from environment variable
    BOT_TOKEN = os.environ.get('BOT_TOKEN')
    
    if not BOT_TOKEN:
        logger.error("BOT_TOKEN environment variable is required")
        logger.error("Please set it in your Render environment variables")
        exit(1)
    
    # Create Application
    application = Application.builder().token(BOT_TOKEN).post_init(post_init).build()
    
    # Register handlers
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, welcome_new_member))
    
    # Start the bot
    logger.info("Bot is starting...")
    application.run_polling()

if __name__ == '__main__':
    main()
