import os
import re
import logging
import datetime
import json
import asyncio
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes
from telegram.ext import filters
from telegram.helpers import mention_html

# Google APIs imports
import gspread
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

class ContentModerationBot:
    def __init__(self, token: str):
        self.token = token
        self.negative_words = self.load_negative_words()
        self.welcome_messages = {}
        
        # Google Docs and Sheets IDs
        self.knowledge_doc_id = "1uZ0g63V3Zxq8sIXrR3ggGQyArkNiOYseGsaX0hyCr6Y"
        self.learning_sheet_id = "1sq4zmYnvyWUymfWvv4sRDFdnj31mQKgkGEpQurHHgYk"
        
        # Initialize Google services
        self.google_creds = None
        self.docs_service = None
        self.sheets_service = None
        self.gc = None
        self.initialize_google_services()
    
    def initialize_google_services(self):
        """Initialize Google services from environment variable"""
        try:
            google_creds_json = os.environ.get('GOOGLE_CREDS_JSON')
            
            if not google_creds_json:
                logger.warning("GOOGLE_CREDS_JSON environment variable is not set - Google services disabled")
                return
            
            # Try to parse the JSON credentials
            try:
                creds_dict = json.loads(google_creds_json)
            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse GOOGLE_CREDS_JSON: {e}")
                # Check if it might be a file path instead of JSON string
                if os.path.exists(google_creds_json):
                    logger.info(f"GOOGLE_CREDS_JSON appears to be a file path, loading from file")
                    with open(google_creds_json, 'r') as f:
                        creds_dict = json.load(f)
                else:
                    logger.error("GOOGLE_CREDS_JSON is not valid JSON and not a file path")
                    return
            
            self.google_creds = service_account.Credentials.from_service_account_info(
                creds_dict,
                scopes=[
                    'https://www.googleapis.com/auth/documents',
                    'https://www.googleapis.com/auth/spreadsheets'
                ]
            )
            
            self.docs_service = build('docs', 'v1', credentials=self.google_creds)
            self.sheets_service = build('sheets', 'v4', credentials=self.google_creds)
            self.gc = gspread.authorize(self.google_creds)
            
            logger.info("Google services successfully initialized")
            
        except Exception as e:
            logger.error(f"Error initializing Google services: {e}")
            logger.warning("Continuing without Google services")
        
    def load_negative_words(self) -> set:
        """Load negative/inappropriate words"""
        return {
            'hate', 'stupid', 'idiot', 'moron', 'retard', 'shit', 'fuck', 'asshole',
            'bastard', 'bitch', 'cunt', 'damn', 'hell', 'dumb', 'loser', 
            'fucking', 'ass', 'dick', 'piss', 'cock', 'pussy', 'fag', 'faggot',
            'whore', 'slut', 'nigger', 'nigga', 'chink', 'spic', 'kike', 'terrorist'
        }
    
    def check_negative_content(self, text: str) -> bool:
        """Check if message contains negative/inappropriate words"""
        text_lower = text.lower()
        return any(re.search(r'\b' + re.escape(word) + r'\b', text_lower) for word in self.negative_words)
    
    async def get_knowledge_response(self, query: str) -> str:
        """Search knowledge base in Google Docs for a response"""
        try:
            if not self.docs_service:
                return "Knowledge base temporarily unavailable."
                
            # Retrieve the document content
            doc = self.docs_service.documents().get(documentId=self.knowledge_doc_id).execute()
            content = doc.get('body', {}).get('content', [])
            
            # Extract text from the document
            full_text = ""
            for element in content:
                if 'paragraph' in element:
                    for para_element in element['paragraph']['elements']:
                        if 'textRun' in para_element:
                            full_text += para_element['textRun']['content']
            
            # Simple keyword matching
            query_lower = query.lower()
            lines = full_text.split('\n')
            
            for line in lines:
                line_lower = line.lower()
                if any(word in line_lower for word in query_lower.split()):
                    return line.strip()
            
            return "I don't have information about that yet. I'll save it to learn more."
        
        except HttpError as e:
            logger.error(f"Google Docs API error: {e}")
            return "Sorry, I'm having trouble accessing my knowledge base right now."
        except Exception as e:
            logger.error(f"Error accessing knowledge base: {e}")
            return "Sorry, I'm having trouble accessing my knowledge base right now."
    
    async def save_to_learning_sheet(self, phrase: str, context: str = ""):
        """Save unfamiliar phrases to Google Sheets for later learning"""
        try:
            if not self.gc:
                logger.warning("Google Sheets not available - skipping save")
                return
                
            sheet = self.gc.open_by_key(self.learning_sheet_id).sheet1
            
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
    
    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle incoming messages"""
        try:
            message = update.message
            if not message or not message.text:
                return
            
            # Ignore messages from the bot itself
            if message.from_user.id == context.bot.id:
                return
            
            # Check for negative content
            if self.check_negative_content(message.text):
                # Flag the message
                warning = f"⚠️ Warning: {mention_html(message.from_user.id, message.from_user.first_name)} used inappropriate language."
                await message.reply_html(warning)
                
                # Delete the inappropriate message
                await message.delete()
                
                # Log this action
                logger.info(f"Flagged and deleted message from {message.from_user.id}: {message.text}")
            
            # Otherwise, try to respond using knowledge base if the bot is mentioned
            elif context.bot.username and (context.bot.username.lower() in message.text.lower() or message.chat.type == 'private'):
                # Extract the query (remove bot mention if present)
                query = re.sub(r'@' + context.bot.username + r'\s*', '', message.text, flags=re.IGNORECASE).strip()
                
                if query:
                    response = await self.get_knowledge_response(query)
                    await message.reply_text(response)
                    
                    # If no response found, save for learning
                    if "I don't have information" in response:
                        await self.save_to_learning_sheet(query, f"User: {message.from_user.id}, Chat: {message.chat.id}")
                        
        except Exception as e:
            logger.error(f"Error handling message: {e}")
    
    async def welcome_new_member(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Send welcome message when bot is added to a group"""
        try:
            for member in update.message.new_chat_members:
                if member.id == context.bot.id:
                    # Bot was added to the group
                    chat_id = update.message.chat_id
                    welcome_message = self.welcome_messages.get(chat_id, 
                        "Hello! I'm here to help maintain a positive environment. "
                        "I can detect inappropriate language and answer questions based on my knowledge base. "
                        "Use /help to see what I can do.")
                    
                    await update.message.reply_text(welcome_message)
                    break
        except Exception as e:
            logger.error(f"Error welcoming new member: {e}")
    
    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /start command"""
        try:
            logger.info(f"/start command received from user: {update.message.from_user.id}")
            await update.message.reply_text(
                "Hi! I'm a moderation and assistance bot. "
                "I can detect inappropriate language and answer questions based on my knowledge base. "
                "Use /help to see all available commands."
            )
        except Exception as e:
            logger.error(f"Error in start command: {e}")
    
    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /help command"""
        try:
            await update.message.reply_text(
                "🤖 Available commands:\n\n"
                "/start - Start interacting with the bot\n"
                "/help - Show this help message\n\n"
                "I automatically monitor messages for inappropriate content and can answer questions "
                "when you mention me in a group chat or message me directly."
            )
        except Exception as e:
            logger.error(f"Error in help command: {e}")
    
    async def run(self):
        """Start the bot with polling"""
        try:
            # Create application
            application = Application.builder().token(self.token).build()
            
            # Register handlers
            application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_message))
            application.add_handler(CommandHandler("start", self.start_command))
            application.add_handler(CommandHandler("help", self.help_command))
            application.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, self.welcome_new_member))
            
            logger.info("Bot application setup completed successfully")
            logger.info("Starting bot polling...")
            
            # Start polling with proper error handling
            await application.initialize()
            await application.start()
            await application.updater.start_polling()
            
            # Keep the bot running
            await asyncio.Event().wait()
            
        except Exception as e:
            logger.error(f"Error running bot: {e}")
            # Try to restart after a delay
            await asyncio.sleep(10)
            logger.info("Restarting bot...")
            await self.run()

# Main execution
async def main():
    # Get bot token from environment variable
    BOT_TOKEN = os.environ.get('BOT_TOKEN')
    
    if not BOT_TOKEN:
        logger.error("BOT_TOKEN environment variable is required")
        return
    
    # Create and run bot
    bot = ContentModerationBot(BOT_TOKEN)
    await bot.run()

if __name__ == '__main__':
    # Run the async main function
    asyncio.run(main())
