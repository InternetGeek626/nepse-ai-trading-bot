import os
import requests
from bs4 import BeautifulSoup
from telegram.ext import Application, CommandHandler, ContextTypes
from telegram import Update
from nepse import NEPSE
import logging

# Set up logging
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize NEPSE API
nepse = NEPSE()

# Telegram bot token from environment variable
TOKEN = os.getenv("TELEGRAM_TOKEN")
if not TOKEN:
    raise ValueError("TELEGRAM_TOKEN environment variable not set")

# Function to fetch offerings (IPOs, debentures, FPOs, etc.)
async def offerings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        # Scrape from Moneycontrol for IPOs, debentures, FPOs, etc.
        url = "https://www.moneycontrol.com/ipo/ipo-issues-open"
        headers = {"User-Agent": "Mozilla/5.0"}
        response = requests.get(url, headers=headers)
        soup = BeautifulSoup(response.content, "html.parser")

        # Find offerings (adjust selector based on site structure)
        offering_items = soup.select("table.pcq_tbl tr")[1:4]  # Get top 3 rows from IPO table
        if not offering_items:
            await update.message.reply_text("No recent offerings found.")
            return

        message = "📊 Recent Offerings (IPOs, FPOs, Debentures, etc.):\n\n"
        for item in offering_items:
            cols = item.select("td")
            if len(cols) < 2:
                continue
            name = cols[0].text.strip()  # Company name
            issue_type = cols[1].text.strip() if len(cols) > 1 else "N/A"  # Issue type (IPO, FPO, etc.)
            details = f"• {name} ({issue_type})\n"
            # Check if there's a link to more details
            link = cols[0].find("a")
            if link and "href" in link.attrs:
                details += f"https://www.moneycontrol.com{link['href']}\n"
            message += details + "\n"

        # Additional scrape for debentures or bonds from Economic Times
        url_debentures = "https://economictimes.indiatimes.com/markets/bonds"
        response_debentures = requests.get(url_debentures, headers=headers)
        soup_debentures = BeautifulSoup(response_debentures.content, "html.parser")
        debenture_items = soup_debentures.select("div.story_list h3 a")[:2]  # Get top 2 bond/debenture news
        if debenture_items:
            message += "📜 Recent Debenture/Bond News:\n\n"
            for item in debenture_items:
                title = item.text.strip()
                link = "https://economictimes.indiatimes.com" + item["href"]
                message += f"• {title}\n{link}\n\n"

        await update.message.reply_text(message)
    except Exception as e:
        logger.error(f"Error fetching offerings: {e}")
        await update.message.reply_text("Sorry, I couldn't fetch offerings information right now.")

# Function to fetch news impacting NEPSE (unchanged)
async def news(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        url = "https://www.business-standard.com/category/markets-news"
        headers = {"User-Agent": "Mozilla/5.0"}
        response = requests.get(url, headers=headers)
        soup = BeautifulSoup(response.content, "html.parser")

        news_items = soup.select("div.cardlisting h2 a")[:3]
        if not news_items:
            await update.message.reply_text("No recent news found.")
            return

        message = "📰 News Impacting NEPSE:\n\n"
        for item in news_items:
            title = item.text.strip()
            link = "https://www.business-standard.com" + item["href"]
            if any(keyword in title.lower() for keyword in ["nepal", "nepse", "south asia", "india", "policy", "economy"]):
                message += f"• {title}\n{link}\n\n"

        if message == "📰 News Impacting NEPSE:\n\n":
            message = "No recent news directly impacting NEPSE found, but here are some market updates:\n\n"
            for item in news_items[:2]:
                title = item.text.strip()
                link = "https://www.business-standard.com" + item["href"]
                message += f"• {title}\n{link}\n\n"

        await update.message.reply_text(message)
    except Exception as e:
        logger.error(f"Error fetching news: {e}")
        await update.message.reply_text("Sorry, I couldn't fetch news right now.")

# Existing commands (unchanged)
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Welcome to NEPSE Alert Bot! Use /opportunities, /monitor, /offerings, /news, or /stop.")

async def opportunities(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        stocks = nepse.get_top_gainers()[:3]
        message = "📈 Top Gainers:\n\n"
        for stock in stocks:
            message += f"• {stock['symbol']}: {stock['percent_change']}%\n"
        await update.message.reply_text(message)
    except Exception as e:
        logger.error(f"Error fetching opportunities: {e}")
        await update.message.reply_text("Sorry, I couldn't fetch opportunities right now.")

async def monitor(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Monitoring NEPSE stocks. I'll notify you of significant changes.")

async def stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Stopping the bot. Goodbye!")

# Main function to set up the bot
def main():
    application = Application.builder().token(TOKEN).build()

    # Add command handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("opportunities", opportunities))
    application.add_handler(CommandHandler("monitor", monitor))
    application.add_handler(CommandHandler("offerings", offerings))
    application.add_handler(CommandHandler("news", news))
    application.add_handler(CommandHandler("stop", stop))

    logger.info("Starting NEPSE Alert Bot...")
    logger.info(f"Telegram Bot initialized with token: {TOKEN[:10]}...")
    application.run_polling()

if __name__ == "__main__":
    main()