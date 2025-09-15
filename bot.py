import os
import re
import logging
import datetime
from typing import Dict, List, Set
from telegram import Update, Chat, ChatMember
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters, CallbackContext
from telegram.utils.helpers import mention_html

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

class ContentModerationBot:
    def __init__(self, token: str, google_credentials_path: str):
        self.updater = Updater(token, use_context=True)
        self.dispatcher = self.updater.dispatcher
        
        # Initialize negative words list
        self.negative_words = self.load_negative_words()
        
        # Google Docs and Sheets IDs (you provided)
        self.knowledge_doc_id = "1uZ0g63V3Zxq8sIXrR3ggGQyArkNiOYseGsaX0hyCr6Y"
        self.learning_sheet_id = "1sq4zmYnvyWUymfWvv4sRDFdnj31mQKgkGEpQurHHgYk"
        
        # Initialize Google services
        self.google_creds = service_account.Credentials.from_service_account_file(
            google_credentials_path,
            scopes=[
                'https://www.googleapis.com/auth/documents',
                'https://www.googleapis.com/auth/spreadsheets'
            ]
        )
        self.docs_service = build('docs', 'v1', credentials=self.google_creds)
        self.sheets_service = build('sheets', 'v4', credentials=self.google_creds)
        
        # Initialize gspread for easier Sheets access
        self.gc = gspread.authorize(self.google_creds)
        
        # Register handlers
        self.dispatcher.add_handler(MessageHandler(Filters.text & ~Filters.command, self.handle_message))
        self.dispatcher.add_handler(CommandHandler("start", self.start_command))
        self.dispatcher.add_handler(CommandHandler("help", self.help_command))
        self.dispatcher.add_handler(CommandHandler("add_knowledge", self.add_knowledge_command))
        self.dispatcher.add_handler(CommandHandler("set_welcome", self.set_welcome_command))
        
        # Handle group events
        self.dispatcher.add_handler(MessageHandler(Filters.status_update.new_chat_members, self.welcome_new_member))
        
        # Store group-specific welcome messages
        self.welcome_messages = {}
        
    def load_negative_words(self) -> Set[str]:
        """Load negative/inappropriate words from a file or database"""
        # Default list of negative words - you can expand this
        return {
            'hate', 'stupid', 'idiot', 'moron', 'retard', 'shit', 'fuck', 'asshole',
            'bastard', 'bitch', 'cunt', 'damn', 'hell', 'dumb', 'loser', 'shit', 
            'fucking', 'ass', 'dick', 'piss', 'cock', 'pussy', 'fag', 'faggot',
            'whore', 'slut', 'nigger', 'nigga', 'chink', 'spic', 'kike', 'terrorist'
        }
    
    def check_negative_content(self, text: str) -> bool:
        """Check if message contains negative/inappropriate words"""
        text_lower = text.lower()
        return any(re.search(r'\b' + re.escape(word) + r'\b', text_lower) for word in self.negative_words)
    
    def get_knowledge_response(self, query: str) -> str:
        """Search knowledge base in Google Docs for a response"""
        try:
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
            
            # Split into questions and answers (assuming Q: and A: format)
            qa_pairs = []
            current_question = None
            current_answer = None
            
            for line in full_text.split('\n'):
                line = line.strip()
                if line.startswith('Q:'):
                    if current_question and current_answer:
                        qa_pairs.append((current_question, current_answer))
                    current_question = line[2:].strip()
                    current_answer = ""
                elif line.startswith('A:') and current_question:
                    current_answer = line[2:].strip()
                elif current_question and current_answer is not None:
                    current_answer += " " + line
            
            if current_question and current_answer:
                qa_pairs.append((current_question, current_answer))
            
            # Find the best matching question
            query_lower = query.lower()
            best_match = None
            best_score = 0
            
            for question, answer in qa_pairs:
                question_lower = question.lower()
                # Simple scoring based on word matches
                score = sum(1 for word in query_lower.split() if word in question_lower)
                if score > best_score:
                    best_score = score
                    best_match = answer
            
            if best_match and best_score >= 2:  # Require at least 2 matching words
                return best_match
            else:
                return "I don't have information about that yet. I'll save it to learn more."
        
        except Exception as e:
            logger.error(f"Error accessing knowledge base: {e}")
            return "Sorry, I'm having trouble accessing my knowledge base right now."
    
    def save_to_learning_sheet(self, phrase: str, context: str = ""):
        """Save unfamiliar phrases to Google Sheets for later learning"""
        try:
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
    
    def add_to_knowledge_doc(self, question: str, answer: str):
        """Add a new Q&A pair to the knowledge document"""
        try:
            # Format the new content
            new_content = f"\nQ: {question}\nA: {answer}\n"
            
            # Determine where to insert the new content (at the end of the document)
            doc = self.docs_service.documents().get(documentId=self.knowledge_doc_id).execute()
            end_index = doc['body']['content'][-1]['endIndex'] - 1
            
            # Create request to insert text
            requests = [
                {
                    'insertText': {
                        'location': {
                            'index': end_index
                        },
                        'text': new_content
                    }
                }
            ]
            
            # Execute the request
            self.docs_service.documents().batchUpdate(
                documentId=self.knowledge_doc_id,
                body={'requests': requests}
            ).execute()
            
            logger.info(f"Added new knowledge: Q: {question}, A: {answer}")
            return True
            
        except Exception as e:
            logger.error(f"Error adding to knowledge doc: {e}")
            return False
    
    def handle_message(self, update: Update, context: CallbackContext):
        """Handle incoming messages"""
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
            message.reply_html(warning)
            
            # Delete the inappropriate message
            message.delete()
            
            # Log this action
            logger.info(f"Flagged and deleted message from {message.from_user.id}: {message.text}")
        
        # Otherwise, try to respond using knowledge base if the bot is mentioned
        elif context.bot.name.lower() in message.text.lower() or message.chat.type == 'private':
            # Extract the query (remove bot mention if present)
            query = re.sub(r'@' + context.bot.name + r'\s*', '', message.text, flags=re.IGNORECASE).strip()
            
            if query:
                response = self.get_knowledge_response(query)
                message.reply_text(response)
                
                # If no response found, save for learning
                if "I don't have information" in response:
                    self.save_to_learning_sheet(query, f"User: {message.from_user.id}, Chat: {message.chat.id}")
    
    def welcome_new_member(self, update: Update, context: CallbackContext):
        """Send welcome message when bot is added to a group"""
        for member in update.message.new_chat_members:
            if member.id == context.bot.id:
                # Bot was added to the group
                chat_id = update.message.chat_id
                welcome_message = self.welcome_messages.get(chat_id, 
                    "Hello! I'm here to help maintain a positive environment. "
                    "I can detect inappropriate language and answer questions based on my knowledge base. "
                    "Use /help to see what I can do.")
                
                update.message.reply_text(welcome_message)
                break
    
    def start_command(self, update: Update, context: CallbackContext):
        """Handle /start command"""
        update.message.reply_text(
            "Hi! I'm a moderation and assistance bot. "
            "I can detect inappropriate language and answer questions based on my knowledge base. "
            "Use /help to see all available commands."
        )
    
    def help_command(self, update: Update, context: CallbackContext):
        """Handle /help command"""
        update.message.reply_text(
            "🤖 Available commands:\n\n"
            "/start - Start interacting with the bot\n"
            "/help - Show this help message\n"
            "/add_knowledge <question> | <answer> - Add new knowledge to my database (admin only)\n"
            "/set_welcome <message> - Set a custom welcome message for this group (admin only)\n\n"
            "I automatically monitor messages for inappropriate content and can answer questions "
            "when you mention me in a group chat or message me directly."
        )
    
    def add_knowledge_command(self, update: Update, context: CallbackContext):
        """Handle /add_knowledge command"""
        # Check if user is an admin (simplified check)
        if update.message.chat.type == 'private' or update.effective_user.id in [admin.user.id for admin in update.effective_chat.get_administrators()]:
            if not context.args:
                update.message.reply_text("Please provide knowledge in the format: /add_knowledge Question | Answer")
                return
            
            text = ' '.join(context.args)
            if '|' not in text:
                update.message.reply_text("Please separate question and answer with a | character: /add_knowledge Question | Answer")
                return
            
            question, answer = text.split('|', 1)
            question = question.strip()
            answer = answer.strip()
            
            if self.add_to_knowledge_doc(question, answer):
                update.message.reply_text("✅ Knowledge added successfully!")
            else:
                update.message.reply_text("❌ Failed to add knowledge. Please check logs for details.")
        else:
            update.message.reply_text("❌ You need to be an administrator to use this command.")
    
    def set_welcome_command(self, update: Update, context: CallbackContext):
        """Handle /set_welcome command"""
        # Check if user is an admin
        if update.message.chat.type == 'private' or update.effective_user.id in [admin.user.id for admin in update.effective_chat.get_administrators()]:
            if not context.args:
                update.message.reply_text("Please provide a welcome message: /set_welcome Your welcome message here")
                return
            
            welcome_message = ' '.join(context.args)
            self.welcome_messages[update.message.chat_id] = welcome_message
            update.message.reply_text("✅ Welcome message set successfully!")
        else:
            update.message.reply_text("❌ You need to be an administrator to use this command.")
    
    def run(self):
        """Start the bot"""
        self.updater.start_polling()
        logger.info("Bot is running...")
        self.updater.idle()

# Main execution
if __name__ == '__main__':
    # Configuration
    BOT_TOKEN = os.environ.get('BOT_TOKEN') or 'YOUR_BOT_TOKEN_HERE'
    GOOGLE_CREDS_PATH = 'fishmeout-credentials.json'
    
    if not BOT_TOKEN or BOT_TOKEN == 'YOUR_BOT_TOKEN_HERE':
        logger.error("BOT_TOKEN environment variable is required")
        logger.error("Please set it with: export BOT_TOKEN='your_bot_token_here'")
        exit(1)
    
    bot = ContentModerationBot(BOT_TOKEN, GOOGLE_CREDS_PATH)
    bot.run()
