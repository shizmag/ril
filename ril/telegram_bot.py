import re
import logging
import os
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes

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

async def safe_delete_message(context: ContextTypes.DEFAULT_TYPE, chat_id: int, message_id: int):
    """Safely delete a message without raising exceptions if it doesn't exist."""
    try:
        await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception:
        pass

def make_progress_bar(pct: float) -> str:
    """Generate a clean visual progress bar."""
    total_blocks = 10
    filled = int(round((pct / 100.0) * total_blocks))
    empty = total_blocks - filled
    return "█" * filled + "░" * empty

@check_user
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler for the /start command."""
    try:
        await update.message.delete()
    except Exception:
        pass

    welcome_text = (
        "👋 *Привет! Я твой бот Read It Later (RIL).*\n\n"
        "Пришли мне любую ссылку, я очищу её от лишней рекламы, сохраню локально с картинками и отправлю тебе .md файл с интерактивным меню.\n\n"
        "ℹ️ *Доступные команды:*\n"
        "📊 /stats — Показать статистику библиотеки\n"
        "📋 /list — Открыть интерактивный список статей\n"
        "🔍 /search <запрос> — Поиск по архиву\n"
        "📥 /get <ID> — Получить файл по ID статьи\n"
        "🗑️ /delete <ID> — Быстрое удаление по ID\n"
        "⚠️ /reset — Очистить библиотеку"
    )
    await update.message.reply_text(welcome_text, parse_mode="Markdown")

@check_user
async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler for the /stats command."""
    try:
        await update.message.delete()
    except Exception:
        pass

    prev_msg_id = context.user_data.get("last_stats_msg_id")
    if prev_msg_id:
        await safe_delete_message(context, update.effective_chat.id, prev_msg_id)

    try:
        stats_data = db.get_stats()
        total = stats_data['total_articles']
        if total == 0:
            sent_msg = await update.message.reply_text("📭 *Ваша библиотека пока пуста.*", parse_mode="Markdown")
            context.user_data["last_stats_msg_id"] = sent_msg.message_id
            return
            
        progress = round((stats_data['read_articles'] / total) * 100, 1)
        unread_mins = round(stats_data['unread_words'] / 200)
        bar = make_progress_bar(progress)
        
        msg = (
            f"📊 *Read It Later — Статистика*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📁 Всего статей: *{total}*\n"
            f"  📥 Не прочитано: *{stats_data['unread_articles']}*\n"
            f"  ✅ Прочитано: *{stats_data['read_articles']}*\n\n"
            f"📈 Прогресс чтения:\n"
            f"`{bar}`  *{progress}%*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📝 Всего слов сохранено: *{stats_data['total_words']:,}*\n"
            f"⏱️ Осталось читать: *~{unread_mins} мин*\n"
            f"📐 Средний размер: *{stats_data['avg_words_per_article']:.0f} слов*"
        )
        sent_msg = await update.message.reply_text(msg, parse_mode="Markdown")
        context.user_data["last_stats_msg_id"] = sent_msg.message_id
    except Exception as e:
        logger.error(f"Error in stats command: {e}")
        await update.message.reply_text("❌ Произошла ошибка при получении статистики.")

@check_user
async def list_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler to list the 10 most recent articles."""
    try:
        await update.message.delete()
    except Exception:
        pass
    await show_articles_list(update, context, edit=False)

async def show_articles_list(update: Update, context: ContextTypes.DEFAULT_TYPE, edit: bool = False):
    """Render the list of recent articles with inline buttons."""
    try:
        articles = db.list_articles(limit=10)
        if not articles:
            text = "📭 *В вашей библиотеке пока нет статей.*"
            keyboard = None
        else:
            msg_lines = ["📋 *Последние сохранённые статьи:*\n"]
            keyboard_buttons = []
            row = []
            for idx, a in enumerate(articles, 1):
                status_icon = "✅" if a['status'] == 'read' else "📥"
                unread_mins = max(1, round(a['word_count'] / 200))
                msg_lines.append(
                    f"*{idx}.* {status_icon} *{a['title']}*\n"
                    f"   _ID: {a['id']} | Слов: {a['word_count']} (~{unread_mins} мин)_\n"
                )
                row.append(InlineKeyboardButton(text=f"📄 {a['id']}", callback_data=f"art:{a['id']}"))
                if len(row) == 5:
                    keyboard_buttons.append(row)
                    row = []
            if row:
                keyboard_buttons.append(row)
                
            text = "\n".join(msg_lines)
            text += "\n💡 _Выберите ID статьи на кнопках ниже для управления или просмотра её деталей._"
            keyboard = InlineKeyboardMarkup(keyboard_buttons)
            
        if edit:
            query = update.callback_query
            await query.edit_message_text(text, reply_markup=keyboard, parse_mode="Markdown")
        else:
            prev_msg_id = context.user_data.get("last_list_msg_id")
            if prev_msg_id:
                await safe_delete_message(context, update.effective_chat.id, prev_msg_id)
            sent_msg = await update.message.reply_text(text, reply_markup=keyboard, parse_mode="Markdown")
            context.user_data["last_list_msg_id"] = sent_msg.message_id
    except Exception as e:
        logger.error(f"Error in show_articles_list: {e}")
        if edit:
            await update.callback_query.edit_message_text("❌ Ошибка при выводе списка статей.")
        else:
            await update.message.reply_text("❌ Ошибка при выводе списка статей.")

async def show_article_details(update: Update, context: ContextTypes.DEFAULT_TYPE, article_id: int):
    """Render details and actions for a specific article."""
    query = update.callback_query
    article = db.get_article(article_id)
    if not article:
        await query.answer("❌ Статья не найдена.", show_alert=True)
        await show_articles_list(update, context, edit=True)
        return
        
    status_text = "✅ Прочитано" if article['status'] == 'read' else "📥 Не прочитано"
    unread_mins = max(1, round(article['word_count'] / 200))
    
    text = (
        f"📖 *Детали статьи ID {article_id}:*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📌 *{article['title']}*\n\n"
        f"📊 *Статус:* {status_text}\n"
        f"📝 *Объем:* {article['word_count']} слов (~{unread_mins} мин)\n"
        f"🕒 *Сохранено:* {article['added_at'][:16].replace('T', ' ')}\n"
        f"🔗 [Открыть оригинал]({article['url']})\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━"
    )
    
    toggle_label = "📥 В нечитаемые" if article['status'] == 'read' else "✅ Прочитано"
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("💾 Получить MD", callback_data=f"get:{article['id']}"),
            InlineKeyboardButton(toggle_label, callback_data=f"toggle:{article['id']}")
        ],
        [
            InlineKeyboardButton("🗑️ Удалить статью", callback_data=f"del_confirm:{article['id']}"),
            InlineKeyboardButton("🔙 Назад к списку", callback_data="list")
        ]
    ])
    
    await query.edit_message_text(text, reply_markup=keyboard, parse_mode="Markdown")

async def show_delete_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE, article_id: int):
    """Confirm deletion of an article."""
    query = update.callback_query
    article = db.get_article(article_id)
    if not article:
        await query.answer("❌ Статья не найдена.", show_alert=True)
        await show_articles_list(update, context, edit=True)
        return
        
    text = (
        f"⚠️ *Удаление статьи ID {article_id}*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"Вы действительно хотите удалить статью:\n"
        f"*\"{article['title']}\"*?\n\n"
        f"Это действие удалит запись из базы данных, её markdown-файл и все её загруженные изображения."
    )
    
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🗑️ Да, удалить навсегда", callback_data=f"del_exe:{article_id}"),
            InlineKeyboardButton("❌ Отмена", callback_data=f"art:{article_id}")
        ]
    ])
    
    await query.edit_message_text(text, reply_markup=keyboard, parse_mode="Markdown")

@check_user
async def search_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler to search articles via SQLite FTS5."""
    if not context.args:
        try:
            await update.message.delete()
        except Exception:
            pass
        await update.message.reply_text("💡 Пожалуйста, укажите поисковый запрос. Пример: `/search квантовые`", parse_mode="Markdown")
        return
        
    query = " ".join(context.args)
    try:
        await update.message.delete()
    except Exception:
        pass

    prev_msg_id = context.user_data.get("last_search_msg_id")
    if prev_msg_id:
        await safe_delete_message(context, update.effective_chat.id, prev_msg_id)

    try:
        results = db.search_articles(query, limit=5)
        if not results:
            sent_msg = await update.message.reply_text(f"🔍 По запросу *'{query}'* ничего не найдено.", parse_mode="Markdown")
            context.user_data["last_search_msg_id"] = sent_msg.message_id
            return
            
        msg_lines = [f"🔍 *Результаты поиска для '{query}':*\n"]
        keyboard_buttons = []
        for r in results:
            status_icon = "✅" if r['status'] == 'read' else "📥"
            msg_lines.append(
                f"{status_icon} *ID {r['id']}*: {r['title']}\n"
                f"📝 _{r['snippet']}_\n"
            )
            keyboard_buttons.append([InlineKeyboardButton(text=f"📖 Открыть ID {r['id']}", callback_data=f"art:{r['id']}")])
            
        sent_msg = await update.message.reply_text(
            "\n".join(msg_lines), 
            reply_markup=InlineKeyboardMarkup(keyboard_buttons), 
            parse_mode="Markdown"
        )
        context.user_data["last_search_msg_id"] = sent_msg.message_id
    except Exception as e:
        logger.error(f"Error in search command: {e}")
        await update.message.reply_text("❌ Ошибка при поиске.")

@check_user
async def get_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Retrieve and send the markdown file of an article by its ID."""
    try:
        await update.message.delete()
    except Exception:
        pass

    if not context.args:
        await update.message.reply_text("💡 Пожалуйста, укажите ID статьи. Пример: `/get 5`", parse_mode="Markdown")
        return
        
    try:
        article_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ ID статьи должен быть числом. Пример: `/get 5`")
        return
        
    try:
        article = db.get_article(article_id)
        if not article:
            await update.message.reply_text(f"❌ Статья с ID {article_id} не найдена.")
            return
            
        file_path = article['file_path']
        if os.path.exists(file_path):
            title = article['title']
            word_count = article['word_count']
            status_icon = "✅" if article['status'] == 'read' else "📥"
            caption = (
                f"{status_icon} *{title}*\n"
                f"📂 ID статьи: `{article_id}` | Слов: {word_count}\n"
                f"🔗 {article['url']}"
            )
            toggle_text = "📥 Не прочитано" if article['status'] == 'read' else "✅ Прочитано"
            keyboard = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(toggle_text, callback_data=f"toggle_doc:{article_id}"),
                    InlineKeyboardButton("🗑️ Удалить", callback_data=f"del_doc:{article_id}")
                ]
            ])
            with open(file_path, 'rb') as doc_file:
                await update.message.reply_document(
                    document=doc_file,
                    filename=os.path.basename(file_path),
                    caption=caption,
                    reply_markup=keyboard,
                    parse_mode="Markdown"
                )
        else:
            await update.message.reply_text(f"❌ Файл статьи не найден на диске.", parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Error fetching article {article_id}: {e}")
        await update.message.reply_text(f"❌ Произошла ошибка при получении статьи.")

@check_user
async def delete_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Delete an article by ID."""
    try:
        await update.message.delete()
    except Exception:
        pass

    if not context.args:
        await update.message.reply_text("💡 Пожалуйста, укажите ID статьи. Пример: `/delete 5`", parse_mode="Markdown")
        return
        
    try:
        article_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ ID статьи должен быть числом. Пример: `/delete 5`")
        return
        
    try:
        article = db.get_article(article_id)
        if not article:
            await update.message.reply_text(f"❌ Статья с ID {article_id} не найдена.")
            return
            
        success = core.delete_article(article_id)
        if success:
            await update.message.reply_text(f"🗑️ Статья *\"{article['title']}\"* (ID: {article_id}) успешно удалена.", parse_mode="Markdown")
        else:
            await update.message.reply_text(f"❌ Не удалось удалить статью с ID {article_id}.")
    except Exception as e:
        logger.error(f"Error deleting article {article_id}: {e}")
        await update.message.reply_text(f"❌ Произошла ошибка при удалении статьи.")

@check_user
async def reset_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler for the /reset command."""
    try:
        await update.message.delete()
    except Exception:
        pass

    await update.message.reply_text(
        "⚠️ *ВНИМАНИЕ!* Вы собираетесь удалить ВСЕ статьи и очистить базу данных.\n"
        "Это действие необратимо.\n\n"
        "Для подтверждения отправьте команду: `/reset_confirm`",
        parse_mode="Markdown"
    )

@check_user
async def reset_confirm_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler for /reset_confirm to perform the reset."""
    try:
        await update.message.delete()
    except Exception:
        pass

    try:
        core.reset_library()
        await update.message.reply_text("✅ Библиотека и база данных успешно очищены!")
    except Exception as e:
        logger.error(f"Error resetting library: {e}")
        await update.message.reply_text(f"❌ Ошибка при очистке библиотеки.")

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
            file_path = res['file_path']
            article_id = res['id']
            
            response_text = (
                f"📥 *Сохранил!*\n\n"
                f"*{title}*\n"
                f"Слов: {word_count}\n"
                f"ID статьи: `{article_id}`\n"
                f"🔗 {url}"
            )
            
            # Delete temporary status message
            await safe_delete_message(context, update.effective_chat.id, status_msg.message_id)
            
            # Action buttons for document message
            keyboard = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("✅ Прочитано", callback_data=f"toggle_doc:{article_id}"),
                    InlineKeyboardButton("🗑️ Удалить", callback_data=f"del_doc:{article_id}")
                ]
            ])
            
            # Send file as document
            if os.path.exists(file_path):
                with open(file_path, 'rb') as doc_file:
                    await update.message.reply_document(
                        document=doc_file,
                        filename=os.path.basename(file_path),
                        caption=response_text,
                        reply_markup=keyboard,
                        parse_mode="Markdown"
                    )
            else:
                await update.message.reply_text(
                    f"⚠️ Файл не найден на диске, но сохранен в базе:\n{response_text}", 
                    parse_mode="Markdown"
                )
            
            # Delete user's incoming message to keep the chat clean
            try:
                await update.message.delete()
            except Exception:
                pass
                
        except Exception as e:
            logger.error(f"Error importing {url}: {e}", exc_info=True)
            # Delete status message
            await safe_delete_message(context, update.effective_chat.id, status_msg.message_id)
            await update.message.reply_text(
                f"❌ Ошибка при импорте ссылки: {url}\n"
                f"Детали: `{str(e)}`",
                parse_mode="Markdown"
            )

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle callback queries from inline buttons."""
    query = update.callback_query
    user_id = query.from_user.id
    
    if ALLOWED_TELEGRAM_USERS and user_id not in ALLOWED_TELEGRAM_USERS:
        await query.answer("❌ У вас нет доступа к этому боту.", show_alert=True)
        return
        
    data = query.data
    
    if data == "list":
        await query.answer()
        await show_articles_list(update, context, edit=True)
        
    elif data.startswith("art:"):
        await query.answer()
        art_id = int(data.split(":")[1])
        await show_article_details(update, context, art_id)
        
    elif data.startswith("get:"):
        art_id = int(data.split(":")[1])
        article = db.get_article(art_id)
        if article:
            file_path = article['file_path']
            if os.path.exists(file_path):
                await query.answer("📥 Отправляю файл...")
                title = article['title']
                word_count = article['word_count']
                status_icon = "✅" if article['status'] == 'read' else "📥"
                caption = (
                    f"{status_icon} *{title}*\n"
                    f"📂 ID статьи: `{art_id}` | Слов: {word_count}\n"
                    f"🔗 {article['url']}"
                )
                toggle_text = "📥 Не прочитано" if article['status'] == 'read' else "✅ Прочитано"
                keyboard = InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton(toggle_text, callback_data=f"toggle_doc:{art_id}"),
                        InlineKeyboardButton("🗑️ Удалить", callback_data=f"del_doc:{art_id}")
                    ]
                ])
                with open(file_path, 'rb') as doc_file:
                    await context.bot.send_document(
                        chat_id=update.effective_chat.id,
                        document=doc_file,
                        filename=os.path.basename(file_path),
                        caption=caption,
                        reply_markup=keyboard,
                        parse_mode="Markdown"
                    )
            else:
                await query.answer("❌ Файл не найден на диске.", show_alert=True)
        else:
            await query.answer("❌ Статья не найдена.", show_alert=True)
            
    elif data.startswith("toggle:"):
        art_id = int(data.split(":")[1])
        article = db.get_article(art_id)
        if article:
            new_status = 'unread' if article['status'] == 'read' else 'read'
            db.mark_as_read(art_id, new_status)
            await query.answer(f"Статус изменен на: {'Прочитано' if new_status == 'read' else 'Не прочитано'}")
            await show_article_details(update, context, art_id)
        else:
            await query.answer("❌ Статья не найдена.", show_alert=True)
            
    elif data.startswith("toggle_doc:"):
        art_id = int(data.split(":")[1])
        article = db.get_article(art_id)
        if article:
            new_status = 'unread' if article['status'] == 'read' else 'read'
            db.mark_as_read(art_id, new_status)
            await query.answer(f"Статус: {'Прочитано' if new_status == 'read' else 'Не прочитано'}")
            
            # Update caption of the document
            status_icon = "✅" if new_status == 'read' else "📥"
            caption = (
                f"{status_icon} *{article['title']}*\n"
                f"📂 ID статьи: `{art_id}` | Слов: {article['word_count']}\n"
                f"🔗 {article['url']}"
            )
            toggle_text = "📥 Не прочитано" if new_status == 'read' else "✅ Прочитано"
            keyboard = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(toggle_text, callback_data=f"toggle_doc:{art_id}"),
                    InlineKeyboardButton("🗑️ Удалить", callback_data=f"del_doc:{art_id}")
                ]
            ])
            try:
                await query.edit_message_caption(caption=caption, reply_markup=keyboard, parse_mode="Markdown")
            except Exception as e:
                logger.error(f"Error editing caption: {e}")
        else:
            await query.answer("❌ Статья не найдена.", show_alert=True)
            
    elif data.startswith("del_confirm:"):
        await query.answer()
        art_id = int(data.split(":")[1])
        await show_delete_confirmation(update, context, art_id)
        
    elif data.startswith("del_exe:"):
        art_id = int(data.split(":")[1])
        article = db.get_article(art_id)
        success = core.delete_article(art_id)
        if success:
            title = article['title'] if article else f"ID {art_id}"
            await query.answer(f"🗑️ Удалено: {title}", show_alert=False)
            await show_articles_list(update, context, edit=True)
        else:
            await query.answer("❌ Не удалось удалить статью.", show_alert=True)
            
    elif data.startswith("del_doc:"):
        art_id = int(data.split(":")[1])
        article = db.get_article(art_id)
        success = core.delete_article(art_id)
        if success:
            title = article['title'] if article else f"ID {art_id}"
            await query.answer(f"🗑️ Удалено: {title}")
            try:
                await query.delete_message()
            except Exception:
                pass
        else:
            await query.answer("❌ Не удалось удалить.", show_alert=True)

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
    app.add_handler(CommandHandler("get", get_command))
    app.add_handler(CommandHandler("read", get_command))
    app.add_handler(CommandHandler("delete", delete_command))
    app.add_handler(CommandHandler("reset", reset_command))
    app.add_handler(CommandHandler("reset_confirm", reset_confirm_command))
    
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    print("Telegram bot started successfully. Press Ctrl+C to terminate.")
    app.run_polling()
