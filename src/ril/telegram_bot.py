import re
import logging
import os
import asyncio
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

async def delayed_delete(context: ContextTypes.DEFAULT_TYPE, chat_id: int, message_id: int, delay_seconds: int = 10):
    """Wait for some seconds and safely delete the message."""
    await asyncio.sleep(delay_seconds)
    await safe_delete_message(context, chat_id, message_id)

def make_progress_bar(pct: float) -> str:
    """Generate a clean visual progress bar."""
    total_blocks = 10
    filled = int(round((pct / 100.0) * total_blocks))
    empty = total_blocks - filled
    return "█" * filled + "░" * empty

def get_document_keyboard(art_id: int, status: str) -> InlineKeyboardMarkup:
    toggle_text = "📥 Не прочитано" if status == 'read' else "✅ Прочитано"
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(toggle_text, callback_data=f"toggle_doc:{art_id}"),
            InlineKeyboardButton("🗑️ Удалить", callback_data=f"del_doc:{art_id}")
        ],
        [
            InlineKeyboardButton("⭐ 1", callback_data=f"rate_doc:{art_id}:1"),
            InlineKeyboardButton("⭐ 2", callback_data=f"rate_doc:{art_id}:2"),
            InlineKeyboardButton("⭐ 3", callback_data=f"rate_doc:{art_id}:3"),
            InlineKeyboardButton("⭐ 4", callback_data=f"rate_doc:{art_id}:4"),
            InlineKeyboardButton("⭐ 5", callback_data=f"rate_doc:{art_id}:5"),
        ]
    ])

@check_user
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler for the /start command."""
    try:
        await update.message.delete()
    except Exception:
        pass

    prev_msg_id = context.user_data.get("last_start_msg_id")
    if prev_msg_id:
        await safe_delete_message(context, update.effective_chat.id, prev_msg_id)

    welcome_text = (
        "👋 *Привет! Я твой бот Read It Later (RIL).*\n\n"
        "Пришли мне любую ссылку, я очищу её от лишней рекламы, сохраню локально с картинками и отправлю тебе файл статьи.\n\n"
        "ℹ️ *Доступные команды:*\n"
        "📊 /stats — Показать статистику библиотеки\n"
        "📋 /list — Открыть интерактивный список статей\n"
        "⚙️ /format — Выбрать формат сохранения по умолчанию (Markdown / HTML)\n"
        "🔍 /search <запрос> — Поиск по архиву\n"
        "📥 /get <ID> — Получить файл по ID статьи\n"
        "🗑️ /delete <ID> — Быстрое удаление по ID\n"
        "⚠️ /reset — Очистить библиотеку"
    )
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("🗑️ Закрыть", callback_data="delete_this_msg")
    ]])
    sent_msg = await update.message.reply_text(welcome_text, reply_markup=keyboard, parse_mode="Markdown")
    context.user_data["last_start_msg_id"] = sent_msg.message_id

@check_user
async def format_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler for the /format command."""
    try:
        await update.message.delete()
    except Exception:
        pass
        
    prev_msg_id = context.user_data.get("last_format_msg_id")
    if prev_msg_id:
        await safe_delete_message(context, update.effective_chat.id, prev_msg_id)

    current_format = context.user_data.get("format", "markdown")
    msg = (
        f"⚙️ *Настройка формата сохранения*\n\n"
        f"Текущий формат по умолчанию: *{current_format.upper()}*\n\n"
        f"Выберите новый формат по умолчанию:"
    )
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📄 Markdown", callback_data="set_format:markdown"),
            InlineKeyboardButton("📚 EPUB", callback_data="set_format:epub"),
            InlineKeyboardButton("🌐 HTML", callback_data="set_format:html")
        ],
        [
            InlineKeyboardButton("🗑️ Закрыть", callback_data="delete_this_msg")
        ]
    ])
    sent_msg = await update.message.reply_text(msg, reply_markup=keyboard, parse_mode="Markdown")
    context.user_data["last_format_msg_id"] = sent_msg.message_id

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
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("🗑️ Закрыть", callback_data="delete_this_msg")
        ]])
        if total == 0:
            sent_msg = await update.message.reply_text("📭 *Ваша библиотека пока пуста.*", reply_markup=keyboard, parse_mode="Markdown")
            context.user_data["last_stats_msg_id"] = sent_msg.message_id
            return
            
        progress = round((stats_data['read_articles'] / total) * 100, 1)
        total_mins = max(1, round(stats_data['total_words'] / 200))
        read_mins = round(stats_data['read_words'] / 200)
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
            f"⏱️ *Время на чтение:*\n"
            f"  • Всего: *~{total_mins} мин.*\n"
            f"  • Прочитано: *~{read_mins} мин.*\n"
            f"  • Осталось: *~{unread_mins} мин.*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📝 Всего слов сохранено: *{stats_data['total_words']:,}*\n"
            f"📐 Средний размер: *{stats_data['avg_words_per_article']:.0f} слов*"
        )
        sent_msg = await update.message.reply_text(msg, reply_markup=keyboard, parse_mode="Markdown")
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
        close_btn = InlineKeyboardButton("🗑️ Закрыть список", callback_data="delete_this_msg")
        if not articles:
            text = "📭 *В вашей библиотеке пока нет статей.*"
            keyboard = InlineKeyboardMarkup([[close_btn]])
        else:
            msg_lines = ["📋 *Последние сохранённые статьи:*\n"]
            keyboard_buttons = []
            for idx, a in enumerate(articles, 1):
                status_icon = "✅" if a['status'] == 'read' else "📥"
                unread_mins = max(1, round(a['word_count'] / 200))
                msg_lines.append(
                    f"*{idx}.* {status_icon} *{a['title']}*\n"
                    f"   _ID: {a['id']} | Слов: {a['word_count']} (~{unread_mins} мин)_\n"
                )
                title = a['title']
                if len(title) > 18:
                    title = title[:15] + "..."
                keyboard_buttons.append([
                    InlineKeyboardButton(text=f"📄 [{a['id']}] {title}", callback_data=f"art:{a['id']}"),
                    InlineKeyboardButton(text="📥 Скачать", callback_data=f"get:{a['id']}")
                ])
            keyboard_buttons.append([close_btn])
                
            text = "\n".join(msg_lines)
            text += "\n💡 _Выберите действие на кнопках ниже для просмотра или скачивания статьи._"
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
            InlineKeyboardButton("📥 Скачать файл", callback_data=f"get:{article['id']}"),
            InlineKeyboardButton(toggle_label, callback_data=f"toggle:{article['id']}")
        ],
        [
            InlineKeyboardButton("🗑️ Удалить статью", callback_data=f"del_confirm:{article['id']}"),
            InlineKeyboardButton("🔙 Назад к списку", callback_data="list")
        ],
        [
            InlineKeyboardButton("🗑️ Закрыть", callback_data="delete_this_msg")
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
        warning_msg = await update.message.reply_text(
            "💡 Пожалуйста, укажите поисковый запрос. Пример: `/search квантовые`",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🗑️ Закрыть", callback_data="delete_this_msg")
            ]]),
            parse_mode="Markdown"
        )
        asyncio.create_task(delayed_delete(context, update.effective_chat.id, warning_msg.message_id, 10))
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
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("🗑️ Закрыть", callback_data="delete_this_msg")
        ]])
        if not results:
            sent_msg = await update.message.reply_text(
                f"🔍 По запросу *'{query}'* ничего не найдено.",
                reply_markup=keyboard,
                parse_mode="Markdown"
            )
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
            title = r['title']
            if len(title) > 18:
                title = title[:15] + "..."
            keyboard_buttons.append([
                InlineKeyboardButton(text=f"📄 [{r['id']}] {title}", callback_data=f"art:{r['id']}"),
                InlineKeyboardButton(text="📥 Скачать", callback_data=f"get:{r['id']}")
            ])
            
        keyboard_buttons.append([InlineKeyboardButton("🗑️ Закрыть результаты", callback_data="delete_this_msg")])

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
        warning_msg = await update.message.reply_text(
            "💡 Пожалуйста, укажите ID статьи. Пример: `/get 5`",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🗑️ Закрыть", callback_data="delete_this_msg")
            ]]),
            parse_mode="Markdown"
        )
        asyncio.create_task(delayed_delete(context, update.effective_chat.id, warning_msg.message_id, 10))
        return
        
    try:
        article_id = int(context.args[0])
    except ValueError:
        warning_msg = await update.message.reply_text(
            "❌ ID статьи должен быть числом. Пример: `/get 5`",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🗑️ Закрыть", callback_data="delete_this_msg")
            ]])
        )
        asyncio.create_task(delayed_delete(context, update.effective_chat.id, warning_msg.message_id, 10))
        return
        
    try:
        article = db.get_article(article_id)
        if not article:
            warning_msg = await update.message.reply_text(
                f"❌ Статья с ID {article_id} не найдена.",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🗑️ Закрыть", callback_data="delete_this_msg")
                ]])
            )
            asyncio.create_task(delayed_delete(context, update.effective_chat.id, warning_msg.message_id, 10))
            return
            
        file_path = article['file_path']
        if os.path.exists(file_path):
            title = article['title']
            word_count = article['word_count']
            status_icon = "✅" if article['status'] == 'read' else "📥"
            read_time = max(1, round(word_count / 200))
            caption = (
                f"{status_icon} *{title}*\n"
                f"📂 ID статьи: `{article_id}` | Слов: {word_count} (*~{read_time} мин. чтения*)\n"
                f"🔗 {article['url']}"
            )
            keyboard = get_document_keyboard(article_id, article['status'])
            with open(file_path, 'rb') as doc_file:
                await update.message.reply_document(
                    document=doc_file,
                    filename=os.path.basename(file_path),
                    caption=caption,
                    reply_markup=keyboard,
                    parse_mode="Markdown"
                )
        else:
            warning_msg = await update.message.reply_text(
                f"❌ Файл статьи не найден на диске.",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🗑️ Закрыть", callback_data="delete_this_msg")
                ]]),
                parse_mode="Markdown"
            )
            asyncio.create_task(delayed_delete(context, update.effective_chat.id, warning_msg.message_id, 10))
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
        warning_msg = await update.message.reply_text(
            "💡 Пожалуйста, укажите ID статьи. Пример: `/delete 5`",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🗑️ Закрыть", callback_data="delete_this_msg")
            ]]),
            parse_mode="Markdown"
        )
        asyncio.create_task(delayed_delete(context, update.effective_chat.id, warning_msg.message_id, 10))
        return
        
    try:
        article_id = int(context.args[0])
    except ValueError:
        warning_msg = await update.message.reply_text(
            "❌ ID статьи должен быть числом. Пример: `/delete 5`",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🗑️ Закрыть", callback_data="delete_this_msg")
            ]])
        )
        asyncio.create_task(delayed_delete(context, update.effective_chat.id, warning_msg.message_id, 10))
        return
        
    try:
        article = db.get_article(article_id)
        if not article:
            warning_msg = await update.message.reply_text(
                f"❌ Статья с ID {article_id} не найдена.",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🗑️ Закрыть", callback_data="delete_this_msg")
                ]])
            )
            asyncio.create_task(delayed_delete(context, update.effective_chat.id, warning_msg.message_id, 10))
            return
            
        success = core.delete_article(article_id)
        if success:
            success_msg = await update.message.reply_text(
                f"🗑️ Статья *\"{article['title']}\"* (ID: {article_id}) успешно удалена.",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🗑️ Закрыть", callback_data="delete_this_msg")
                ]]),
                parse_mode="Markdown"
            )
            asyncio.create_task(delayed_delete(context, update.effective_chat.id, success_msg.message_id, 5))
        else:
            warning_msg = await update.message.reply_text(
                f"❌ Не удалось удалить статью с ID {article_id}.",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🗑️ Закрыть", callback_data="delete_this_msg")
                ]])
            )
            asyncio.create_task(delayed_delete(context, update.effective_chat.id, warning_msg.message_id, 10))
    except Exception as e:
        logger.error(f"Error deleting article {article_id}: {e}")
        await update.message.reply_text(f"❌ Произошла ошибка при удалении статьи.")

@check_user
async def read_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Mark article as read by ID."""
    try:
        await update.message.delete()
    except Exception:
        pass

    if not context.args:
        warning_msg = await update.message.reply_text(
            "💡 Пожалуйста, укажите ID статьи. Пример: `/read 5`",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🗑️ Закрыть", callback_data="delete_this_msg")
            ]]),
            parse_mode="Markdown"
        )
        asyncio.create_task(delayed_delete(context, update.effective_chat.id, warning_msg.message_id, 10))
        return
        
    try:
        article_id = int(context.args[0])
    except ValueError:
        warning_msg = await update.message.reply_text(
            "❌ ID статьи должен быть числом. Пример: `/read 5`",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🗑️ Закрыть", callback_data="delete_this_msg")
            ]])
        )
        asyncio.create_task(delayed_delete(context, update.effective_chat.id, warning_msg.message_id, 10))
        return
        
    try:
        article = db.get_article(article_id)
        if not article:
            warning_msg = await update.message.reply_text(
                f"❌ Статья с ID {article_id} не найдена.",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🗑️ Закрыть", callback_data="delete_this_msg")
                ]])
            )
            asyncio.create_task(delayed_delete(context, update.effective_chat.id, warning_msg.message_id, 10))
            return
            
        db.mark_as_read(article_id, "read")
        success_msg = await update.message.reply_text(
            f"✅ Статья *\"{article['title']}\"* (ID: {article_id}) отмечена как прочитанная.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🗑️ Закрыть", callback_data="delete_this_msg")
            ]]),
            parse_mode="Markdown"
        )
        asyncio.create_task(delayed_delete(context, update.effective_chat.id, success_msg.message_id, 5))
    except Exception as e:
        logger.error(f"Error marking article {article_id} as read: {e}")
        await update.message.reply_text(f"❌ Произошла ошибка при изменении статуса статьи.")

@check_user
async def unread_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Mark article as unread by ID."""
    try:
        await update.message.delete()
    except Exception:
        pass

    if not context.args:
        warning_msg = await update.message.reply_text(
            "💡 Пожалуйста, укажите ID статьи. Пример: `/unread 5`",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🗑️ Закрыть", callback_data="delete_this_msg")
            ]]),
            parse_mode="Markdown"
        )
        asyncio.create_task(delayed_delete(context, update.effective_chat.id, warning_msg.message_id, 10))
        return
        
    try:
        article_id = int(context.args[0])
    except ValueError:
        warning_msg = await update.message.reply_text(
            "❌ ID статьи должен быть числом. Пример: `/unread 5`",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🗑️ Закрыть", callback_data="delete_this_msg")
            ]])
        )
        asyncio.create_task(delayed_delete(context, update.effective_chat.id, warning_msg.message_id, 10))
        return
        
    try:
        article = db.get_article(article_id)
        if not article:
            warning_msg = await update.message.reply_text(
                f"❌ Статья с ID {article_id} не найдена.",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🗑️ Закрыть", callback_data="delete_this_msg")
                ]])
            )
            asyncio.create_task(delayed_delete(context, update.effective_chat.id, warning_msg.message_id, 10))
            return
            
        db.mark_as_read(article_id, "unread")
        success_msg = await update.message.reply_text(
            f"📥 Статья *\"{article['title']}\"* (ID: {article_id}) отмечена как непрочитанная.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🗑️ Закрыть", callback_data="delete_this_msg")
            ]]),
            parse_mode="Markdown"
        )
        asyncio.create_task(delayed_delete(context, update.effective_chat.id, success_msg.message_id, 5))
    except Exception as e:
        logger.error(f"Error marking article {article_id} as unread: {e}")
        await update.message.reply_text(f"❌ Произошла ошибка при изменении статуса статьи.")

@check_user
async def reset_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler for the /reset command."""
    try:
        await update.message.delete()
    except Exception:
        pass

    prev_msg_id = context.user_data.get("last_reset_msg_id")
    if prev_msg_id:
        await safe_delete_message(context, update.effective_chat.id, prev_msg_id)

    sent_msg = await update.message.reply_text(
        "⚠️ *ВНИМАНИЕ!* Вы собираетесь удалить ВСЕ статьи и очистить базу данных.\n"
        "Это действие необратимо.\n\n"
        "Для подтверждения отправьте команду: `/reset_confirm`",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🗑️ Отмена", callback_data="delete_this_msg")
        ]]),
        parse_mode="Markdown"
    )
    context.user_data["last_reset_msg_id"] = sent_msg.message_id

@check_user
async def reset_confirm_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler for /reset_confirm to perform the reset."""
    try:
        await update.message.delete()
    except Exception:
        pass

    prev_reset_msg_id = context.user_data.get("last_reset_msg_id")
    if prev_reset_msg_id:
        await safe_delete_message(context, update.effective_chat.id, prev_reset_msg_id)
        context.user_data["last_reset_msg_id"] = None

    try:
        core.reset_library()
        success_msg = await update.message.reply_text(
            "✅ Библиотека и база данных успешно очищены!",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🗑️ Закрыть", callback_data="delete_this_msg")
            ]])
        )
        asyncio.create_task(delayed_delete(context, update.effective_chat.id, success_msg.message_id, 5))
    except Exception as e:
        logger.error(f"Error resetting library: {e}")
        await update.message.reply_text("❌ Ошибка при очистке библиотеки.")

async def _import_single_url(
    url: str,
    fmt: str,
    converter,
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    semaphore: asyncio.Semaphore,
    force: bool = False
):
    """Worker to import a single URL with concurrency control."""
    async with semaphore:
        if not force:
            existing = db.get_article_by_url(url)
            if existing:
                keyboard = InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton("🔄 Обновить", callback_data=f"force_upd:{existing['id']}:{fmt}"),
                        InlineKeyboardButton("📥 Скачать", callback_data=f"get:{existing['id']}"),
                        InlineKeyboardButton("🗑️ Закрыть", callback_data="delete_this_msg")
                    ]
                ])
                await update.message.reply_text(
                    f"⚠️ Статья уже сохранена в библиотеке (ID: {existing['id']}):\n"
                    f"*{existing['title']}*\n\n"
                    f"Хотите обновить её или скачать существующий файл?",
                    reply_markup=keyboard,
                    parse_mode="Markdown"
                )
                return

        status_msg = await update.message.reply_text(f"⏳ Начинаю импорт ({fmt.upper()}): {url}...")
        try:
            # Process URL through core pipeline
            res = await core.process_url(url, converter=converter, force=force)
            
            # Form response
            title = res['title']
            word_count = res['word_count']
            file_path = res['file_path']
            article_id = res['id']
            
            read_time = max(1, round(word_count / 200))
            response_text = (
                f"📥 *Сохранил!*\n\n"
                f"*{title}*\n"
                f"Слов: {word_count} (*~{read_time} мин. чтения*)\n"
                f"ID статьи: `{article_id}`\n"
                f"🔗 {url}"
            )
            
            # Delete temporary status message
            await safe_delete_message(context, update.effective_chat.id, status_msg.message_id)
            
            # Action buttons for document message
            keyboard = get_document_keyboard(article_id, "unread")
            
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
                
        except Exception as e:
            logger.error(f"Error importing {url}: {e}", exc_info=True)
            # Delete status message
            await safe_delete_message(context, update.effective_chat.id, status_msg.message_id)
            error_msg = await update.message.reply_text(
                f"❌ Ошибка при импорте ссылки: {url}\n"
                f"Детали: `{str(e)}`",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🗑️ Закрыть", callback_data="delete_this_msg")
                ]]),
                parse_mode="Markdown"
            )
            asyncio.create_task(delayed_delete(context, update.effective_chat.id, error_msg.message_id, 30))

@check_user
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle normal messages and extract links."""
    text = update.message.text
    if not text:
        return
        
    # Simple regex to extract URL(s)
    urls = re.findall(r'(https?://\S+)', text)
    if not urls:
        try:
            await update.message.delete()
        except Exception:
            pass
        warning_msg = await update.message.reply_text(
            "ℹ️ Пришлите мне ссылку, чтобы сохранить статью в архив.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🗑️ Закрыть", callback_data="delete_this_msg")
            ]])
        )
        asyncio.create_task(delayed_delete(context, update.effective_chat.id, warning_msg.message_id, 10))
        return
        
    text_lower = text.lower()
    if "html" in text_lower:
        fmt = "html"
    elif "epub" in text_lower:
        fmt = "epub"
    elif "markdown" in text_lower or "md" in text_lower:
        fmt = "markdown"
    else:
        fmt = context.user_data.get("format", "markdown")
        
    from ril.converters import MarkdownConverter, HTMLConverter, EPUBConverter
    if fmt == "html":
        converter = HTMLConverter()
    elif fmt == "epub":
        converter = EPUBConverter()
    else:
        converter = MarkdownConverter()
        
    try:
        await update.message.delete()
    except Exception:
        pass

    force = any(w in text_lower for w in ("force", "update", "обновить"))

    # Process links concurrently with a limit of 3 concurrent chromium/download instances
    semaphore = asyncio.Semaphore(3)
    tasks = [
        _import_single_url(url, fmt, converter, update, context, semaphore, force=force)
        for url in urls
    ]
    await asyncio.gather(*tasks)

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
        
    elif data == "delete_this_msg":
        await query.answer()
        try:
            await query.message.delete()
        except Exception:
            pass

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
            read_time = max(1, round(article['word_count'] / 200))
            rating_val = article.get('rating')
            rating_str = "⭐" * rating_val if rating_val else "нет оценки"
            caption = (
                f"{status_icon} *{article['title']}*\n"
                f"📂 ID статьи: `{art_id}` | Слов: {article['word_count']} (*~{read_time} мин. чтения*)\n"
                f"⭐ Оценка: {rating_str}\n"
                f"🔗 {article['url']}"
            )
            keyboard = get_document_keyboard(art_id, new_status)
            try:
                await query.edit_message_caption(caption=caption, reply_markup=keyboard, parse_mode="Markdown")
            except Exception as e:
                if "not modified" not in str(e).lower():
                    logger.error(f"Error editing caption: {e}")
        else:
            await query.answer("❌ Статья не найдена.", show_alert=True)
            
    elif data.startswith("rate_doc:"):
        parts = data.split(':')
        art_id = int(parts[1])
        rating = int(parts[2])
        db.rate_article(art_id, rating)
        await query.answer(f"Оценка {'⭐' * rating} успешно установлена!", show_alert=True)
        
        article = db.get_article(art_id)
        if article:
            status_icon = "✅" if article['status'] == 'read' else "📥"
            read_time = max(1, round(article['word_count'] / 200))
            rating_str = "⭐" * rating
            caption = (
                f"{status_icon} *{article['title']}*\n"
                f"📂 ID статьи: `{art_id}` | Слов: {article['word_count']} (*~{read_time} мин. чтения*)\n"
                f"⭐ Оценка: {rating_str}\n"
                f"🔗 {article['url']}"
            )
            keyboard = get_document_keyboard(art_id, article['status'])
            try:
                await query.edit_message_caption(caption=caption, reply_markup=keyboard, parse_mode="Markdown")
            except Exception as e:
                if "not modified" not in str(e).lower():
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
            
    elif data.startswith("force_upd:"):
        parts = data.split(":")
        art_id = int(parts[1])
        fmt = parts[2]
        article = db.get_article(art_id)
        if article:
            await query.answer("🔄 Запускаю обновление статьи...")
            from ril.converters import MarkdownConverter, HTMLConverter, EPUBConverter
            if fmt == "html":
                converter = HTMLConverter()
            elif fmt == "epub":
                converter = EPUBConverter()
            else:
                converter = MarkdownConverter()
            
            # Edit the message text to show loading status
            await query.edit_message_text(f"⏳ Обновляю статью: {article['url']}...")
            
            try:
                res = await core.process_url(article['url'], converter=converter, force=True)
                
                title = res['title']
                word_count = res['word_count']
                file_path = res['file_path']
                article_id = res['id']
                
                read_time = max(1, round(word_count / 200))
                response_text = (
                    f"📥 *Обновил!*\n\n"
                    f"*{title}*\n"
                    f"Слов: {word_count} (*~{read_time} мин. чтения*)\n"
                    f"ID статьи: `{article_id}`\n"
                    f"🔗 {article['url']}"
                )
                
                # Delete the loading message
                try:
                    await query.message.delete()
                except Exception:
                    pass
                
                keyboard = get_document_keyboard(article_id, "unread")
                if os.path.exists(file_path):
                    with open(file_path, 'rb') as doc_file:
                        await context.bot.send_document(
                            chat_id=update.effective_chat.id,
                            document=doc_file,
                            filename=os.path.basename(file_path),
                            caption=response_text,
                            reply_markup=keyboard,
                            parse_mode="Markdown"
                        )
                else:
                    await context.bot.send_message(
                        chat_id=update.effective_chat.id,
                        text=f"⚠️ Файл не найден на диске, но сохранен в базе:\n{response_text}",
                        parse_mode="Markdown"
                    )
            except Exception as e:
                logger.error(f"Error force updating {article['url']}: {e}", exc_info=True)
                await query.edit_message_text(
                    f"❌ Ошибка при обновлении ссылки: {article['url']}\n"
                    f"Детали: `{str(e)}`",
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton("🗑️ Закрыть", callback_data="delete_this_msg")
                    ]]),
                    parse_mode="Markdown"
                )
        else:
            await query.answer("❌ Статья не найдена.", show_alert=True)
            
    elif data.startswith("set_format:"):
        fmt = data.split(":")[1]
        context.user_data["format"] = fmt
        await query.answer(f"✅ Формат изменен на {fmt.upper()}")
        
        msg = (
            f"⚙️ *Настройка формата сохранения*\n\n"
            f"Текущий формат по умолчанию изменен на: *{fmt.upper()}*\n\n"
            f"Все новые статьи будут сохраняться в этом формате."
        )
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("📄 Markdown", callback_data="set_format:markdown"),
                InlineKeyboardButton("📚 EPUB", callback_data="set_format:epub"),
                InlineKeyboardButton("🌐 HTML", callback_data="set_format:html")
            ],
            [
                InlineKeyboardButton("🗑️ Закрыть", callback_data="delete_this_msg")
            ]
        ])
        try:
            await query.edit_message_text(msg, reply_markup=keyboard, parse_mode="Markdown")
        except Exception:
            pass

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
    app.add_handler(CommandHandler("read", read_command))
    app.add_handler(CommandHandler("unread", unread_command))
    app.add_handler(CommandHandler("delete", delete_command))
    app.add_handler(CommandHandler("format", format_command))
    app.add_handler(CommandHandler("reset", reset_command))
    app.add_handler(CommandHandler("reset_confirm", reset_confirm_command))
    
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    print("Telegram bot started successfully. Press Ctrl+C to terminate.")
    app.run_polling()
