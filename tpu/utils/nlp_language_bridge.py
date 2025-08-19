import html
import re

from deep_translator import GoogleTranslator
from langdetect import detect


def detect_language(text):
    try:
        return detect(text)
    except:
        return "unknown"

def translate_to_english(text):
    try:
        return GoogleTranslator(source='auto', target='en').translate(text)
    except:
        return text

def clean_text(text):
    text = html.unescape(text)
    text = re.sub(r'<.*?>', '', text)
    text = re.sub(r'[^\x00-\x7F]+', ' ', text)  # remove non-ASCII
    text = re.sub(r'\s+', ' ', text).strip()
    return text

def process_text(text):
    lang = detect_language(text)
    if lang != "en":
        text = translate_to_english(text)
    return clean_text(text)
