import os
import asyncio
from datetime import datetime, time as dt_time
import logging
import requests
from bs4 import BeautifulSoup
from telegram.ext import Application, CommandHandler, ContextTypes
from telegram import Update
from nepse.core import Client
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

# Debug: Confirm script is starting
print("Starting NEPSE Alert Bot...")

# Set up logging
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')

# Initialize Telegram Bot
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
if not TELEGRAM_TOKEN:
    raise ValueError("Please set the TELEGRAM_TOKEN environment variable")
app = Application.builder().token(TELEGRAM_TOKEN).build()
logging.info("Telegram Bot initialized successfully.")
print("Telegram Bot initialized with token:", TELEGRAM_TOKEN[:10] + "...")

# NEPSE Trading Hours (Sunday to Thursday, 11 AM to 3 PM)
TRADING_DAYS = [0, 1, 2, 3, 4]  # Monday to Friday (0 = Monday, 4 = Friday)
TRADING_HOURS_START = dt_time(11, 0)  # 11:00 AM
TRADING_HOURS_END = dt_time(15, 0)    # 3:00 PM

# Global flag to control monitoring loop
monitoring_active = False

def is_trading_hours():
    current_time = datetime.now().time()
    current_day = datetime.now().weekday()
    return current_day in TRADING_DAYS and TRADING_HOURS_START <= current_time <= TRADING_HOURS_END

async def fetch_nepse_data(max_retries=3, delay=5):
    logging.info("Fetching NEPSE data...")
    for attempt in range(max_retries):
        nepse = Client()
        try:
            stock_data = await nepse.market_client.get_today_price()
            historical_data = await nepse.market_client.get_historical_data(days=30) if hasattr(nepse.market_client, 'get_historical_data') else []
            logging.info("NEPSE data fetched successfully.")
            logging.debug(f"Stock data: {stock_data[:2] if stock_data else None}")
            logging.debug(f"Historical data: {historical_data[:2] if historical_data else None}")
            return stock_data, historical_data
        except Exception as e:
            logging.error(f"Attempt {attempt + 1}/{max_retries} - Error fetching NEPSE data: {e}")
            if attempt < max_retries - 1:
                await asyncio.sleep(delay)
            else:
                logging.info("Using mock data as fallback after max retries...")
                mock_stock_data = [
                    {'symbol': 'MOCK1', 'closingPrice': 100.0},
                    {'symbol': 'MOCK2', 'closingPrice': 200.0}
                ]
                mock_historical_data = [
                    {'symbol': 'MOCK1', 'closingPrice': 95.0, 'totalTradeQuantity': 1000},
                    {'symbol': 'MOCK1', 'closingPrice': 98.0, 'totalTradeQuantity': 1200},
                    {'symbol': 'MOCK2', 'closingPrice': 190.0, 'totalTradeQuantity': 800},
                    {'symbol': 'MOCK2', 'closingPrice': 195.0, 'totalTradeQuantity': 900}
                ]
                return mock_stock_data, mock_historical_data
        finally:
            await nepse.close()

def calculate_rsi(data, periods=14):
    if len(data) < periods:
        return None
    deltas = [data[i] - data[i-1] for i in range(1, len(data))]
    gains = [d if d > 0 else 0 for d in deltas]
    losses = [-d if d < 0 else 0 for d in deltas]
    avg_gain = sum(gains[:periods]) / periods
    avg_loss = sum(losses[:periods]) / periods
    for i in range(periods, len(gains)):
        avg_gain = (avg_gain * (periods - 1) + gains[i]) / periods
        avg_loss = (avg_loss * (periods - 1) + losses[i]) / periods
    if avg_loss == 0:
        return 100
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))

async def fetch_news(stock_name):
    try:
        url = f"https://www.sharesansar.com/search?query={stock_name.replace(' ', '+')}"
        headers = {'User-Agent': 'Mozilla/5.0'}
        response = requests.get(url, headers=headers)
        soup = BeautifulSoup(response.text, 'html.parser')
        news_items = soup.find_all('div', class_='news-title', limit=3)
        news_texts = [item.text.strip() for item in news_items if item]
        if not news_texts:
            return ["No recent news found."], False
        analyzer = SentimentIntensityAnalyzer()
        sentiments = [analyzer.polarity_scores(text)['compound'] for text in news_texts]
        avg_sentiment = sum(sentiments) / len(sentiments) if sentiments else 0
        is_dangerous = avg_sentiment < -0.5
        return news_texts, is_dangerous
    except Exception as e:
        logging.error(f"Error fetching news for {stock_name}: {e}")
        return ["Error fetching news."], False

async def analyze_stock(stock_data, historical_data):
    if not stock_data or not isinstance(stock_data, list):
        return []
    analyzer = SentimentIntensityAnalyzer()
    analysis_results = []
    for stock in stock_data[:10]:
        try:
            stock_name = stock.get('symbol', 'Unknown')
            fundamental_score = 0.5  # Simplified since P/E ratio is removed
            
            historical = [h for h in historical_data if h['symbol'] == stock_name][-10:] if historical_data else []
            closing_prices_ma = [float(h['closingPrice']) for h in historical] if historical else []
            ma_10 = sum(closing_prices_ma) / len(closing_prices_ma) if closing_prices_ma else 0
            current_price = float(stock.get('closingPrice', 0))
            ma_trend = "Above MA" if current_price > ma_10 else "Below MA"
            
            historical_rsi = [h for h in historical_data if h['symbol'] == stock_name][-14:] if historical_data else []
            closing_prices_rsi = [float(h['closingPrice']) for h in historical_rsi] if historical_rsi else []
            rsi = calculate_rsi(closing_prices_rsi, periods=14) if closing_prices_rsi else None
            rsi = rsi if rsi is not None else 50
            
            technical_score = 0.7 if rsi < 30 or rsi > 70 else 0.5
            
            news_texts, is_dangerous = await fetch_news(stock_name)
            news_sentiments = [analyzer.polarity_scores(text)['compound'] for text in news_texts]
            avg_sentiment = sum(news_sentiments) / len(news_sentiments) if news_sentiments else 0
            
            recommendation = "Buy" if (technical_score > 0.6 and avg_sentiment > 0) else "No Buy"
            
            # Calculate volatility and volume spike for good opportunities
            historical_vol = [h for h in historical_data if h['symbol'] == stock_name][-30:] if historical_data else []
            closing_prices_vol = [float(h['closingPrice']) for h in historical_vol] if historical_vol else []
            returns = [(closing_prices_vol[i] - closing_prices_vol[i-1]) / closing_prices_vol[i-1] for i in range(1, len(closing_prices_vol))]
            mean_return = sum(returns) / len(returns) if returns else 0
            variance = sum((r - mean_return) ** 2 for r in returns) / len(returns) if returns else 0
            volatility = (variance ** 0.5) * 100 if variance else 0
            
            volumes = [float(h['totalTradeQuantity']) for h in historical_vol] if historical_vol else []
            avg_volume = sum(volumes[-10:]) / 10 if volumes else 0
            latest_volume = float(historical_vol[-1]['totalTradeQuantity']) if historical_vol else 0
            volume_spike = latest_volume > avg_volume * 1.5
            
            analysis_results.append({
                'name': stock_name,
                'fundamental_score': fundamental_score,
                'technical_score': technical_score,
                'rsi': rsi,
                'ma_trend': ma_trend,
                'sentiment': avg_sentiment,
                'recommendation': recommendation,
                'news': news_texts,
                'is_dangerous': is_dangerous,
                'current_price': current_price,
                'volatility': volatility,
                'volume_spike': volume_spike
            })
        except Exception as e:
            logging.error(f"Error analyzing stock {stock_name}: {e}")
    return analysis_results

async def identify_big_movers(stock_data, historical_data):
    if not stock_data or not historical_data:
        return []
    big_movers = []
    for stock in stock_data:
        try:
            stock_name = stock.get('symbol', 'Unknown')
            historical = [h for h in historical_data if h['symbol'] == stock_name][-30:] if historical_data else []
            if not historical:
                continue
                
            closing_prices = [float(h['closingPrice']) for h in historical]
            returns = [(closing_prices[i] - closing_prices[i-1]) / closing_prices[i-1] for i in range(1, len(closing_prices))]
            mean_return = sum(returns) / len(returns) if returns else 0
            variance = sum((r - mean_return) ** 2 for r in returns) / len(returns) if returns else 0
            volatility = (variance ** 0.5) * 100
            
            volumes = [float(h['totalTradeQuantity']) for h in historical]
            avg_volume = sum(volumes[-10:]) / 10 if volumes else 0
            latest_volume = float(historical[-1]['totalTradeQuantity']) if historical else 0
            volume_spike = latest_volume > avg_volume * 1.5
            
            prices_52w = [float(h['closingPrice']) for h in historical]
            high_52w = max(prices_52w) if prices_52w else 0
            current_price = float(stock.get('closingPrice', 0))
            near_high = current_price >= high_52w * 0.95
            
            if volatility > 5 and volume_spike and near_high:
                big_movers.append({
                    'name': stock_name,
                    'volatility': volatility,
                    'volume_spike': volume_spike,
                    'near_52w_high': near_high,
                    'current_price': current_price
                })
        except Exception as e:
            logging.error(f"Error identifying big movers for {stock_name}: {e}")
    return big_movers

async def identify_good_opportunities(analysis_results):
    good_opportunities = []
    for stock in analysis_results:
        if (stock['rsi'] < 30 and  # Oversold
            stock['ma_trend'] == "Above MA" and  # Upward trend
            stock['sentiment'] > 0.3 and  # Positive news
            stock['volatility'] > 5 and  # High volatility
            stock['volume_spike']):  # Volume spike
            good_opportunities.append({
                'name': stock['name'],
                'rsi': stock['rsi'],
                'ma_trend': stock['ma_trend'],
                'sentiment': stock['sentiment'],
                'volatility': stock['volatility'],
                'volume_spike': stock['volume_spike'],
                'current_price': stock['current_price'],
                'news': stock['news']  # Include news for the output
            })
    return good_opportunities

@bot.message_handler(commands=['start'])
async def start(message):
    user_id = message.chat.id
    logging.info(f"Received /start command from user {user_id}")
    await bot.reply_to(message, "Welcome to Nepse AI Trading Bot! Available commands:\n"
                              "/start - Show this message\n"
                              "/help - List all available commands\n"
                              "/monitor - Start monitoring stocks\n"
                              "/status - Check bot and market status\n"
                              "/opportunities - Get good stock opportunities\n"
                              "/stop - Stop monitoring")

@bot.message_handler(commands=['help'])
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Welcome to Nepse AI Trading Bot! Available commands:\n"
        "/start - Show this message\n"
        "/help - List all available commands\n"
        "/monitor - Start monitoring stocks\n"
        "/status - Check bot and market status\n"
        "/opportunities - Get good stock opportunities\n"
        "/stop - Stop monitoring"
    )import os
import asyncio
import re
from datetime import datetime, time as dt_time
import logging
import requests
from bs4 import BeautifulSoup
from nepse.core.client import Client
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
from telegram.ext import Application, CommandHandler, ContextTypes
from telegram import Update

print("Starting NEPSE Alert Bot...")

# Set up logging
logging.basicConfig(level=logging.DEBUG, format="%(asctime)s - %(levelname)s - %(message)s")

# Initialize Telegram Bot
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
if not TELEGRAM_TOKEN:
    raise ValueError("Please set the TELEGRAM_TOKEN environment variable.")
app = Application.builder().token(TELEGRAM_TOKEN).build()
logging.info(f"Telegram Bot initialized successfully. TELEGRAM_TOKEN: {TELEGRAM_TOKEN[:10]}...")

# NEPSE Trading Hours: Sunday to Thursday, 11 AM to 3 PM
TRADING_DAYS = [0, 1, 2, 3, 4]  # Monday to Friday
TRADING_HOURS_START = dt_time(11, 0)  # 11:00 AM
TRADING_HOURS_END = dt_time(15, 0)  # 3:00 PM

# Global flag to control monitoring
monitoring_active = False

def is_trading_hours():
    current_time = datetime.now().time()
    current_day = datetime.now().weekday()
    return current_day in TRADING_DAYS and TRADING_HOURS_START <= current_time <= TRADING_HOURS_END

async def fetch_nepse_data(max_retries=3, delay=5):
    logging.info("Fetching NEPSE data...")
    for attempt in range(max_retries):
        try:
            nepse_market_client = Client()
            stock_data = await nepse_market_client.get_today_price() if hasattr(nepse_market_client, 'get_today_price') else []
            historical_data = await nepse_market_client.get_historical_data(days=30) if hasattr(nepse_market_client, 'get_historical_data') else []
            logging.debug(f"Stock data: {stock_data[:2] if stock_data else None}")
            logging.debug(f"Historical data: {historical_data[:2] if historical_data else None}")
            return stock_data, historical_data
        except Exception as e:
            logging.error(f"Attempt {attempt + 1}/{max_retries} - Error fetching NEPSE data: {e}")
            if attempt < max_retries - 1:
                await asyncio.sleep(delay)
            else:
                logging.info("Using mock data as fallback due to max retries...")
                mock_stock_data = [
                    {'symbol': 'MOCK1', 'closingPrice': 109.00},
                    {'symbol': 'MOCK2', 'closingPrice': 200.00}
                ]
                return mock_stock_data, []

# News keywords for scanning
NEWS_KEYWORDS = [
    # Corporate Actions
    r"dividend declaration", r"bonus share", r"rights issue", r"merger", r"share buyback",
    # Financial Results
    r"quarterly results", r"net profit", r"earnings per share|EPS", r"revised guidance",
    # Regulatory & SEBON Notices
    r"trading halt", r"SEBON guidelines", r"IPO",
    # Macroeconomic Events
    r"rate hike", r"inflation data", r"GDP growth",
    # Mergers & Acquisitions
    r"due diligence", r"definitive agreement",
    # Market Sentiment
    r"rumor", r"insider trading", r"upgrade",
    # Technical Triggers
    r"circuit breaker", r"floorsheet anomaly",
    # Nepali terms
    r"मुनाफा", r"बोनस", r"हकप्रद"  # profit, bonus, rights issue
]

async def fetch_news():
    try:
        logging.info("Fetching news from sharesansar.com...")
        response = requests.get("https://www.sharesansar.com/category/latest")
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')
        news_items = soup.find_all('h5', class_='mb-0')
        news_texts = [item.get_text(strip=True) for item in news_items]
        return news_texts
    except Exception as e:
        logging.error(f"Error fetching news: {e}")
        return []

async def analyze_news(news_texts):
    analyzer = SentimentIntensityAnalyzer()
    relevant_news = []
    for text in news_texts:
        # Check for any matching keywords
        for keyword in NEWS_KEYWORDS:
            if re.search(keyword, text, re.IGNORECASE):
                sentiment = analyzer.polarity_scores(text)
                relevant_news.append({
                    'text': text,
                    'sentiment': sentiment['compound'],
                    'keywords': keyword
                })
                break
    return relevant_news

async def identify_good_opportunities(analysis_results):
    for stock in analysis_results:
        stock['rsi'] < 30 and  # Oversold
        stock['ma_trend'] == "Above MA" and  # Upward trend
        stock['sentiment'] > 0.3 and  # Positive news
        stock['volume_spike'] > 5 and  # High volatility
        stock['volume_spike']  # Volume spike

        good_opportunities = []
        stock_data, historical_data = await fetch_nepse_data()

        # Mock analysis for simplicity
        for stock in stock_data:
            analysis = {
                'symbol': stock['symbol'],
                'rsi': stock['closingPrice'] % 100,  # Simplified mock RSI
                'ma_trend': "Above MA" if stock['closingPrice'] > 150 else "Below MA",
                'volume_spike': stock['closingPrice'] / 20,
                'current_price': stock['closingPrice']
            }

            # Analyze news
            news_texts = await fetch_news()
            news_analysis = await analyze_news(news_texts)
            analysis['news'] = news_analysis
            analysis['sentiment'] = sum(item['sentiment'] for item in news_analysis) / len(news_analysis) if news_analysis else 0

            if (
                analysis['rsi'] < 30 and  # Oversold
                analysis['ma_trend'] == "Above MA" and  # Upward trend
                analysis['sentiment'] > 0.3 and  # Positive news
                analysis['volume_spike'] > 5  # High volume spike
            ):
                good_opportunities.append({
                    'name': stock['symbol'],
                    'rsi': stock['rsi'],
                    'ma_trend': stock['ma_trend'],
                    'sentiment': stock['sentiment'],
                    'volume_spike': stock['volume_spike'],
                    'current_price': stock['current_price'],
                    'news': stock['news']  # Include news for the output
                })
        return good_opportunities

# Bot Message Handler Commands: ['start']
async def start(message: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = message.chat.id
    logging.info(f"Received /start command from user {user_id}")
    await message.reply_text(
        "Welcome to Nepse AI Trading Bot! Available commands:\n"
        "/start - Show this message\n"
        "/help - List all available commands\n"
        "/monitor - Start monitoring stocks\n"
        "/status - Check bot and market status\n"
        "/opportunities - Get good stock opportunities\n"
        "/stop - Stop monitoring"
    )

# Bot Message Handler Commands: ['help']
async def help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Welcome to Nepse AI Trading Bot! Available commands:\n"
        "/start - Show this message\n"
        "/help - List all available commands\n"
        "/monitor - Start monitoring stocks\n"
        "/status - Check bot and market status\n"
        "/opportunities - Get good stock opportunities\n"
        "/stop - Stop monitoring"
    )

# Bot Message Handler Commands: ['status']
async def status(message: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = message.chat.id
    logging.info(f"Received /status command from user {user_id}")
    market_status = "OPEN" if is_trading_hours() else "CLOSED"
    bot_status = "monitoring" if monitoring_active else "idle"
    await message.reply_text(f"Bot Status: {bot_status}\nMarket Status: NEPSE is {market_status}")

# Bot Message Handler Commands: ['opportunities']
async def opportunities(message: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = message.chat.id
    logging.info(f"Received /opportunities command from user {user_id}")
    good_opps = await identify_good_opportunities([])
    if not good_opps:
        await message.reply_text("No good opportunities found at the moment.")
        return
    for opp in good_opps:
        news_summary = "\n".join([f"- {item['text']} (Sentiment: {item['sentiment']:.2f})" for item in opp['news']]) if opp['news'] else "No relevant news."
        await message.reply_text(
            f"Opportunity: {opp['name']}\n"
            f"RSI: {opp['rsi']}\n"
            f"MA Trend: {opp['ma_trend']}\n"
            f"Sentiment: {opp['sentiment']:.2f}\n"
            f"Volume Spike: {opp['volume_spike']:.2f}\n"
            f"Price: {opp['current_price']}\n"
            f"News:\n{news_summary}"
        )

# Add handlers to the application
app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("help", help))
app.add_handler(CommandHandler("status", status))
app.add_handler(CommandHandler("opportunities", opportunities))

# Start the bot
app.run_polling()import os
import asyncio
import re
from datetime import datetime, time as dt_time
import logging
import requests
from bs4 import BeautifulSoup
from nepse.core.client import Client
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
from telegram.ext import Application, CommandHandler, ContextTypes
from telegram import Update

print("Starting NEPSE Alert Bot...")

# Set up logging
logging.basicConfig(level=logging.DEBUG, format="%(asctime)s - %(levelname)s - %(message)s")

# Initialize Telegram Bot
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
if not TELEGRAM_TOKEN:
    raise ValueError("Please set the TELEGRAM_TOKEN environment variable.")
app = Application.builder().token(TELEGRAM_TOKEN).build()
logging.info(f"Telegram Bot initialized successfully. TELEGRAM_TOKEN: {TELEGRAM_TOKEN[:10]}...")

# NEPSE Trading Hours: Sunday to Thursday, 11 AM to 3 PM
TRADING_DAYS = [0, 1, 2, 3, 4]  # Monday to Friday
TRADING_HOURS_START = dt_time(11, 0)  # 11:00 AM
TRADING_HOURS_END = dt_time(15, 0)  # 3:00 PM

# Global flag to control monitoring
monitoring_active = False

def is_trading_hours():
    current_time = datetime.now().time()
    current_day = datetime.now().weekday()
    return current_day in TRADING_DAYS and TRADING_HOURS_START <= current_time <= TRADING_HOURS_END

async def fetch_nepse_data(max_retries=3, delay=5):
    logging.info("Fetching NEPSE data...")
    for attempt in range(max_retries):
        try:
            nepse_market_client = Client()
            stock_data = await nepse_market_client.get_today_price() if hasattr(nepse_market_client, 'get_today_price') else []
            historical_data = await nepse_market_client.get_historical_data(days=30) if hasattr(nepse_market_client, 'get_historical_data') else []
            logging.debug(f"Stock data: {stock_data[:2] if stock_data else None}")
            logging.debug(f"Historical data: {historical_data[:2] if historical_data else None}")
            return stock_data, historical_data
        except Exception as e:
            logging.error(f"Attempt {attempt + 1}/{max_retries} - Error fetching NEPSE data: {e}")
            if attempt < max_retries - 1:
                await asyncio.sleep(delay)
            else:
                logging.info("Using mock data as fallback due to max retries...")
                mock_stock_data = [
                    {'symbol': 'MOCK1', 'closingPrice': 109.00},
                    {'symbol': 'MOCK2', 'closingPrice': 200.00}
                ]
                return mock_stock_data, []

# News keywords for scanning
NEWS_KEYWORDS = [
    # Corporate Actions
    r"dividend declaration", r"bonus share", r"rights issue", r"merger", r"share buyback",
    # Financial Results
    r"quarterly results", r"net profit", r"earnings per share|EPS", r"revised guidance",
    # Regulatory & SEBON Notices
    r"trading halt", r"SEBON guidelines", r"IPO",
    # Macroeconomic Events
    r"rate hike", r"inflation data", r"GDP growth",
    # Mergers & Acquisitions
    r"due diligence", r"definitive agreement",
    # Market Sentiment
    r"rumor", r"insider trading", r"upgrade",
    # Technical Triggers
    r"circuit breaker", r"floorsheet anomaly",
    # Nepali terms
    r"मुनाफा", r"बोनस", r"हकप्रद"  # profit, bonus, rights issue
]

# Educational explanations for news keywords
NEWS_EXPLANATIONS = {
    "dividend declaration": "This may increase stock demand due to expected payouts.",
    "bonus share": "This increases the number of shares, potentially boosting liquidity.",
    "rights issue": "Shareholders can buy more shares, often at a discount, affecting price.",
    "merger": "Mergers can lead to synergies but may also introduce uncertainty.",
    "share buyback": "This reduces outstanding shares, often signaling confidence.",
    "quarterly results": "Results can drive volatility based on performance.",
    "net profit": "Higher profits typically boost stock price.",
    "earnings per share|EPS": "EPS reflects profitability per share, a key metric.",
    "revised guidance": "Guidance changes can shift investor expectations.",
    "trading halt": "Halts pause trading, often due to major news.",
    "SEBON guidelines": "Regulatory changes can impact market operations.",
    "IPO": "New listings can attract investor interest.",
    "rate hike": "Higher rates may reduce borrowing, impacting growth stocks.",
    "inflation data": "Inflation affects purchasing power and interest rates.",
    "GDP growth": "Economic growth can boost market confidence.",
    "due diligence": "A step in M&A, indicating a deal is progressing.",
    "definitive agreement": "A confirmed deal, often leading to price movements.",
    "rumor": "Rumors can drive short-term volatility.",
    "insider trading": "Insider activity may signal confidence or concern.",
    "upgrade": "Analyst upgrades often lead to price increases.",
    "circuit breaker": "Trading pauses to prevent extreme volatility.",
    "floorsheet anomaly": "Unusual trading activity may indicate manipulation.",
    "मुनाफा": "Higher profits typically boost stock price.",
    "बोनस": "This increases the number of shares, potentially boosting liquidity.",
    "हकप्रद": "Shareholders can buy more shares, often at a discount."
}

async def fetch_news():
    try:
        logging.info("Fetching news from sharesansar.com...")
        response = requests.get("https://www.sharesansar.com/category/latest")
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')
        news_items = soup.find_all('h5', class_='mb-0')
        news_texts = [item.get_text(strip=True) for item in news_items]
        return news_texts
    except Exception as e:
        logging.error(f"Error fetching news: {e}")
        return []

async def analyze_news(news_texts):
    analyzer = SentimentIntensityAnalyzer()
    relevant_news = []
    for text in news_texts:
        # Check for any matching keywords
        for keyword in NEWS_KEYWORDS:
            if re.search(keyword, text, re.IGNORECASE):
                sentiment = analyzer.polarity_scores(text)
                relevant_news.append({
                    'text': text,
                    'sentiment': sentiment['compound'],
                    'keywords': keyword
                })
                break
    return relevant_news

async def identify_good_opportunities(analysis_results):
    good_opportunities = []
    stock_data, historical_data = await fetch_nepse_data()

    # Mock analysis for simplicity
    for stock in stock_data:
        analysis = {
            'symbol': stock['symbol'],
            'rsi': stock['closingPrice'] % 100,  # Simplified mock RSI
            'ma_trend': "Above MA" if stock['closingPrice'] > 150 else "Below MA",
            'volume_spike': stock['closingPrice'] / 20,
            'current_price': stock['closingPrice']
        }

        # Analyze news
        news_texts = await fetch_news()
        news_analysis = await analyze_news(news_texts)
        analysis['news'] = news_analysis
        analysis['sentiment'] = sum(item['sentiment'] for item in news_analysis) / len(news_analysis) if news_analysis else 0

        if (
            analysis['rsi'] < 30 and  # Oversold
            analysis['ma_trend'] == "Above MA" and  # Upward trend
            analysis['sentiment'] > 0.3 and  # Positive news
            analysis['volume_spike'] > 5  # High volume spike
        ):
            good_opportunities.append({
                'name': stock['symbol'],
                'rsi': analysis['rsi'],
                'ma_trend': analysis['ma_trend'],
                'sentiment': analysis['sentiment'],
                'volume_spike': analysis['volume_spike'],
                'current_price': analysis['current_price'],
                'news': analysis['news']  # Include news for the output
            })
    return good_opportunities

# Bot Message Handler Commands: ['start']
async def start(message: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = message.chat.id
    logging.info(f"Received /start command from user {user_id}")
    await message.reply_text(
        "Welcome to Nepse AI Trading Bot! Available commands:\n"
        "/start - Show this message\n"
        "/help - List all available commands\n"
        "/monitor - Start monitoring stocks\n"
        "/status - Check bot and market status\n"
        "/opportunities - Get good stock opportunities\n"
        "/stop - Stop monitoring"
    )

# Bot Message Handler Commands: ['help']
async def help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Welcome to Nepse AI Trading Bot! Available commands:\n"
        "/start - Show this message\n"
        "/help - List all available commands\n"
        "/monitor - Start monitoring stocks\n"
        "/status - Check bot and market status\n"
        "/opportunities - Get good stock opportunities\n"
        "/stop - Stop monitoring"
    )

# Bot Message Handler Commands: ['status']
async def status(message: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = message.chat.id
    logging.info(f"Received /status command from user {user_id}")
    market_status = "OPEN" if is_trading_hours() else "CLOSED"
    bot_status = "monitoring" if monitoring_active else "idle"
    await message.reply_text(f"Bot Status: {bot_status}\nMarket Status: NEPSE is {market_status}")

# Bot Message Handler Commands: ['opportunities']
async def opportunities(message: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = message.chat.id
    logging.info(f"Received /opportunities command from user {user_id}")
    good_opps = await identify_good_opportunities([])
    if not good_opps:
        await message.reply_text("No good opportunities found at the moment.")
        return
    for opp in good_opps:
        news_summary = ""
        if opp['news']:
            for item in opp['news']:
                explanation = next((NEWS_EXPLANATIONS[key] for key in NEWS_EXPLANATIONS if re.search(key, item['text'], re.IGNORECASE)), "This news may impact the stock.")
                news_summary += f"- {item['text']} (Sentiment: {item['sentiment']:.2f})\n  * {explanation}\n"
        else:
            news_summary = "No relevant news."
        await message.reply_text(
            f"Opportunity: {opp['name']}\n"
            f"RSI: {opp['rsi']}\n"
            f"MA Trend: {opp['ma_trend']}\n"
            f"Sentiment: {opp['sentiment']:.2f}\n"
            f"Volume Spike: {opp['volume_spike']:.2f}\n"
            f"Price: {opp['current_price']}\n"
            f"News:\n{news_summary}"
        )

# Bot Message Handler Commands: ['monitor']
async def monitor(message: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    global monitoring_active
    user_id = message.chat.id
    logging.info(f"Received /monitor command from user {user_id}")
    if monitoring_active:
        await message.reply_text("Monitoring is already active.")
        return
    monitoring_active = True
    await message.reply_text("Started monitoring NEPSE stocks. Use /stop to stop monitoring.")
    
    while monitoring_active:
        if is_trading_hours():
            stock_data, historical_data = await fetch_nepse_data()
            good_opps = await identify_good_opportunities(stock_data)
            if good_opps:
                for opp in good_opps:
                    news_summary = ""
                    if opp['news']:
                        for item in opp['news']:
                            explanation = next((NEWS_EXPLANATIONS[key] for key in NEWS_EXPLANATIONS if re.search(key, item['text'], re.IGNORECASE)), "This news may impact the stock.")
                            news_summary += f"- {item['text']} (Sentiment: {item['sentiment']:.2f})\n  * {explanation}\n"
                    else:
                        news_summary = "No relevant news."
                    await message.reply_text(
                        f"Opportunity Alert: {opp['name']}\n"
                        f"RSI: {opp['rsi']}\n"
                        f"MA Trend: {opp['ma_trend']}\n"
                        f"Sentiment: {opp['sentiment']:.2f}\n"
                        f"Volume Spike: {opp['volume_spike']:.2f}\n"
                        f"Price: {opp['current_price']}\n"
                        f"News:\n{news_summary}"
                    )
        else:
            await message.reply_text("Market is closed. Using mock data for testing.")
            stock_data, historical_data = await fetch_nepse_data()
            good_opps = await identify_good_opportunities(stock_data)
            if good_opps:
                for opp in good_opps:
                    news_summary = ""
                    if opp['news']:
                        for item in opp['news']:
                            explanation = next((NEWS_EXPLANATIONS[key] for key in NEWS_EXPLANATIONS if re.search(key, item['text'], re.IGNORECASE)), "This news may impact the stock.")
                            news_summary += f"- {item['text']} (Sentiment: {item['sentiment']:.2f})\n  * {explanation}\n"
                    else:
                        news_summary = "No relevant news."
                    await message.reply_text(
                        f"Mock Opportunity Alert: {opp['name']}\n"
                        f"RSI: {opp['rsi']}\n"
                        f"MA Trend: {opp['ma_trend']}\n"
                        f"Sentiment: {opp['sentiment']:.2f}\n"
                        f"Volume Spike: {opp['volume_spike']:.2f}\n"
                        f"Price: {opp['current_price']}\n"
                        f"News:\n{news_summary}"
                    )
        await asyncio.sleep(300)  # Check every 5 minutes

# Bot Message Handler Commands: ['stop']
async def stop(message: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    global monitoring_active
    user_id = message.chat.id
    logging.info(f"Received /stop command from user {user_id}")
    if not monitoring_active:
        await message.reply_text("Monitoring is not active.")
        return
    monitoring_active = False
    await message.reply_text("Stopped monitoring NEPSE stocks.")

# Add handlers to the application
app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("help", help))
app.add_handler(CommandHandler("status", status))
app.add_handler(CommandHandler("opportunities", opportunities))
app.add_handler(CommandHandler("monitor", monitor))
app.add_handler(CommandHandler("stop", stop))

# Start the bot
app.run_polling()

@bot.message_handler(commands=['status'])
async def status(message):
    user_id = message.chat.id
    logging.info(f"Received /status command from user {user_id}")
    market_status = "open" if is_trading_hours() else "closed"
    bot_status = "monitoring" if monitoring_active else "idle"
    await bot.reply_to(message, f"Bot Status: {bot_status}\nMarket Status: NEPSE is {market_status}")

@bot.message_handler(commands=['opportunities'])
async def opportunities(message):
    user_id = message.chat.id
    logging.info(f"Received /opportunities command from user {user_id}")
    stock_data, historical_data = await fetch_nepse_data()
    if not stock_data:
        await bot.reply_to(message, "Failed to fetch stock data. Please try again later.")
        return
    analysis_results = await analyze_stock(stock_data, historical_data)
    good_opportunities = await identify_good_opportunities(analysis_results)
    if good_opportunities:
        await bot.reply_to(message, "💡 Good Opportunities (Potential Gains in 2+ Days):")
        for stock in good_opportunities:
            message = (f"Stock: {stock['name']}\n"
                       f"RSI: {stock['rsi']:.2f}\n"
                       f"Current Price: {stock['current_price']}\n"
                       f"News Sentiment: {stock['sentiment']:.2f}\n"
                       f"Recent News: {'; '.join(stock['news'])}\n"
                       f"Volatility: {stock['volatility']:.2f}%\n"
                       f"Volume Spike: {'Yes' if stock['volume_spike'] else 'No'}\n"
                       f"MA Trend: {stock['ma_trend']}")
            await bot.send_message(user_id, message)
    else:
        await bot.reply_to(message, "No good opportunities found at the moment.")

@bot.message_handler(commands=['stop'])
async def stop(message):
    global monitoring_active
    user_id = message.chat.id
    logging.info(f"Received /stop command from user {user_id}")
    if monitoring_active:
        monitoring_active = False
        await bot.reply_to(message, "Monitoring stopped. Use /monitor to start again.")
    else:
        await bot.reply_to(message, "Monitoring is already stopped.")

@bot.message_handler(commands=['monitor'])
async def monitor_stocks(message):
    global monitoring_active
    user_id = message.chat.id
    logging.info(f"Received /monitor command from user {user_id}")
    if monitoring_active:
        await bot.reply_to(message, "Monitoring is already active. Use /stop to stop monitoring.")
        return
    monitoring_active = True
    await bot.reply_to(message, "Starting stock monitoring...")

    while monitoring_active:
        if is_trading_hours():
            stock_data, historical_data = await fetch_nepse_data()
            if not stock_data:
                await bot.send_message(user_id, "Failed to fetch stock data. Retrying in 5 minutes.")
                await asyncio.sleep(300)
                continue

            analysis_results = await analyze_stock(stock_data, historical_data)
            good_opportunities = await identify_good_opportunities(analysis_results)

            if good_opportunities:
                await bot.send_message(user_id, "💡 Good Opportunities Detected (Potential Gains in 2+ Days):")
                for stock in good_opportunities:
                    message = (f"Stock: {stock['name']}\n"
                               f"RSI: {stock['rsi']:.2f}\n"
                               f"Current Price: {stock['current_price']}\n"
                               f"News Sentiment: {stock['sentiment']:.2f}\n"
                               f"Recent News: {'; '.join(stock['news'])}\n"
                               f"Volatility: {stock['volatility']:.2f}%\n"
                               f"Volume Spike: {'Yes' if stock['volume_spike'] else 'No'}\n"
                               f"MA Trend: {stock['ma_trend']}")
                    await bot.send_message(user_id, message)

            for stock in analysis_results:
                if stock['is_dangerous']:
                    await bot.send_message(user_id, f"⚠️ Dangerous News Alert for {stock['name']}:\n"
                                                    f"News Sentiment: {stock['sentiment']:.2f}\n"
                                                    f"Recent News: {'; '.join(stock['news'])}\n"
                                                    f"This could impact the market!")
                else:
                    message = (f"Stock Alert: {stock['name']}\n"
                               f"Fundamental Score: {stock['fundamental_score']}\n"
                               f"Technical Score: {stock['technical_score']}\n"
                               f"RSI: {stock['rsi']:.2f}\n"
                               f"MA Trend: {stock['ma_trend']}\n"
                               f"News Sentiment: {stock['sentiment']:.2f}\n"
                               f"Recommendation: {stock['recommendation']}\n"
                               f"Recent News: {'; '.join(stock['news'])}")
                    await bot.send_message(user_id, message)

            await asyncio.sleep(300)  # Check every 5 minutes during trading hours

        else:
            await bot.send_message(user_id, "Market is closed. Monitoring will resume during trading hours.")
            stock_data, historical_data = await fetch_nepse_data()
            if stock_data and historical_data:
                analysis_results = await analyze_stock(stock_data, historical_data)
                good_opportunities = await identify_good_opportunities(analysis_results)

                if good_opportunities:
                    await bot.send_message(user_id, "💡 Good Opportunities for Next Session (Potential Gains in 2+ Days):")
                    for stock in good_opportunities:
                        message = (f"Stock: {stock['name']}\n"
                                   f"RSI: {stock['rsi']:.2f}\n"
                                   f"Current Price: {stock['current_price']}\n"
                                   f"News Sentiment: {stock['sentiment']:.2f}\n"
                                   f"Recent News: {'; '.join(stock['news'])}\n"
                                   f"Volatility: {stock['volatility']:.2f}%\n"
                                   f"Volume Spike: {'Yes' if stock['volume_spike'] else 'No'}\n"
                                   f"MA Trend: {stock['ma_trend']}")
                        await bot.send_message(user_id, message)

                big_movers = await identify_big_movers(stock_data, historical_data)
                if big_movers:
                    await bot.send_message(user_id, "Potential Big Movers for Tomorrow:")
                    for stock in big_movers:
                        message = (f"Stock: {stock['name']}\n"
                                   f"Volatility: {stock['volatility']:.2f}%\n"
                                   f"Volume Spike: {'Yes' if stock['volume_spike'] else 'No'}\n"
                                   f"Near 52-Week High: {'Yes' if stock['near_52w_high'] else 'No'}\n"
                                   f"Current Price: {stock['current_price']}")
                        await bot.send_message(user_id, message)

                for stock in analysis_results:
                    if stock['is_dangerous']:
                        await bot.send_message(user_id, f"⚠️ Dangerous News Alert for {stock['name']}:\n"
                                                        f"News Sentiment: {stock['sentiment']:.2f}\n"
                                                        f"Recent News: {'; '.join(stock['news'])}\n"
                                                        f"This could impact the market!")
                    message = (f"Post-Market Update: {stock['name']}\n"
                               f"Fundamental Score: {stock['fundamental_score']}\n"
                               f"Technical Score: {stock['technical_score']}\n"
                               f"RSI: {stock['rsi']:.2f}\n"
                               f"MA Trend: {stock['ma_trend']}\n"
                               f"News Sentiment: {stock['sentiment']:.2f}\n"
                               f"Recent News: {'; '.join(stock['news'])}")
                    await bot.send_message(user_id, message)

            logging.info("Outside trading hours. Waiting for the next check.")
            await asyncio.sleep(1800)  # Check every 30 minutes outside trading hours
            continue

# Start the bot
if __name__ == "__main__":
    logging.info("Starting bot polling...")
    print("Bot is now polling for messages...")
    asyncio.run(bot.polling())
