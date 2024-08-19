import os
import logging
from datetime import datetime
from newsapi import NewsApiClient
from oauth2client.service_account import ServiceAccountCredentials
import gspread
import tweepy
import spacy
from collections import Counter
import re

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

# Load the English NLP model
nlp = spacy.load("en_core_web_sm")

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
        
        if len(all_data) == 1:
            logging.info("Sheet only contains headers, no data yet")
            return {
                'posted_articles': [],
                'news_api_requests': 0,
                'tweets_today': 0,
                'tweets_this_month': 0,
                'last_tweet_time': None
            }
        
        data = all_data[1:]
        logging.debug(f"Data rows: {data}")
        
        try:
            url_index = headers.index('url')
            news_api_requests_index = headers.index('news_api_requests')
            tweets_today_index = headers.index('tweets_today')
            tweets_this_month_index = headers.index('tweets_this_month')
            last_tweet_time_index = headers.index('last_tweet_time')
            
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
    if data['news_api_requests'] >= 1000:
        logging.warning("Monthly NewsAPI request limit reached")
        return None

    logging.info("Fetching news articles...")
    try:
        newsapi = NewsApiClient(api_key=NEWS_API_KEY)
        all_articles = newsapi.get_everything(q='politics OR government OR elections OR (president OR Biden OR Trump OR Kamala OR Harris OR Democrats OR Republicans) -ads -wired -gizmodo',
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

def summarize_text(text, max_length):
    doc = nlp(text)
    sentences = list(doc.sents)
    summary = ""
    for sentence in sentences:
        if len(summary) + len(sentence.text) <= max_length:
            summary += sentence.text + " "
        else:
            break
    return summary.strip()

def generate_hashtags(text):
    doc = nlp(text)
    
    # Extract entities and noun chunks
    entities = [ent.text.lower() for ent in doc.ents if ent.label_ in ['ORG', 'PERSON', 'GPE', 'EVENT']]
    noun_chunks = [chunk.text.lower() for chunk in doc.noun_chunks if len(chunk.text.split()) <= 2]
    
    # Combine and count occurrences
    important_phrases = entities + noun_chunks
    phrase_counts = Counter(important_phrases)
    
    # Sort phrases by count (descending) and then by length (ascending)
    sorted_phrases = sorted(phrase_counts.items(), key=lambda x: (-x[1], len(x[0])))
    
    hashtags = []
    for phrase, _ in sorted_phrases:
        if len(hashtags) >= 3:
            break
        
        # Clean the phrase: remove non-alphanumeric characters and spaces
        clean_phrase = re.sub(r'[^\w\s]', '', phrase)
        clean_phrase = re.sub(r'\s+', '', clean_phrase)
        
        # Capitalize each word
        hashtag = "#" + clean_phrase.title()
        
        # Ensure the hashtag is not too long and not already in the list
        if len(hashtag) > 1 and len(hashtag) <= 20 and hashtag not in hashtags:
            hashtags.append(hashtag)
    
    return hashtags

def create_tweet_text(all_articles, posted_articles):
    if not all_articles or "articles" not in all_articles:
        logging.warning("No articles found in the API response")
        return None, None

    for article in all_articles["articles"]:
        if is_valid_article(article, posted_articles):
            title = article.get("title", "").strip()
            description = article.get("description", "").strip()
            url = article.get("url", "")

            full_text = f"{title}. {description}"
            summary = summarize_text(full_text, 180)

            hashtags = generate_hashtags(full_text)

            tweet_text = f"{summary}\n{url}\n{' '.join(hashtags)}"

            if len(tweet_text) > 280:
                tweet_text = tweet_text[:277] + "..."

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
    
    if data['last_tweet_time']:
        last_tweet = datetime.fromisoformat(data['last_tweet_time'])
        if current_time.date() != last_tweet.date():
            data['tweets_today'] = 0
        if current_time.month != last_tweet.month:
            data['tweets_this_month'] = 0

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
        response = client.create_tweet(text=tweet_text)
        logging.info("Tweet posted successfully")
        
        data['tweets_today'] += 1
        data['tweets_this_month'] += 1
        data['last_tweet_time'] = current_time.isoformat()
        
        return True
    except Exception as e:
        logging.error(f"Failed to post tweet: {e}")
        return False

def main():
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

if __name__ == "__main__":
    main()
