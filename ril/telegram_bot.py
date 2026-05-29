import re
import logging
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

from ril.config import TELEGRAM_TOKEN, ALLOWED_TELEGRAM_USERS
from ril import db, core

# Logger setup
logger = logging.getLogger("ril-telegram")

# Helper decorator or check for authorized users
def check_user(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not update.effective_user:
            return
        user_id = update.effective_user.id
        if ALLOWED_TELEGRAM_USERS and user_id not in ALLOWED_TELEGRAM_USERS:
            logger.warning(f"Unauthorized access attempt by User ID {user_id} (@{update.effective_user.username})")
            await update.message.reply_text("❌ У вас нет доступа к этому боту.")
            return
        return await func(update, context)
    return wrapper

@check_user
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler for the /start command."""
    welcome_text = (
        "👋 Привет! Я твой бот Read It Later (RIL).\n\n"
        "Отправь мне любую ссылку, и я:\n"
        "1. Загружу страницу в фоновом режиме (Playwright)\n"
        "2. Очищу её от рекламы и меню (Readability)\n"
        "3. Скачаю все картинки локально\n"
        "4. Преобразую в красивый Markdown\n"
        "5. Запишу в SQLite для поиска по ключевым словам\n\n"
        "Commands:\n"
        "📊 /stats — Показать статистику чтения\n"
        "🔍 /search <запрос> — Поиск по статьям\n"
        "📋 /list — Показать последние 10 статей"
    )
    await update.message.reply_text(welcome_text)

@check_user
async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler for the /stats command."""
    try:
        stats_data = db.get_stats()
        total = stats_data['total_articles']
        if total == 0:
            await update.message.reply_text("📭 Ваша библиотека пока пуста.")
            return
            
        progress = round((stats_data['read_articles'] / total) * 100, 1)
        unread_mins = round(stats_data['unread_words'] / 200)
        
        msg = (
            f"📊 Статистика библиотеки RIL:\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📁 Всего статей: {total}\n"
            f"  📥 Не прочитано: {stats_data['unread_articles']}\n"
            f"  ✅ Прочитано: {stats_data['read_articles']} ({progress}%)\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📝 Всего слов сохранено: {stats_data['total_words']:,}\n"
            f"⏱️ Осталось читать: ~{unread_mins} минут(ы)\n"
            f"📐 Средний размер статьи: {stats_data['avg_words_per_article']:.0f} слов"
        )
        await update.message.reply_text(msg)
    except Exception as e:
        logger.error(f"Error in stats command: {e}")
        await update.message.reply_text("❌ Произошла ошибка при получении статистики.")

@check_user
async def list_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler to list the 10 most recent articles."""
    try:
        articles = db.list_articles(limit=10)
        if not articles:
            await update.message.reply_text("📭 В библиотеке нет статей.")
            return
            
        msg_lines = ["📋 Последние 10 статей:"]
        for a in articles:
            status_icon = "✅" if a['status'] == 'read' else "📥"
            msg_lines.append(
                f"{status_icon} ID {a['id']}: *{a['title']}*\n"
                f"   _Слов: {a['word_count']} | Добавлено: {a['added_at'][:10]}_\n"
            )
        await update.message.reply_text("\n".join(msg_lines), parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Error in list command: {e}")
        await update.message.reply_text("❌ Ошибка при выводе списка статей.")

@check_user
async def search_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler to search articles via SQLite FTS5."""
    # Context.args holds space-separated arguments
    if not context.args:
        await update.message.reply_text("💡 Пожалуйста, укажите поисковый запрос. Пример: `/search квантовые`", parse_mode="Markdown")
        return
        
    query = " ".join(context.args)
    try:
        results = db.search_articles(query, limit=5)
        if not results:
            await update.message.reply_text(f"🔍 По запросу '{query}' ничего не найдено.")
            return
            
        msg_lines = [f"🔍 Результаты поиска для '{query}':\n"]
        for r in results:
            status_icon = "✅" if r['status'] == 'read' else "📥"
            msg_lines.append(
                f"{status_icon} *ID {r['id']}*: {r['title']}\n"
                f"📝 _{r['snippet']}_\n"
            )
        await update.message.reply_text("\n".join(msg_lines), parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Error in search command: {e}")
        await update.message.reply_text("❌ Ошибка при поиске.")

@check_user
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle normal messages and extract links."""
    text = update.message.text
    if not text:
        return
        
    # Simple regex to extract URL(s)
    urls = re.findall(r'(https?://\S+)', text)
    if not urls:
        await update.message.reply_text("ℹ️ Пришлите мне ссылку, чтобы сохранить статью в архив.")
        return
        
    for url in urls:
        status_msg = await update.message.reply_text(f"⏳ Начинаю импорт: {url}...")
        try:
            # Process URL through core pipeline
            res = await core.process_url(url)
            
            # Form response
            title = res['title']
            word_count = res['word_count']
            
            response_text = (
                f"📥 *Сохранил!*\n\n"
                f"В статье *{title}* {word_count} слов, картинки на месте.\n\n"
                f"📂 ID статьи: `{res['id']}`\n"
                f"📍 Путь: `{res['file_path']}`"
            )
            await status_msg.edit_text(response_text, parse_mode="Markdown")
            
        except Exception as e:
            logger.error(f"Error importing {url}: {e}", exc_info=True)
            await status_msg.edit_text(
                f"❌ Ошибка при импорте ссылки: {url}\n"
                f"Детали: `{str(e)}`",
                parse_mode="Markdown"
            )

def run_bot():
    """Start the Telegram Bot."""
    if not TELEGRAM_TOKEN:
        print("Error: TELEGRAM_TOKEN environment variable is not set. Cannot start bot.")
        return
        
    print("Setting up Telegram Bot...")
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    
    # Register handlers
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("stats", stats_command))
    app.add_handler(CommandHandler("list", list_command))
    app.add_handler(CommandHandler("search", search_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    print("Telegram bot started successfully. Press Ctrl+C to terminate.")
    app.run_polling()
