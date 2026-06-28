use crate::telegram::state::{BotState, PendingState};
use crate::telegram::{keyboards, views, MapTgError};
use std::sync::Arc;
use teloxide::prelude::*;
use teloxide::types::{InlineKeyboardButton, InlineKeyboardMarkup, InputFile, Message};

pub async fn handle_callback_query(
    bot: Bot,
    q: CallbackQuery,
    state: Arc<BotState>,
) -> ResponseResult<()> {
    match handle_callback_query_inner(bot, q, state).await {
        Ok(()) => Ok(()),
        Err(teloxide::RequestError::Api(teloxide::ApiError::MessageNotModified)) => {
            Ok(())
        }
        Err(e) => Err(e),
    }
}

pub async fn handle_callback_query_inner(
    bot: Bot,
    q: CallbackQuery,
    state: Arc<BotState>,
) -> ResponseResult<()> {
    let user = &q.from;

    // Authorization check
    if !crate::telegram::is_allowed(user.id, &state.config.allowed_telegram_users) {
        let _ = bot
            .answer_callback_query(q.id)
            .text("Доступ запрещен")
            .await;
        return Ok(());
    }

    let data = match q.data {
        Some(ref d) => d,
        None => {
            let _ = bot.answer_callback_query(q.id).await;
            return Ok(());
        }
    };

    let msg = match q.message {
        Some(ref m) => m,
        None => {
            let _ = bot.answer_callback_query(q.id).await;
            return Ok(());
        }
    };

    // Acknowledge the callback query to stop the loading spinner
    let _ = bot.answer_callback_query(q.id).await;

    if data == "hub" {
        show_hub(bot, msg, state).await?;
    } else if data.starts_with("list:") {
        let parts: Vec<&str> = data.split(':').collect();
        if parts.len() == 3 {
            let status = parts[1];
            let page: i64 = parts[2].parse().unwrap_or(0);
            show_list(bot, msg, status, page, state).await?;
        }
    } else if data.starts_with("get_file:") {
        let id: i64 = data
            .split(':')
            .nth(1)
            .and_then(|s| s.parse().ok())
            .unwrap_or(0);
        match state.bridge.get_article_content(id).await.tg_err() {
            Ok(res) => {
                let file_path = std::path::Path::new(&res.article.file_path);
                if file_path.exists() {
                    let doc = InputFile::file(file_path);
                    let read_time = (res.article.word_count as f64 / 200.0).ceil() as i64;
                    let rating_stars = match res.article.rating {
                        Some(r) => "⭐".repeat(r as usize),
                        None => "нет оценки".to_string(),
                    };
                    let status_emoji = if res.article.status == "read" { "✅" } else { "📖" };
                    let status_text = if res.article.status == "read" { "Прочитано" } else { "Не прочитано" };
                    let caption = format!(
                        "📄 <b>{}</b>\n\n\
                         <b>Слов:</b> {} (~{} мин. чтения)\n\
                         <b>Статус:</b> {} {}\n\
                         <b>Оценка:</b> {}\n\
                         <b>ID:</b> <code>{}</code>",
                        views::escape_html(&res.article.title),
                        res.article.word_count,
                        read_time,
                        status_emoji,
                        status_text,
                        rating_stars,
                        res.article.id
                    );
                    let markup = keyboards::document_keyboard(id, &res.article.status);
                    bot.send_document(msg.chat.id, doc)
                        .caption(caption)
                        .reply_markup(markup)
                        .parse_mode(teloxide::types::ParseMode::Html)
                        .await?;
                } else if !res.content.is_empty() {
                    let mut text = res.content;
                    if text.len() > 4000 {
                        text.truncate(4000);
                        text.push_str("\n\n[Содержимое урезано из-за лимита Telegram]");
                    }
                    bot.send_message(msg.chat.id, text).await?;
                } else {
                    bot.send_message(msg.chat.id, "Файл не найден, а текст пуст.")
                        .await?;
                }
            }
            Err(e) => {
                bot.send_message(msg.chat.id, format!("Ошибка при получении статьи: {}", e))
                    .await?;
            }
        }
    } else if data.starts_with("toggle_doc:") {
        let id: i64 = data
            .split(':')
            .nth(1)
            .and_then(|s| s.parse().ok())
            .unwrap_or(0);
        if let Ok(content) = state.bridge.get_article_content(id).await {
            let new_status = if content.article.status == "read" {
                state.bridge.mark_article_unread(id).await.tg_err()?;
                "unread"
            } else {
                state.bridge.mark_article_read(id).await.tg_err()?;
                "read"
            };
            
            let status_text = if new_status == "read" { "Прочитано" } else { "Не прочитано" };
            let read_time = (content.article.word_count as f64 / 200.0).ceil() as i64;
            let rating_stars = match content.article.rating {
                Some(r) => "⭐".repeat(r as usize),
                None => "нет оценки".to_string(),
            };
            let status_emoji = if new_status == "read" { "✅" } else { "📖" };
            
            let caption = format!(
                "📄 <b>{}</b>\n\n\
                 <b>Слов:</b> {} (~{} мин. чтения)\n\
                 <b>Статус:</b> {} {}\n\
                 <b>Оценка:</b> {}\n\
                 <b>ID:</b> <code>{}</code>",
                views::escape_html(&content.article.title),
                content.article.word_count,
                read_time,
                status_emoji,
                status_text,
                rating_stars,
                content.article.id
            );
            let markup = keyboards::document_keyboard(id, new_status);
            let _ = bot.edit_message_caption(msg.chat.id, msg.id)
                .caption(caption)
                .reply_markup(markup)
                .parse_mode(teloxide::types::ParseMode::Html)
                .await;
        }
    } else if data.starts_with("del_doc:") {
        let id: i64 = data
            .split(':')
            .nth(1)
            .and_then(|s| s.parse().ok())
            .unwrap_or(0);
        if let Ok(res) = state.bridge.delete_article(id).await {
            if res {
                let _ = bot.delete_message(msg.chat.id, msg.id).await;
            }
        }
    } else if data.starts_with("rate_doc:") {
        let parts: Vec<&str> = data.split(':').collect();
        if parts.len() == 3 {
            let id: i64 = parts[1].parse().unwrap_or(0);
            let val: i32 = parts[2].parse().unwrap_or(0);
            state.bridge.rate_article(id, Some(val)).await.tg_err()?;
            
            let stars = "⭐".repeat(val as usize);
            if let Ok(content) = state.bridge.get_article_content(id).await {
                let read_time = (content.article.word_count as f64 / 200.0).ceil() as i64;
                let status_emoji = if content.article.status == "read" { "✅" } else { "📖" };
                let status_text = if content.article.status == "read" { "Прочитано" } else { "Не прочитано" };
                
                let caption = format!(
                    "📄 <b>{}</b>\n\n\
                     <b>Слов:</b> {} (~{} мин. чтения)\n\
                     <b>Статус:</b> {} {}\n\
                     <b>Оценка:</b> {}\n\
                     <b>ID:</b> <code>{}</code>",
                    views::escape_html(&content.article.title),
                    content.article.word_count,
                    read_time,
                    status_emoji,
                    status_text,
                    stars,
                    content.article.id
                );
                let markup = keyboards::document_keyboard(id, &content.article.status);
                let _ = bot.edit_message_caption(msg.chat.id, msg.id)
                    .caption(caption)
                    .reply_markup(markup)
                    .parse_mode(teloxide::types::ParseMode::Html)
                    .await;
            }
        }
    } else if data.starts_with("art:") {
        let id: i64 = data
            .split(':')
            .nth(1)
            .and_then(|s| s.parse().ok())
            .unwrap_or(0);
        show_article(bot, msg, id, state).await?;
    } else if data.starts_with("read:") {
        let id: i64 = data
            .split(':')
            .nth(1)
            .and_then(|s| s.parse().ok())
            .unwrap_or(0);
        state.bridge.mark_article_read(id).await.tg_err()?;
        show_article(bot, msg, id, state).await?;
    } else if data.starts_with("unread:") {
        let id: i64 = data
            .split(':')
            .nth(1)
            .and_then(|s| s.parse().ok())
            .unwrap_or(0);
        state.bridge.mark_article_unread(id).await.tg_err()?;
        show_article(bot, msg, id, state).await?;
    } else if data.starts_with("art_del:") {
        let id: i64 = data
            .split(':')
            .nth(1)
            .and_then(|s| s.parse().ok())
            .unwrap_or(0);
        show_delete_confirm(bot, msg, id, state).await?;
    } else if data.starts_with("art_del_conf:") {
        let id: i64 = data
            .split(':')
            .nth(1)
            .and_then(|s| s.parse().ok())
            .unwrap_or(0);
        state.bridge.delete_article(id).await.tg_err()?;
        bot.edit_message_text(msg.chat.id, msg.id, "🗑 <b>Материал успешно удален.</b>")
            .reply_markup(keyboards::back_to_hub_keyboard())
            .parse_mode(teloxide::types::ParseMode::Html)
            .await?;
    } else if data.starts_with("art_rate:") {
        let id: i64 = data
            .split(':')
            .nth(1)
            .and_then(|s| s.parse().ok())
            .unwrap_or(0);
        bot.edit_message_text(
            msg.chat.id,
            msg.id,
            "⭐ <b>Пожалуйста, выберите оценку для материала:</b>",
        )
        .reply_markup(keyboards::rating_keyboard(id))
        .parse_mode(teloxide::types::ParseMode::Html)
        .await?;
    } else if data.starts_with("rate_set:") {
        let parts: Vec<&str> = data.split(':').collect();
        if parts.len() == 3 {
            let id: i64 = parts[1].parse().unwrap_or(0);
            let val: i32 = parts[2].parse().unwrap_or(0);
            let rating = if val == 0 { None } else { Some(val) };
            state.bridge.rate_article(id, rating).await.tg_err()?;

            if rating.is_some() {
                state
                    .set_pending_state(user.id, PendingState::WaitingForComment { article_id: id })
                    .await;
                bot.edit_message_text(msg.chat.id, msg.id, "⭐ <b>Оценка успешно установлена!</b>\n\nНапишите текстовый комментарий к этому материалу и отправьте его в ответном сообщении. Если комментарий не нужен, просто нажмите кнопку ниже.")
                    .reply_markup(keyboards::back_to_article_keyboard(id))
                    .parse_mode(teloxide::types::ParseMode::Html)
                    .await?;
            } else {
                show_article(bot, msg, id, state).await?;
            }
        }
    } else if data.starts_with("art_comm:") {
        let id: i64 = data
            .split(':')
            .nth(1)
            .and_then(|s| s.parse().ok())
            .unwrap_or(0);
        show_comment_menu(bot, msg, id, state).await?;
    } else if data.starts_with("comm_set:") {
        let id: i64 = data
            .split(':')
            .nth(1)
            .and_then(|s| s.parse().ok())
            .unwrap_or(0);
        state
            .set_pending_state(user.id, PendingState::WaitingForComment { article_id: id })
            .await;
        bot.edit_message_text(
            msg.chat.id,
            msg.id,
            "💬 <b>Пожалуйста, отправьте ваш комментарий следующим сообщением:</b>",
        )
        .reply_markup(keyboards::back_to_article_keyboard(id))
        .parse_mode(teloxide::types::ParseMode::Html)
        .await?;
    } else if data.starts_with("comm_del:") {
        let id: i64 = data
            .split(':')
            .nth(1)
            .and_then(|s| s.parse().ok())
            .unwrap_or(0);
        state.bridge.set_article_comment(id, None).await.tg_err()?;
        show_article(bot, msg, id, state).await?;
    } else if data.starts_with("art_tags:") {
        let id: i64 = data
            .split(':')
            .nth(1)
            .and_then(|s| s.parse().ok())
            .unwrap_or(0);
        show_article_tags(bot, msg, id, state).await?;
    } else if data.starts_with("tag_add:") {
        let id: i64 = data
            .split(':')
            .nth(1)
            .and_then(|s| s.parse().ok())
            .unwrap_or(0);
        state
            .set_pending_state(user.id, PendingState::WaitingForTag { article_id: id })
            .await;
        bot.edit_message_text(
            msg.chat.id,
            msg.id,
            "🏷 <b>Пожалуйста, отправьте название тега (или несколько через запятую):</b>",
        )
        .reply_markup(keyboards::back_to_article_keyboard(id))
        .parse_mode(teloxide::types::ParseMode::Html)
        .await?;
    } else if data.starts_with("tag_rem:") {
        let parts: Vec<&str> = data.splitn(3, ':').collect();
        if parts.len() == 3 {
            let id: i64 = parts[1].parse().unwrap_or(0);
            let tag = parts[2];
            state.bridge.remove_tag(id, tag).await.tg_err()?;
            show_article_tags(bot, msg, id, state).await?;
        }
    } else if data.starts_with("tags_list:") {
        let page: i64 = data
            .split(':')
            .nth(1)
            .and_then(|s| s.parse().ok())
            .unwrap_or(0);
        show_tags_list(bot, msg, page, state).await?;
    } else if data.starts_with("stag:") {
        let parts: Vec<&str> = data.splitn(3, ':').collect();
        if parts.len() == 3 {
            let tag = parts[1];
            let page: i64 = parts[2].parse().unwrap_or(0);
            show_articles_by_tag(bot, msg, tag, page, state).await?;
        }
    } else if data == "ratings_list" {
        show_ratings_list(bot, msg).await?;
    } else if data.starts_with("srate:") {
        let parts: Vec<&str> = data.split(':').collect();
        if parts.len() == 3 {
            let rating: i32 = parts[1].parse().unwrap_or(0);
            let page: i64 = parts[2].parse().unwrap_or(0);
            show_articles_by_rating(bot, msg, rating, page, state).await?;
        }
    } else if data.starts_with("stats:") {
        let section = data.split(':').nth(1).unwrap_or("overview");
        show_stats(bot, msg, section, state).await?;
    } else if data == "settings" {
        show_settings(bot, msg, user.id, state).await?;
    } else if data.starts_with("set_fmt:") {
        let fmt_str = data.split(':').nth(1).unwrap_or("markdown");
        if let Ok(fmt) = fmt_str.parse::<crate::domain::SaveFormat>() {
            let mut map = state.user_formats.lock().await;
            map.insert(user.id, fmt);
        }
        show_settings(bot, msg, user.id, state).await?;
    } else if data == "reset_lib_prompt" {
        bot.edit_message_text(msg.chat.id, msg.id, "⚠️ <b>ВНИМАНИЕ!</b>\n\nЭто действие безвозвратно удалит ВСЕ сохраненные материалы, файлы и записи в базе данных.\n\nВы уверены?")
            .reply_markup(keyboards::reset_lib_confirm_keyboard())
            .parse_mode(teloxide::types::ParseMode::Html)
            .await?;
    } else if data == "reset_lib_confirm" {
        state.bridge.reset_library().await.tg_err()?;
        bot.edit_message_text(
            msg.chat.id,
            msg.id,
            "✅ <b>Библиотека полностью очищена.</b>",
        )
        .reply_markup(keyboards::back_to_hub_keyboard())
        .parse_mode(teloxide::types::ParseMode::Html)
        .await?;
    } else if data == "search" {
        show_search_menu(bot, msg, user.id, state).await?;
    } else if data == "sf_query" {
        state
            .set_pending_state(user.id, PendingState::WaitingForSearchQuery)
            .await;
        bot.edit_message_text(
            msg.chat.id,
            msg.id,
            "🔎 <b>Введите поисковый запрос (текст или ключевые слова):</b>",
        )
        .reply_markup(keyboards::back_to_article_keyboard(0)) // fallback to cancel
        .parse_mode(teloxide::types::ParseMode::Html)
        .await?;
    } else if data == "sf_domain" {
        state
            .set_pending_state(user.id, PendingState::WaitingForFilterDomain)
            .await;
        bot.edit_message_text(
            msg.chat.id,
            msg.id,
            "🌐 <b>Введите домен сайта для фильтрации (например, habr.com или medium.com):</b>",
        )
        .reply_markup(keyboards::back_to_article_keyboard(0))
        .parse_mode(teloxide::types::ParseMode::Html)
        .await?;
    } else if data == "sf_tag" {
        show_search_tag_select(bot, msg, state).await?;
    } else if data.starts_with("sft_select:") {
        let tag = data.split(':').nth(1).unwrap_or("none");
        state
            .update_search_session(user.id, |s| {
                s.tag = if tag == "none" {
                    None
                } else {
                    Some(tag.to_string())
                };
            })
            .await;
        show_search_menu(bot, msg, user.id, state).await?;
    } else if data == "sf_status" {
        bot.edit_message_text(
            msg.chat.id,
            msg.id,
            "📝 <b>Выберите статус прочтения для фильтрации:</b>",
        )
        .reply_markup(keyboards::search_status_select_keyboard())
        .parse_mode(teloxide::types::ParseMode::Html)
        .await?;
    } else if data.starts_with("sfs_") {
        let val = data.strip_prefix("sfs_").unwrap_or("any");
        state
            .update_search_session(user.id, |s| {
                s.status = match val {
                    "read" => Some("read".to_string()),
                    "unread" => Some("unread".to_string()),
                    _ => None,
                };
            })
            .await;
        show_search_menu(bot, msg, user.id, state).await?;
    } else if data == "sf_rating" {
        bot.edit_message_text(
            msg.chat.id,
            msg.id,
            "⭐ <b>Выберите оценку для фильтрации:</b>",
        )
        .reply_markup(keyboards::search_rating_select_keyboard())
        .parse_mode(teloxide::types::ParseMode::Html)
        .await?;
    } else if data.starts_with("sfr_") {
        let val = data.strip_prefix("sfr_").unwrap_or("any");
        state
            .update_search_session(user.id, |s| {
                if val == "none" {
                    s.no_rating = true;
                    s.rating = None;
                } else if val == "any" {
                    s.no_rating = false;
                    s.rating = None;
                } else if let Ok(r) = val.parse::<i32>() {
                    s.no_rating = false;
                    s.rating = Some(r);
                }
            })
            .await;
        show_search_menu(bot, msg, user.id, state).await?;
    } else if data == "sf_date" {
        bot.edit_message_text(
            msg.chat.id,
            msg.id,
            "📅 <b>Выберите интервал добавления:</b>",
        )
        .reply_markup(keyboards::search_date_select_keyboard())
        .parse_mode(teloxide::types::ParseMode::Html)
        .await?;
    } else if data.starts_with("sfd_") {
        let val = data.strip_prefix("sfd_").unwrap_or("any");
        state
            .update_search_session(user.id, |s| {
                s.date_added = match val {
                    "today" => Some("today".to_string()),
                    "week" => Some("week".to_string()),
                    "month" => Some("month".to_string()),
                    _ => None,
                };
            })
            .await;
        show_search_menu(bot, msg, user.id, state).await?;
    } else if data == "sf_reset" {
        state.clear_search_session(user.id).await;
        show_search_menu(bot, msg, user.id, state).await?;
    } else if data.starts_with("sf_run:") {
        let page: i64 = data
            .split(':')
            .nth(1)
            .and_then(|s| s.parse().ok())
            .unwrap_or(0);
        run_search(bot, msg, user.id, page, state).await?;
    }

    Ok(())
}

async fn show_hub(bot: Bot, msg: &Message, state: Arc<BotState>) -> ResponseResult<()> {
    let stats = state.bridge.get_reading_stats().await.tg_err()?;
    let progress = if stats.total_articles > 0 {
        (stats.read_articles as f64 / stats.total_articles as f64) * 100.0
    } else {
        0.0
    };
    let text = format!(
        "📚 <b>Read It Later Hub</b>\n\n\
         Всего материалов: <b>{}</b>\n\
         Прочитано: <b>{}</b> (прогресс {:.1}%)\n\
         Не прочитано: <b>{}</b>\n\n\
         Выберите действие на клавиатуре ниже:",
        stats.total_articles, stats.read_articles, progress, stats.unread_articles
    );
    bot.edit_message_text(msg.chat.id, msg.id, text)
        .reply_markup(keyboards::hub_keyboard())
        .parse_mode(teloxide::types::ParseMode::Html)
        .await?;
    Ok(())
}

async fn show_list(
    bot: Bot,
    msg: &Message,
    status: &str,
    page: i64,
    state: Arc<BotState>,
) -> ResponseResult<()> {
    let limit = 8;
    let offset = page * limit;
    let status_filter = if status == "all" {
        None
    } else {
        Some(status.to_string())
    };

    let paginated = state
        .bridge
        .search_articles_advanced(
            None,
            status_filter,
            None,
            None,
            None,
            false,
            false,
            None,
            limit,
            offset,
        )
        .await
        .tg_err()?;

    let total_pages = (paginated.total_count as f64 / limit as f64).ceil() as i64;

    let prev_cb = if page > 0 {
        Some(format!("list:{}:{}", status, page - 1))
    } else {
        None
    };
    let next_cb = if page + 1 < total_pages {
        Some(format!("list:{}:{}", status, page + 1))
    } else {
        None
    };

    let title = match status {
        "read" => "Прочитанные материалы",
        "unread" => "Непрочитанные материалы",
        _ => "Все материалы библиотеки",
    };

    let text = views::render_articles_list(&paginated.articles, title, page, total_pages);
    bot.edit_message_text(msg.chat.id, msg.id, text)
        .reply_markup(keyboards::articles_list_keyboard(&paginated.articles, prev_cb, next_cb, "hub"))
        .parse_mode(teloxide::types::ParseMode::Html)
        .await?;
    Ok(())
}

async fn show_article(
    bot: Bot,
    msg: &Message,
    id: i64,
    state: Arc<BotState>,
) -> ResponseResult<()> {
    if id == 0 {
        // Fallback to hub if id is invalid or it was a cancel button
        show_hub(bot, msg, state).await?;
        return Ok(());
    }

    match state.bridge.get_article_content(id).await.tg_err() {
        Ok(content) => {
            let text = views::render_article_card(&content.article);
            bot.edit_message_text(msg.chat.id, msg.id, text)
                .reply_markup(keyboards::article_card_keyboard(
                    content.article.id,
                    &content.article.status,
                    &content.article.url,
                ))
                .parse_mode(teloxide::types::ParseMode::Html)
                .await?;
        }
        Err(_) => {
            bot.edit_message_text(msg.chat.id, msg.id, "❌ <b>Ошибка: материал не найден.</b>")
                .reply_markup(keyboards::back_to_hub_keyboard())
                .parse_mode(teloxide::types::ParseMode::Html)
                .await?;
        }
    }
    Ok(())
}

async fn show_delete_confirm(
    bot: Bot,
    msg: &Message,
    id: i64,
    state: Arc<BotState>,
) -> ResponseResult<()> {
    let content = state.bridge.get_article_content(id).await.tg_err()?;
    let text = format!(
        "⚠️ <b>Вы уверены, что хотите удалить этот материал?</b>\n\n<b>{}</b>",
        views::escape_html(&content.article.title)
    );
    bot.edit_message_text(msg.chat.id, msg.id, text)
        .reply_markup(keyboards::delete_confirm_keyboard(id))
        .parse_mode(teloxide::types::ParseMode::Html)
        .await?;
    Ok(())
}

async fn show_comment_menu(
    bot: Bot,
    msg: &Message,
    id: i64,
    state: Arc<BotState>,
) -> ResponseResult<()> {
    let content = state.bridge.get_article_content(id).await.tg_err()?;
    let current_comm = content
        .article
        .comment
        .as_deref()
        .unwrap_or("<i>комментарий отсутствует</i>");
    let text = format!(
        "💬 <b>Комментарий к статье:</b>\n\n<i>{}</i>",
        views::escape_html(current_comm)
    );
    bot.edit_message_text(msg.chat.id, msg.id, text)
        .reply_markup(keyboards::comment_keyboard(id))
        .parse_mode(teloxide::types::ParseMode::Html)
        .await?;
    Ok(())
}

async fn show_article_tags(
    bot: Bot,
    msg: &Message,
    id: i64,
    state: Arc<BotState>,
) -> ResponseResult<()> {
    let content = state.bridge.get_article_content(id).await.tg_err()?;
    let mut rows = vec![];
    rows.push(vec![InlineKeyboardButton::callback(
        "➕ Добавить тег",
        format!("tag_add:{}", id),
    )]);

    // Row for each tag to delete it
    for t in &content.article.tags {
        rows.push(vec![InlineKeyboardButton::callback(
            format!("❌ Удалить #{}", t),
            format!("tag_rem:{}:{}", id, t),
        )]);
    }
    rows.push(vec![InlineKeyboardButton::callback(
        "🔙 Назад",
        format!("art:{}", id),
    )]);
    let markup = InlineKeyboardMarkup::new(rows);

    let tags_text = if content.article.tags.is_empty() {
        "нет тегов".to_string()
    } else {
        content
            .article
            .tags
            .iter()
            .map(|t| format!("#{}", t))
            .collect::<Vec<_>>()
            .join(", ")
    };

    let text = format!(
        "🏷 <b>Теги материала:</b>\n\n<code>{}</code>",
        views::escape_html(&tags_text)
    );
    bot.edit_message_text(msg.chat.id, msg.id, text)
        .reply_markup(markup)
        .parse_mode(teloxide::types::ParseMode::Html)
        .await?;
    Ok(())
}

async fn show_tags_list(
    bot: Bot,
    msg: &Message,
    page: i64,
    state: Arc<BotState>,
) -> ResponseResult<()> {
    let tags = state.bridge.list_tags().await.tg_err()?;
    let limit = 10;
    let offset = page * limit;
    let total_pages = (tags.len() as f64 / limit as f64).ceil() as i64;

    let mut paged_tags = tags;
    if offset < paged_tags.len() as i64 {
        paged_tags = paged_tags.split_off(offset as usize);
    } else {
        paged_tags.clear();
    }
    paged_tags.truncate(limit as usize);

    let text = views::render_tags_stats(&paged_tags);
    bot.edit_message_text(msg.chat.id, msg.id, text)
        .reply_markup(keyboards::tags_list_keyboard(
            &paged_tags,
            page,
            total_pages,
        ))
        .parse_mode(teloxide::types::ParseMode::Html)
        .await?;
    Ok(())
}

async fn show_articles_by_tag(
    bot: Bot,
    msg: &Message,
    tag: &str,
    page: i64,
    state: Arc<BotState>,
) -> ResponseResult<()> {
    let limit = 8;
    let offset = page * limit;
    let paginated = state
        .bridge
        .search_articles_advanced(
            None,
            None,
            Some(tag.to_string()),
            None,
            None,
            false,
            false,
            None,
            limit,
            offset,
        )
        .await
        .tg_err()?;
    let total_pages = (paginated.total_count as f64 / limit as f64).ceil() as i64;

    let prev_cb = if page > 0 {
        Some(format!("stag:{}:{}", tag, page - 1))
    } else {
        None
    };
    let next_cb = if page + 1 < total_pages {
        Some(format!("stag:{}:{}", tag, page + 1))
    } else {
        None
    };

    let text = views::render_articles_list(
        &paginated.articles,
        &format!("Тег: #{}", tag),
        page,
        total_pages,
    );
    bot.edit_message_text(msg.chat.id, msg.id, text)
        .reply_markup(keyboards::pagination_keyboard(
            prev_cb,
            next_cb,
            "tags_list:0",
        ))
        .parse_mode(teloxide::types::ParseMode::Html)
        .await?;
    Ok(())
}

async fn show_articles_by_rating(
    bot: Bot,
    msg: &Message,
    rating: i32,
    page: i64,
    state: Arc<BotState>,
) -> ResponseResult<()> {
    let limit = 8;
    let offset = page * limit;
    let rating_opt = if rating == 0 { None } else { Some(rating) };
    let no_rating = rating == 0;

    let paginated = state
        .bridge
        .search_articles_advanced(
            None, None, None, rating_opt, None, false, no_rating, None, limit, offset,
        )
        .await
        .tg_err()?;
    let total_pages = (paginated.total_count as f64 / limit as f64).ceil() as i64;

    let prev_cb = if page > 0 {
        Some(format!("srate:{}:{}", rating, page - 1))
    } else {
        None
    };
    let next_cb = if page + 1 < total_pages {
        Some(format!("srate:{}:{}", rating, page + 1))
    } else {
        None
    };

    let title = if rating == 0 {
        "Материалы без оценки".to_string()
    } else {
        format!("Оценка: {} ⭐", rating)
    };
    let text = views::render_articles_list(&paginated.articles, &title, page, total_pages);
    bot.edit_message_text(msg.chat.id, msg.id, text)
        .reply_markup(keyboards::pagination_keyboard(
            prev_cb,
            next_cb,
            "ratings_list",
        ))
        .parse_mode(teloxide::types::ParseMode::Html)
        .await?;
    Ok(())
}

async fn show_stats(
    bot: Bot,
    msg: &Message,
    section: &str,
    state: Arc<BotState>,
) -> ResponseResult<()> {
    let text = match section {
        "sources" => {
            let sources = state.bridge.get_sources_stats(15).await.tg_err()?;
            views::render_sources_stats(&sources)
        }
        "tags" => {
            let tags = state.bridge.get_tags_stats().await.tg_err()?;
            views::render_tags_stats(&tags)
        }
        "ratings" => {
            let ratings = state.bridge.get_ratings_stats().await.tg_err()?;
            views::render_ratings_stats(&ratings)
        }
        "dynamics" => {
            let dynamics = state.bridge.get_dynamics_stats().await.tg_err()?;
            views::render_dynamics_stats(&dynamics)
        }
        _ => {
            let stats = state.bridge.get_extended_stats().await.tg_err()?;
            views::render_stats_overview(&stats)
        }
    };

    bot.edit_message_text(msg.chat.id, msg.id, text)
        .reply_markup(keyboards::stats_menu_keyboard())
        .parse_mode(teloxide::types::ParseMode::Html)
        .await?;
    Ok(())
}

async fn show_settings(
    bot: Bot,
    msg: &Message,
    user_id: UserId,
    state: Arc<BotState>,
) -> ResponseResult<()> {
    let current_fmt = {
        let map = state.user_formats.lock().await;
        *map.get(&user_id).unwrap_or(&state.config.default_format)
    }
    .to_string();

    let text = format!(
        "⚙️ <b>Настройки Read It Later Bot</b>\n\nТекущий формат сохранения по умолчанию: <b>{}</b>",
        current_fmt
    );
    bot.edit_message_text(msg.chat.id, msg.id, text)
        .reply_markup(keyboards::settings_keyboard(&current_fmt))
        .parse_mode(teloxide::types::ParseMode::Html)
        .await?;
    Ok(())
}

async fn show_search_menu(
    bot: Bot,
    msg: &Message,
    user_id: UserId,
    state: Arc<BotState>,
) -> ResponseResult<()> {
    let session = state.get_search_session(user_id).await;
    let text = "🔎 <b>Расширенный поиск материалов</b>\n\nНастройте фильтры с помощью кнопок ниже и нажмите <b>Искать</b>:";
    bot.edit_message_text(msg.chat.id, msg.id, text)
        .reply_markup(keyboards::search_menu_keyboard(&session))
        .parse_mode(teloxide::types::ParseMode::Html)
        .await?;
    Ok(())
}

async fn show_search_tag_select(
    bot: Bot,
    msg: &Message,
    state: Arc<BotState>,
) -> ResponseResult<()> {
    let tags = state.bridge.list_tags().await.tg_err()?;
    let mut rows = vec![];
    for chunk in tags.chunks(3) {
        let mut row = vec![];
        for t in chunk {
            row.push(InlineKeyboardButton::callback(
                format!("#{}", t.tag),
                format!("sft_select:{}", t.tag),
            ));
        }
        rows.push(row);
    }
    rows.push(vec![InlineKeyboardButton::callback(
        "❌ Сбросить тег",
        "sft_select:none",
    )]);
    rows.push(vec![InlineKeyboardButton::callback(
        "🔙 Назад к поиску",
        "search",
    )]);
    let markup = InlineKeyboardMarkup::new(rows);

    bot.edit_message_text(msg.chat.id, msg.id, "🏷 <b>Выберите тег для фильтрации:</b>")
        .reply_markup(markup)
        .parse_mode(teloxide::types::ParseMode::Html)
        .await?;
    Ok(())
}

async fn run_search(
    bot: Bot,
    msg: &Message,
    user_id: UserId,
    page: i64,
    state: Arc<BotState>,
) -> ResponseResult<()> {
    let session = state.get_search_session(user_id).await;
    let limit = 6;
    let offset = page * limit;

    let paginated = state
        .bridge
        .search_articles_advanced(
            session.query.clone(),
            session.status.clone(),
            session.tag.clone(),
            session.rating,
            session.domain.clone(),
            session.no_tags,
            session.no_rating,
            session.date_added.clone(),
            limit,
            offset,
        )
        .await
        .tg_err()?;

    let total_pages = (paginated.total_count as f64 / limit as f64).ceil() as i64;
    let prev_cb = if page > 0 {
        Some(format!("sf_run:{}", page - 1))
    } else {
        None
    };
    let next_cb = if page + 1 < total_pages {
        Some(format!("sf_run:{}", page + 1))
    } else {
        None
    };

    let text =
        views::render_articles_list(&paginated.articles, "Результаты поиска", page, total_pages);
    bot.edit_message_text(msg.chat.id, msg.id, text)
        .reply_markup(keyboards::articles_list_keyboard(&paginated.articles, prev_cb, next_cb, "search"))
        .parse_mode(teloxide::types::ParseMode::Html)
        .await?;
    Ok(())
}

async fn show_ratings_list(bot: Bot, msg: &Message) -> ResponseResult<()> {
    let mut rows = vec![];
    for rating in (1..=5).rev() {
        rows.push(vec![InlineKeyboardButton::callback(
            format!("⭐ {}", rating),
            format!("srate:{}:0", rating),
        )]);
    }
    rows.push(vec![InlineKeyboardButton::callback(
        "❌ Без оценки",
        "srate:0:0",
    )]);
    rows.push(vec![InlineKeyboardButton::callback("🏠 В хаб", "hub")]);
    let markup = InlineKeyboardMarkup::new(rows);

    bot.edit_message_text(
        msg.chat.id,
        msg.id,
        "⭐ <b>Выберите оценку для фильтрации:</b>",
    )
    .reply_markup(markup)
    .parse_mode(teloxide::types::ParseMode::Html)
    .await?;
    Ok(())
}
