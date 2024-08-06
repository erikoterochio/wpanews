import os
import time
import logging
from datetime import datetime
from newsapi import NewsApiClient
from oauth2client.service_account import ServiceAccountCredentials
import requests
import gspread
import tweepy

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Configuration
NEWS_API_KEY = os.environ.get("NEWS_API_KEY")
NEWS_API_URL = 'https://newsapi.org/v2/top-headlines'
TWITTER_API_URL = "https://api.twitter.com/2/tweets"
API_KEY = os.environ.get("API_KEY")
API_KEY_SECRET = os.environ.get("API_KEY_SECRET")
ACCESS_TOKEN = os.environ.get("ACCESS_TOKEN")
ACCESS_TOKEN_SECRET = os.environ.get("ACCESS_TOKEN_SECRET")

# Google Sheets setup
GOOGLE_SHEETS_CREDENTIALS = os.environ.get("GOOGLE_SHEETS_CREDENTIALS")
SHEET_ID = os.environ.get("SHEET_ID")

def get_sheet():
    creds = ServiceAccountCredentials.from_json_keyfile_dict(
        eval(GOOGLE_SHEETS_CREDENTIALS),
        ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    )
    client = gspread.authorize(creds)
    return client.open_by_key(SHEET_ID).sheet1

def load_data():
    sheet = get_sheet()
    all_data = sheet.get_all_values()
    logging.debug(f"Raw sheet data: {all_data}")
    
    expected_headers = ['url', 'timestamp', 'news_api_requests', 'tweets_today', 'tweets_this_month', 'last_tweet_time']
    
    if not all_data:
        # Sheet is completely empty, add headers
        logging.info("Initializing empty sheet with headers")
        sheet.append_row(expected_headers)
        return {
            'posted_articles': [],
            'news_api_requests': 0,
            'tweets_today': 0,
            'tweets_this_month': 0,
            'last_tweet_time': None
        }
    else:
        headers = all_data[0]
        logging.debug(f"Existing headers: {headers}")
        
        if headers != expected_headers:
            logging.warning(f"Existing headers do not match expected headers. Existing: {headers}, Expected: {expected_headers}")
            # You might want to handle this situation, e.g., by updating the headers or raising an error
        
        if len(all_data) == 1:  # Only headers exist
            logging.info("Sheet only contains headers, no data yet")
            return {
                'posted_articles': [],
                'news_api_requests': 0,
                'tweets_today': 0,
                'tweets_this_month': 0,
                'last_tweet_time': None
            }
        
        data = all_data[1:]  # Exclude header row
        logging.debug(f"Data rows: {data}")
        
        try:
            # Find the index of each column
            url_index = headers.index('url')
            news_api_requests_index = headers.index('news_api_requests')
            tweets_today_index = headers.index('tweets_today')
            tweets_this_month_index = headers.index('tweets_this_month')
            last_tweet_time_index = headers.index('last_tweet_time')
            
            # Get the last row of data
            last_row = data[-1]
            logging.debug(f"Last row: {last_row}")
            
            return {
                'posted_articles': [row[url_index] for row in data if row[url_index]],
                'news_api_requests': int(last_row[news_api_requests_index]) if last_row[news_api_requests_index].isdigit() else 0,
                'tweets_today': int(last_row[tweets_today_index]) if last_row[tweets_today_index].isdigit() else 0,
                'tweets_this_month': int(last_row[tweets_this_month_index]) if last_row[tweets_this_month_index].isdigit() else 0,
                'last_tweet_time': last_row[last_tweet_time_index] if last_row[last_tweet_time_index] else None
            }
        except Exception as e:
            logging.error(f"Error processing sheet data: {e}")
            return {
                'posted_articles': [],
                'news_api_requests': 0,
                'tweets_today': 0,
                'tweets_this_month': 0,
                'last_tweet_time': None
            }

def save_data(data, url):
    sheet = get_sheet()
    sheet.append_row([
        url,
        datetime.now().isoformat(),
        data['news_api_requests'],
        data['tweets_today'],
        data['tweets_this_month'],
        data['last_tweet_time']
    ])

def get_news(data):
    current_time = datetime.now()
    
    # Check if we've exceeded the monthly limit
    if data['news_api_requests'] >= 1000:
        logging.warning("Monthly NewsAPI request limit reached")
        return None

    logging.info("Fetching news articles...")
    try:
        newsapi = NewsApiClient(api_key=NEWS_API_KEY)
        all_articles = newsapi.get_everything(q='(politics OR government OR president) AND NOT (ads OR wired)',
                                              language='en',
                                              sort_by='popularity',
                                              page=1,
                                              page_size=100)
        
        data['news_api_requests'] += 1
        logging.info(f"Retrieved {len(all_articles.get('articles', []))} articles")
        return all_articles
    except Exception as e:
        logging.error(f"Error fetching news: {e}", exc_info=True)
        return None

def is_valid_article(article, posted_articles):
    title = article.get("title", "")
    description = article.get("description", "")
    content = article.get("content", "")
    url = article.get("url", "")
    
    if not title or not description or not content or url in posted_articles:
        return False
    if "[Removed]" in title:
        return False
    if "If you click 'Accept all', we and our partners" in description or "If you click 'Accept all', we and our partners" in content:
        return False
    return True

def create_tweet_text(all_articles, posted_articles):
    if not all_articles or "articles" not in all_articles:
        logging.warning("No articles found in the API response")
        return None, None

    for article in all_articles["articles"]:
        if is_valid_article(article, posted_articles):
            author = article.get("author", "Unknown Author")
            title = article.get("title", "No Title")
            description = article.get("description", "No Description")
            url = article.get("url", "")
            tweet_text = f"{author}: {title}\n{description}\nLink: {url}"
            logging.info(f"Created tweet text: {tweet_text[:50]}...")
            return tweet_text, url
    
    logging.warning("No valid article found to tweet")
    return None, None

def getClient():
    client = tweepy.Client(
        consumer_key=API_KEY,
        consumer_secret=API_KEY_SECRET,
        access_token=ACCESS_TOKEN,
        access_token_secret=ACCESS_TOKEN_SECRET
    )
    return client

def post_tweet(tweet_text, data, client):
    current_time = datetime.now()
    
    # Reset daily and monthly counters if needed
    if data['last_tweet_time']:
        last_tweet = datetime.fromisoformat(data['last_tweet_time'])
        if current_time.date() != last_tweet.date():
            data['tweets_today'] = 0
        if current_time.month != last_tweet.month:
            data['tweets_this_month'] = 0

    # Check if we've exceeded the daily or monthly limit
    if data['tweets_today'] >= 50:
        logging.warning("Daily tweet limit reached")
        return False
    if data['tweets_this_month'] >= 1500:
        logging.warning("Monthly tweet limit reached")
        return False

    if not tweet_text:
        logging.warning("No tweet text provided")
        return False

    try:
        logging.info("Attempting to post tweet...")
        response = client.create_tweet(text=tweet_text)  # Changed this line
        logging.info("Tweet posted successfully")
        
        data['tweets_today'] += 1
        data['tweets_this_month'] += 1
        data['last_tweet_time'] = current_time.isoformat()
        
        return True
    except Exception as e:  # Changed to catch any exception
        logging.error(f"Failed to post tweet: {e}")
        return False

def main():
    while True:
        data = load_data()
        all_articles = get_news(data)
        if all_articles:
            client = getClient()
            tweet_text, article_url = create_tweet_text(all_articles, data['posted_articles'])
            if tweet_text and post_tweet(tweet_text, data, client):
                save_data(data, article_url)
            else:
                logging.warning("Failed to create or post tweet")
        else:
            logging.warning("No news articles found or API limit reached")
        
        # Wait for 60 minutes before the next iteration
        logging.info(f"Next update in 60 minutes. Current time: {datetime.now()}")
        time.sleep(60 * 60)

if __name__ == "__main__":
    main()
