import os
from dotenv import load_dotenv


load_dotenv()

TOKEN = os.getenv("BOT_TOKEN")

GIGACHAT_TOKEN = os.getenv("GIGA_TOKEN")

PROXY_RU = os.getenv("PROXY_URL")

DOC_DIR = './docs/' 
PDF_KEYWORDS = ['условия договора кредитования', 'договор кредита', 'условия кредита']


FIELD_NAMES = {
    "name": "Наименование",
    "rate": "% Ставка",
    "rate_type": "Тип ставки",
    "sum": "Сумма",
    "term": "Срок",
    "payment_type": "Тип платежа",
    "commission": "Комиссии",
    "early_repayment": "Досрочное погашение",
    "insurance": "Страхование",
    "currency": "Валюта",
    "additional": "Дополнительно",
    "files": "Файлы",
}
