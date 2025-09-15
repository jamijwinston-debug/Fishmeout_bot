#!/bin/bash

# Setup script for Telegram Moderation Bot

echo "Setting up Telegram Moderation Bot..."

# Check if Python is installed
if ! command -v python3 &> /dev/null; then
    echo "Python 3 is not installed. Please install it first."
    exit 1
fi

# Check if pip is installed
if ! command -v pip3 &> /dev/null; then
    echo "pip3 is not installed. Please install it first."
    exit 1
fi

# Create requirements.txt
echo "Creating requirements.txt..."
cat > requirements.txt << EOL
python-telegram-bot==13.7
google-api-python-client==2.48.0
google-auth-httplib2==0.1.0
google-auth-oauthlib==0.4.6
gspread==5.4.0
EOL

# Install required packages
echo "Installing required packages..."
pip3 install -r requirements.txt

# Check if credentials file exists
if [ ! -f "fishmeout-credentials.json" ]; then
    echo "Error: fishmeout-credentials.json not found!"
    echo "Please make sure your credentials file is in the same directory."
    exit 1
fi

# Ask for bot token if not set
if [ -z "$BOT_TOKEN" ]; then
    echo "Please enter your Telegram Bot Token (from @BotFather):"
    read BOT_TOKEN
    export BOT_TOKEN=$BOT_TOKEN
    echo "export BOT_TOKEN=$BOT_TOKEN" >> ~/.bashrc
    echo "Bot token saved to environment variables"
fi

# Share the Google Doc and Sheet with the service account
SERVICE_ACCOUNT_EMAIL="telegram-bot-service@fishmeout.iam.gserviceaccount.com"
echo "Please share your Google Doc and Sheet with the service account: $SERVICE_ACCOUNT_EMAIL"
echo "Google Doc ID: 1uZ0g63V3Zxq8sIXrR3ggGQyArkNiOYseGsaX0hyCr6Y"
echo "Google Sheet ID: 1sq4zmYnvyWUymfWvv4sRDFdnj31mQKgkGEpQurHHgYk"
echo ""
echo "Press Enter to continue after sharing the documents..."
read

# Run the bot
echo "Starting the bot..."
python3 bot.py
