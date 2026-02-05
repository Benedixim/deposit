from aiogram import Router, F
import asyncio
import sqlite3
from aiogram.types import Message, CallbackQuery
from aiogram.filters import Command
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.types import FSInputFile
import glob
import fitz
from datetime import datetime
import json
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
import re
import os
from gigachat import GigaChat
import requests
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright

from aiogram.fsm.context import FSMContext

from app.keyboards.start_keyboard import get_info_keyboard, get_sets_keyboard
from app.state import BankState
from app.excel.py_xlsx import create_bank_excel_report
from app.db.model import (SessionLocal, User, Log, Data, Bank, Set, Product, Characteristic,
                           migrate_products, migrate_banks, migrate_characteristics, init_db, init_banks,engine)
from config import GIGACHAT_TOKEN, DOC_DIR, PDF_KEYWORDS, FIELD_NAMES

router = Router()


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

#---------------------Использование playwright для работы на сервере--------------------
async def get_page_content_playwright(url: str, timeout: int = 30000) -> str | None:
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=[
                    '--disable-blink-features=AutomationControlled',
                    '--start-maximized',
                ]
            )
            
            context = await browser.new_context(
                viewport={'width': 1920, 'height': 1080},
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            )
            
            page = await context.new_page()
            
            try:
                await page.goto(url, wait_until='networkidle', timeout=timeout)
                content = await page.content()
                await browser.close()
                return content
            except Exception as e:
                print(f"Playwright ошибка для {url}: {e}")
                await browser.close()
                return None
    except Exception as e:
        print(f"Критическая ошибка Playwright: {e}")
        return None


async def get_page_content(url: str) -> str | None:

    try:
        response = requests.get(
            url,
            timeout=10,
            verify=False,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            }
        )
        
        if response.status_code == 200 and len(response.text) > 500:
            print(f"{url}: загружено через requests")
            return response.text
    except Exception as e:
        print(f"-! requests не сработал для {url}: {type(e).__name__}")
    

    print(f"- Пробуем Playwright для {url}...")
    content = await get_page_content_playwright(url)
    
    if content and len(content) > 500:
        print(f"{url}: загружено через Playwright")
        return content
    
    print(f"-!!! Не удалось загрузить {url}")
    return None

#----------------------Handlers--------------------------
@router.message(Command("start"))
async def start_handler(message: Message, state: FSMContext):
    await message.answer(
        "👋 Добро пожаловать!\n\n"
        "Выберите **набор карт**:",
        parse_mode="Markdown",
        reply_markup=get_sets_keyboard()
    )
    await message.answer('кнопка "📊 Собрать информацию" добавлена', reply_markup=get_info_keyboard())


#activate migration
@router.message(Command("actv"))
async def start_multi(message: Message, state: FSMContext):
    init_db()
    init_banks()
    migrate_banks()
    migrate_products()
    migrate_characteristics()
    print("✅ Полная миграция завершена!")


@router.message(F.text == "📊 Собрать информацию")
async def click_button_start(message: Message, state: FSMContext):
    await message.answer( 
        "Выберите **набор карт**:",
        parse_mode="Markdown",
        reply_markup=get_sets_keyboard())

@router.message(Command('db'))
async def dump_data_base(message: Message):
    db_file_path = "credits.db"  
    
    try:
        document = FSInputFile(db_file_path)
        await message.answer_document(document, caption="Вот ваша база данных")
    except Exception as e:
        await message.answer(f"Ошибка при отправке файла: {e}")


async def show_products_keyboard(callback: CallbackQuery, state: FSMContext, set_id: int):
    data = await state.get_data()
    selected_products = set(data.get("selected_products", []))
    
    db = SessionLocal()
    try:
        products = db.query(Product).filter_by(set_id=set_id).all()
        set_obj = db.query(Set).filter_by(id=set_id).first()
        
        keyboard = []
        for product in products:
            is_selected = product.id in selected_products
            emoji = "✅" if is_selected else ""
            keyboard.append([InlineKeyboardButton(
                text=f"{emoji} {product.name}",
                callback_data=f"toggle_product_{product.id}"
            )])
        
        # Кнопки навигации
        keyboard.append([
            InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_set"),
            InlineKeyboardButton(text="➡️ Далее", callback_data="show_characteristics")
        ])
        
        set_name = set_obj.name if set_obj else "Набор"
        text = f"📦 **{set_name}**\n\nВыберите продукты\nВыбрано: {len(selected_products)}/{len(products)}"
        
        await callback.message.edit_text(
            text,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard)
        )
    finally:
        db.close()
    
    await callback.answer()


async def show_characteristics_keyboard(callback: CallbackQuery, state: FSMContext):
    """Отображение характеристик с мультивыбором"""
    data = await state.get_data()
    selected_chars = set(data.get("selected_characteristics", []))
    
    db = SessionLocal()
    try:
        chars = db.query(Characteristic).all()
        
        keyboard = []
        for char in chars:
            is_selected = char.id in selected_chars
            emoji = "✅" if is_selected else ""
            display_name = FIELD_NAMES.get(char.name, char.name)
            keyboard.append([InlineKeyboardButton(
                text=f"{emoji} {display_name}",
                callback_data=f"toggle_char_{char.id}"
            )])
        
        # Кнопки навигации
        keyboard.append([
            InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_products"),
            InlineKeyboardButton(text="➡️ Далее", callback_data="confirm_selection")
        ])
        
        text = f"Выберите характеристики\nВыбрано: {len(selected_chars)}/{len(chars)}"
        
        await callback.message.edit_text(
            text,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard)
        )
    finally:
        db.close()
    
    await callback.answer()


async def show_confirmation(callback: CallbackQuery, state: FSMContext):
    """Показывает подтверждение выбора"""
    data = await state.get_data()
    selected_products = data.get("selected_products", [])
    selected_chars = data.get("selected_characteristics", [])
    
    db = SessionLocal()
    try:
        # Получаем имена продуктов
        product_objects = db.query(Product).filter(Product.id.in_(selected_products)).all()
        product_names = [p.name for p in product_objects]
        
        # Получаем имена характеристик
        char_objects = db.query(Characteristic).filter(Characteristic.id.in_(selected_chars)).all()
        char_names = [c.name for c in char_objects]
        display_char_names = [FIELD_NAMES.get(name, name) for name in char_names]
        
        # Получаем уникальные банки
        bank_ids = set(p.bank_id for p in product_objects)
        banks = db.query(Bank).filter(Bank.id.in_(bank_ids)).all()
        bank_names = [b.name for b in banks]
        
        keyboard = [
            [InlineKeyboardButton(text="✅ Да, начать парсинг", callback_data="start_parsing")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_characteristics")]
        ]
        
        text = (
            "📋 **Подтверждение выбора**\n\n"
            f"**Продукты:** {', '.join(product_names)}\n\n"
            f"**Характеристики:** {', '.join(display_char_names)}\n\n"
            f"**Банки:** {', '.join(bank_names)}\n\n"
            "Начать парсинг?"
        )
        
        await callback.message.edit_text(
            text,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard)
        )
    finally:
        db.close()
    
    await callback.answer()


@router.callback_query(F.data == "set_credits")
async def show_standard_products(callback: CallbackQuery, state: FSMContext):
    db = SessionLocal()
    try:
        set_obj = db.query(Set).filter_by(name="Кредиты").first()
        if set_obj:
            await state.update_data(selected_set_id=set_obj.id)
            await state.set_state(BankState.waiting_products)
            await show_products_keyboard(callback, state, set_obj.id)
        else:
            await callback.answer("❌ Набор 'Кредиты' не найден")
    finally:
        db.close()


@router.callback_query(F.data == "set_deposit")
async def show_premium_products(callback: CallbackQuery, state: FSMContext):
    db = SessionLocal()
    try:
        set_obj = db.query(Set).filter_by(name="Депозиты").first()
        if set_obj:
            await state.update_data(selected_set_id=set_obj.id)
            await state.set_state(BankState.waiting_products)
            await show_products_keyboard(callback, state, set_obj.id)
        else:
            await callback.answer("❌ Набор 'Депозиты' не найден")
    finally:
        db.close()


@router.callback_query(F.data.startswith("toggle_product_"), BankState.waiting_products)
async def toggle_product(callback: CallbackQuery, state: FSMContext):
    """Переключение выбора продукта"""
    product_id = int(callback.data.split("_", 2)[2])
    data = await state.get_data()
    selected_products = set(data.get("selected_products", []))
    
    if product_id in selected_products:
        selected_products.remove(product_id)
    else:
        selected_products.add(product_id)
    
    set_id = data.get("selected_set_id")
    await state.update_data(selected_products=list(selected_products))
    await show_products_keyboard(callback, state, set_id)


@router.callback_query(F.data == "back_to_set", BankState.waiting_products)
async def back_to_set(callback: CallbackQuery, state: FSMContext):
    """Возврат к выбору набора"""
    await state.update_data(selected_products=[])
    await callback.message.edit_text(
        "👋 Выберите **набор карт**:",
        parse_mode="Markdown",
        reply_markup=get_sets_keyboard()
    )
    await state.set_state(BankState.waiting_set_selection)
    await callback.answer()


@router.callback_query(F.data == "show_characteristics", BankState.waiting_products)
async def show_characteristics(callback: CallbackQuery, state: FSMContext):
    """Переход к выбору характеристик"""
    data = await state.get_data()
    if not data.get("selected_products"):
        await callback.answer("❌ Выберите хотя бы один продукт!", show_alert=True)
        return
    
    await state.set_state(BankState.waiting_characteristics)
    await show_characteristics_keyboard(callback, state)


@router.callback_query(F.data.startswith("toggle_char_"), BankState.waiting_characteristics)
async def toggle_characteristic(callback: CallbackQuery, state: FSMContext):
    """Переключение выбора характеристики"""
    char_id = int(callback.data.split("_", 2)[2])
    data = await state.get_data()
    selected_chars = set(data.get("selected_characteristics", []))
    
    if char_id in selected_chars:
        selected_chars.remove(char_id)
    else:
        selected_chars.add(char_id)
    
    await state.update_data(selected_characteristics=list(selected_chars))
    await show_characteristics_keyboard(callback, state)


@router.callback_query(F.data == "back_to_products", BankState.waiting_characteristics)
async def back_to_products(callback: CallbackQuery, state: FSMContext):
    """Возврат к выбору продуктов"""
    data = await state.get_data()
    set_id = data.get("selected_set_id")
    await state.set_state(BankState.waiting_products)
    await show_products_keyboard(callback, state, set_id)


@router.callback_query(F.data == "confirm_selection", BankState.waiting_characteristics)
async def confirm_selection(callback: CallbackQuery, state: FSMContext):
    """Показывает подтверждение перед парсингом"""
    data = await state.get_data()
    
    if not data.get("selected_characteristics"):
        await callback.answer("❌ Выберите хотя бы одну характеристику!", show_alert=True)
        return
    
    await show_confirmation(callback, state)
    await callback.answer()


@router.callback_query(F.data == "back_to_characteristics")
async def back_to_characteristics(callback: CallbackQuery, state: FSMContext):
    """Возврат к выбору характеристик из подтверждения"""
    await state.set_state(BankState.waiting_characteristics)
    await show_characteristics_keyboard(callback, state)


def extract_pdf_links(html: str, base_url: str) -> list[str]:
    soup = BeautifulSoup(html, 'html.parser')
    links = set()

    for a in soup.find_all('a', href=True):
        href = a['href'].strip()

        if any(x in href.lower() for x in ['.pdf', 'pdf/', 'documents']):
            if href.startswith('/'):
                href = base_url.rstrip('/') + href
            elif not href.startswith('http'):
                continue

            links.add(href.split('#')[0])

    return list(links)


def download_pdf(url: str, save_dir: str = "./tmp_pdfs") -> str | None:
    os.makedirs(save_dir, exist_ok=True)

    try:
        r = requests.get(
            url,
            timeout=15,
            verify=False,
            headers={"User-Agent": "Mozilla/5.0"}
        )
        if r.status_code != 200:
            return None

        filename = os.path.join(
            save_dir,
            os.path.basename(url.split('?')[0])
        )

        with open(filename, "wb") as f:
            f.write(r.content)

        return filename
    except Exception as e:
        print(f"PDF download error: {e}")
        return None


async def extract_pdf_text(pdf_path: str) -> str:
    try:
        doc = fitz.open(pdf_path)
        text = ''
        for page in doc:
            text += page.get_text()
        doc.close()
        return text.strip()[:80000]  
    except Exception as e:
        print(f"Ошибка PDF {pdf_path}: {e}")
        return ''

async def find_relevant_pdfs(keywords: list = None) -> list[str]:
    if keywords is None:
        keywords = PDF_KEYWORDS
    docs = []
    for file in glob.glob(f"{DOC_DIR}**/*.pdf", recursive=True):
        if any(re.search(re.escape(kw), file.lower(), re.I) for kw in keywords):
            docs.append(file)
    print(f"Найдено PDF: {len(docs)}")
    return docs


def extract_pdf_links_belarusbank(html: str) -> list[str]:
    soup = BeautifulSoup(html, 'html.parser')
    links = set()

    # === 1. Обычные <a> ссылки ===
    for a in soup.find_all('a', href=True):
        href = a['href'].strip()
        if '.pdf' in href.lower():
            if href.startswith('/'):
                href = 'https://belarusbank.by/fizicheskim_licam/kredit/consumer/kredit-1/' + href
            links.add(href)

    # === 2. Баннеры и картинки ===
    for img in soup.find_all(['img', 'source']):
        for attr in ['src', 'data-src', 'srcset', 'data-srcset']:
            val = img.get(attr)
            if val and '.pdf' in val.lower():
                links.add(val.split(' ')[0])

    return list(links)



@router.callback_query(F.data == 'start_parsing')
async def parse_selected_banks_callback(callback: CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    db = SessionLocal()    
    total_tokens_in = 0
    total_tokens_out = 0


    try:
        # Логирование
        log = Log(user_id=user_id, action='parse', status='new', created_at=datetime.utcnow())
        db.add(log)
        db.commit()
        log.status = 'process'
        db.commit()


        # Данные из состояния
        data = await state.get_data()
        selectedproducts = data.get('selected_products')
        selectedchars = data.get('selected_characteristics')

        # Получаем названия выбранных характеристик
        selected_char_names = []
        if selectedchars:
            charobjects = db.query(Characteristic).filter(Characteristic.id.in_(selectedchars)).all()
            selected_char_names = [c.name for c in charobjects]
        print(f"DEBUG selectedcharnames: {selected_char_names}")

        # Проверяем продукты
        if not selectedproducts:
            await callback.message.edit_text("❌ Нет выбранных продуктов!")
            db.close()
            return

        selected_product_data = db.query(Product).filter(Product.id.in_(selectedproducts)).all()
        selectedproductnames = [p.name for p in selected_product_data]
        
        bank_ids = [p.bank_id for p in selected_product_data]
        banks = db.query(Bank).filter(Bank.id.in_(bank_ids)).all()
        all_banks = [b.name for b in banks]

        if not all_banks:
            await callback.message.edit_text("❌ Нет банков!")
            db.close()
            return

        giga = GigaChat(
            credentials=GIGACHAT_TOKEN,
            scope="GIGACHAT_API_B2B",
            verify_ssl_certs=False,
            model="GigaChat-2-Max"
        )


        display_char_names = [FIELD_NAMES.get(name, name) for name in selected_char_names]
        await callback.message.edit_text(
            f"🔄 Парсинг...\n"
            f"Продукты: {', '.join(selectedproductnames)}\n"
            f"Характеристики: {', '.join(display_char_names) if display_char_names else ''}\n"
            f"Банки: {', '.join(all_banks)}"
        )

        results = []
        total = len(selected_product_data)

        for i, product in enumerate(selected_product_data, 1):
            bank = db.query(Bank).get(product.bank_id)
            if not bank:
                print(f"-! Нет банка для {product.name}")
                results.append(_empty_schema('Unknown', product.name))
                continue

            url = product.url
            progress = int(i / total * 10)
            bar = '█' * progress + '░' * (10 - progress)
            
            try:
                await callback.message.edit_text(
                    f"🔄 {bank.name} | {product.name} ({i}/{total})\n{bar}"
                )

                # Парсинг веб-страницы
                page_content = await get_page_content(url)
                if not page_content or len(page_content) < 500:
                    print(f"-! Ничего не найдено: {bank.name} {product.name}")
                    results.append(_empty_schema(bank.name, product.name))
                    continue

                if bank.name.lower() == "беларусбанк":
                    pdf_links = extract_pdf_links_belarusbank(page_content)
                else:    
                    pdf_links = extract_pdf_links(page_content, bank.url if hasattr(bank, 'url') else url)
                    print(f"Найдено PDF ссылок: {pdf_links}")

                pdf_texts = []
                pdf_files_for_excel = []  

                for pdf_url in pdf_links[:3]:

                    pdf_file = download_pdf(pdf_url)
                    if not pdf_file:
                        continue

                    pdf_files_for_excel.append(pdf_url) 

                    pdf_text = await extract_pdf_text(pdf_file)
                    if pdf_text:
                        pdf_texts.append(
                            f"PDF ({os.path.basename(pdf_file)}):\n{pdf_text}"
                        )

                    try:
                        os.remove(pdf_file)
                    except:
                        pass

                pdf_content = "\n\n---\n\n".join(pdf_texts)

                # Очистка HTML
                print(f"+ {bank.name} {product.name} HTML: {len(page_content)}")
                soup = BeautifulSoup(page_content, 'html.parser')
                for tag in soup(['script', 'style', 'iframe']):
                    tag.decompose()

                # Поиск важных блоков
                for table in soup.find_all('table'):
                    table['data-critical'] = 'table'
                for li in soup.find_all('li'):
                    if any(word in li.get_text().lower() 
                          for word in ['byn', 'бел', 'руб', '%', 'ставка', 'срок']):
                        li['data-critical'] = 'important'

                for p in soup.find_all('p'):
                    if any(word in p.get_text().lower() 
                          for word in ['byn', 'бел', 'руб', '%', 'ставка', 'срок']):
                        p['detail-banner__prop'] = 'p'

                cleaned_html = str(soup)
                if len(cleaned_html) > 80000:
                    if bank.name == 'Беларусбанк':
                        cleaned_html = cleaned_html[:120000]
                    cleaned_html = cleaned_html[:80000]

                
                if len(cleaned_html) < 300:
                    print(f"-! HTML слишком короткий")
                    results.append(_empty_schema(bank.name, product.name))
                    continue

                pdf_texts = []

                for pdf_url in pdf_links[:3]:  
                    pdf_file = download_pdf(pdf_url)
                    if not pdf_file:
                        continue

                    pdf_text = await extract_pdf_text(pdf_file)
                    if pdf_text:
                        pdf_texts.append(
                            f"PDF ({os.path.basename(pdf_file)}):\n{pdf_text}"
                        )

                    try:
                        os.remove(pdf_file)
                    except:
                        pass

                pdf_content = "\n\n---\n\n".join(pdf_texts)

                prompt = f"""
        Ты ИНФОРМАЦИОННЫЙ ПАРСЕР банковских продуктов.
        Ты НЕ рассуждаешь и НЕ объясняешь.

        Верни ТОЛЬКО JSON:
        {{
        "name": null,
        "rate": null,
        "rate_type": null,
        "sum": null,
        "term": null,
        "payment_type": null,
        "commission": null,
        "early_repayment": null,
        "insurance": null,
        "currency": null,
        "additional": null
        }}

        ПРАВИЛА:
        - Если поле не найдено — null
        - Не добавляй новые поля
        - Не пиши текст вне JSON

        HTML страницы продукта ({bank.name}):
        {cleaned_html}

        ТЕКСТ ИЗ PDF:
        ВАЖНО: если условия (ставка, сумма, срок, комиссии) отсутствуют или неполные в HTML,
        ОБЯЗАТЕЛЬНО используй PDF.

        PDF:
        {pdf_content}

        JSON:
        """

                        
                result = giga.chat(prompt)
                raw_response = result.choices[0].message.content

                tokens_in = len(prompt) // 4
                tokens_out = len(raw_response) // 4
                total_tokens_in += tokens_in
                total_tokens_out += tokens_out

                print(f"{bank.name} RAW: {repr(raw_response[:150])}")

                parsed_data = _parse_json_safely(raw_response)
                if parsed_data:
                    parsed_data = normalize_ranges(parsed_data)

                if not parsed_data:
                    print(f"!!! {bank.name} Не удалось распарсить JSON")
                    
                    # Fallback промпт только по тексту
                    textcontent = soup.get_text(separator=' ', strip=True)[:70000]
                    prompt_fallback = f"""
                        Извлеки значения и верни JSON:

                        {{
                        "name": null,
                        "rate": null,
                        "rate_type": null,
                        "sum": null,
                        "term": null,
                        "payment_type": null,
                        "commission": null,
                        "early_repayment": null,
                        "insurance": null,
                        "currency": null,
                        "additional": null
                        }}

                        Текст:
                        {textcontent}
                        """
                    try:
                        resultfallback = giga.chat(prompt_fallback)
                        raw_response_fallback = resultfallback.choices[0].message.content

                        tokens_in_fb = len(prompt_fallback) // 4
                        tokens_out_fb = len(raw_response_fallback) // 4
                        total_tokens_in += tokens_in_fb
                        total_tokens_out += tokens_out_fb
                        parsed_data = _parse_json_safely(raw_response_fallback)
                        if parsed_data and any(v for v in parsed_data.values() if v and v != 'null'):
                            print(f"✓ Fallback сработал для {bank.name}")
                    except Exception as e:
                        print(f"Fallback ошибка {bank.name}: {e}")
                    
                    if not parsed_data:
                        results.append(_empty_schema(bank.name, product.name))
                        continue

                # Проверяем наличие данных
                hasdata = any(v for v in parsed_data.values() if v and v != 'null')
                if not hasdata:
                    print(f"!!!!! {bank.name} Все поля null")
                    results.append(_empty_schema(bank.name, product.name))
                    continue

                # Добавляем метаданные
                parsed_data['bank'] = bank.name
                parsed_data['product'] = product.name
                parsed_data['files'] = ", ".join(pdf_files_for_excel) if pdf_files_for_excel else None
                print(f"{bank.name} ✓: {parsed_data.get('name', 'N/A')}")
                results.append(parsed_data)

                await asyncio.sleep(1.0) 

            except Exception as e:
                print(f"{bank.name} ERROR: {str(e)}")
                results.append(_empty_schema(bank.name, product.name))

        # === СОХРАНЕНИЕ РЕЗУЛЬТАТОВ ===
        characteristics = ', '.join(selected_char_names) if selected_char_names else ''
        datarow = Data(
            user_id=user_id, 
            characteristics=characteristics, 
            card_set=', '.join(selectedproductnames), 
            payload=results
        )
        db.add(datarow)
        db.commit()

        # Создание Excel отчёта
        excelpath = await asyncio.to_thread(
            create_bank_excel_report, 
            results, 
            "./reports", 
            selected_char_names if selected_char_names else None,
            pdf_path="/path/to/document.pdf"
        )
        
        

        
        file = FSInputFile(excelpath)
        await callback.message.answer_document(
            file, 
            caption=f"✅ Парсинг завершён!\n"
                   f"Продукты: {', '.join(selectedproductnames)}\n"
                   f"Банки: {', '.join(all_banks)}\n"
                   f"PDF использовано: {len(pdf_links)} шт.\n"
                   #f"Токенов затрачено: {len(response)}"
        )
        os.unlink(excelpath)

        await callback.message.edit_text("✅ Excel отчёт отправлен!")
        log.token = total_tokens_in + total_tokens_out
        log.status = 'ok'
        db.commit()

    except Exception as e:
        log.status = 'error'
        log.message = str(e)
        db.commit()
        await callback.message.edit_text(f"❌ Ошибка парсинга: {str(e)}")
        print(f"КРИТИЧЕСКАЯ ОШИБКА: {str(e)}")
        
    finally:
        db.close()
        await state.clear()



def _parse_json_safely(raw_response: str) -> dict | None:
    if not raw_response:
        return None
    
    json_str = re.sub(r'```json\n?|```', '', raw_response).strip()
    
    strategies = [
        lambda s: s,  # Как есть
        lambda s: s[:s.rfind('}')+1] if '}' in s else s,  
        lambda s: re.sub(r',\s*$', '', s),  
    ]
    
    for strategy in strategies:
        try:
            cleaned = strategy(json_str)
            parsed = json.loads(cleaned)
            if 'summ' in parsed:
                parsed['sum'] = parsed.pop('summ')
            return parsed
        except:
            continue
    
    return None

def normalize_ranges(data: dict) -> dict:
    for key, value in data.items():
        if isinstance(value, dict) and 'min' in value and 'max' in value:
            min_v = value.get('min')
            max_v = value.get('max')

            if min_v and max_v:
                data[key] = f"{min_v} – {max_v}"
            else:
                data[key] = min_v or max_v

    return data


def _empty_schema(bank_name: str, product_name: str) -> dict:
    return {
        "name": None,
        "rate": None,
        "rate_type": None,
        "sum": None,
        "term": None,
        "payment_type": None,
        "commission": None,
        "early_repayment": None,
        "insurance": None,
        "currency": None,
        "additional": None,
        "files": None,
        "bank": bank_name,
        "product": product_name,
    }

