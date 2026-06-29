/// Validates that a rating is either None or Some(1..=5).
pub fn validate_rating(rating: Option<i32>) -> Result<Option<i32>, String> {
    match rating {
        None => Ok(None),
        Some(r) if (1..=5).contains(&r) => Ok(Some(r)),
        Some(r) => Err(format!("Rating must be between 1 and 5, got {}", r)),
    }
}

/// Normalizes tag string: splits by comma, trims, removes empty, deduplicates, and converts to lowercase.
pub fn normalize_tags(text: &str) -> Vec<String> {
    let mut tags = Vec::new();
    for part in text.split(',') {
        let trimmed = part.trim();
        if !trimmed.is_empty() {
            let normalized = trimmed.to_lowercase();
            if !tags.contains(&normalized) {
                tags.push(normalized);
            }
        }
    }
    tags
}

/// Calculates total pages and prev/next page numbers.
/// Note: page is 0-indexed internally, but let's handle all inputs robustly.
pub fn calculate_pages(page: i64, total_count: i64, limit: i64) -> (i64, Option<i64>, Option<i64>) {
    if limit <= 0 || total_count <= 0 {
        return (0, None, None);
    }
    let total_pages = (total_count as f64 / limit as f64).ceil() as i64;

    // Clamp page to valid range
    let current_page = if page < 0 {
        0
    } else if page >= total_pages {
        total_pages - 1
    } else {
        page
    };

    let prev = if current_page > 0 {
        Some(current_page - 1)
    } else {
        None
    };
    let next = if current_page + 1 < total_pages {
        Some(current_page + 1)
    } else {
        None
    };

    (total_pages, prev, next)
}

/// Validates comments (e.g. non-empty and within character limits).
pub fn validate_comment(comment: &str) -> Result<String, String> {
    let trimmed = comment.trim();
    if trimmed.is_empty() {
        return Err("Comment cannot be empty".to_string());
    }
    if trimmed.len() > 1000 {
        return Err("Comment is too long (maximum 1000 characters)".to_string());
    }
    Ok(trimmed.to_string())
}

use crate::telegram::state::BotState;
use crate::telegram::{keyboards, views, MapTgError};
use std::sync::Arc;
use teloxide::prelude::*;
use teloxide::types::{InlineKeyboardMarkup, UserId};

pub async fn show_hub(
    bot: Bot,
    chat_id: ChatId,
    state: Arc<BotState>,
    user_id: UserId,
) -> ResponseResult<()> {
    let stats = state.bridge.get_reading_stats().await.tg_err()?;
    let progress = if stats.total_articles > 0 {
        (stats.read_articles as f64 / stats.total_articles as f64) * 100.0
    } else {
        0.0
    };
    
    let current_fmt = {
        let map = state.user_formats.lock().await;
        *map.get(&user_id).unwrap_or(&state.config.default_format)
    }
    .to_string();

    let welcome = format!(
        "📚 <b>Read It Later Bot</b> — ваш удобный хаб для материалов!\n\n\
         Всего материалов: <b>{}</b>\n\
         Прочитано: <b>{}</b> (прогресс {:.1}%)\n\
         Не прочитано: <b>{}</b>\n\
         Формат скачивания: <b>{}</b>\n\n\
         Отправьте мне ссылку, чтобы сохранить её, или воспользуйтесь кнопками меню:",
        stats.total_articles, stats.read_articles, progress, stats.unread_articles, current_fmt
    );

    let markup = keyboards::hub_keyboard();

    let mut edited = false;
    if let Some(hub_msg_id) = state.get_hub_message(user_id).await {
        match bot
            .edit_message_text(chat_id, teloxide::types::MessageId(hub_msg_id), &welcome)
            .reply_markup(markup.clone())
            .parse_mode(teloxide::types::ParseMode::Html)
            .await
        {
            Ok(_) => edited = true,
            Err(teloxide::RequestError::Api(teloxide::ApiError::MessageNotModified)) => {
                edited = true;
            }
            Err(_) => {
                // If editing failed, we will send a new one
            }
        }
    }

    if !edited {
        let sent = bot
            .send_message(chat_id, &welcome)
            .reply_markup(markup)
            .parse_mode(teloxide::types::ParseMode::Html)
            .await?;
        state.set_hub_message(user_id, sent.id.0).await;
    }

    // Clean up/delete the state message if any to keep chat clean
    if let Some(state_msg_id) = state.clear_state_message(user_id).await {
        let _ = bot
            .delete_message(chat_id, teloxide::types::MessageId(state_msg_id))
            .await;
    }

    Ok(())
}

pub async fn show_state_screen(
    bot: Bot,
    chat_id: ChatId,
    state: Arc<BotState>,
    user_id: UserId,
    text: String,
    markup: Option<InlineKeyboardMarkup>,
) -> ResponseResult<()> {
    let mut edited = false;
    if let Some(state_msg_id) = state.get_state_message(user_id).await {
        let mut edit_req = bot.edit_message_text(chat_id, teloxide::types::MessageId(state_msg_id), &text);
        if let Some(ref m) = markup {
            edit_req = edit_req.reply_markup(m.clone());
        }
        match edit_req.parse_mode(teloxide::types::ParseMode::Html).await {
            Ok(_) => edited = true,
            Err(teloxide::RequestError::Api(teloxide::ApiError::MessageNotModified)) => {
                edited = true;
            }
            Err(_) => {
                // Delete previous if it exists but failed to edit
                let _ = bot
                    .delete_message(chat_id, teloxide::types::MessageId(state_msg_id))
                    .await;
            }
        }
    }

    if !edited {
        let mut send_req = bot.send_message(chat_id, &text);
        if let Some(m) = markup {
            send_req = send_req.reply_markup(m);
        }
        let sent = send_req.parse_mode(teloxide::types::ParseMode::Html).await?;
        state.set_state_message(user_id, sent.id.0).await;
    }

    Ok(())
}

pub async fn edit_or_replace_state(
    bot: Bot,
    chat_id: ChatId,
    state: Arc<BotState>,
    user_id: UserId,
    text: String,
    markup: Option<InlineKeyboardMarkup>,
) -> ResponseResult<()> {
    show_state_screen(bot, chat_id, state, user_id, text, markup).await
}

pub async fn ack_action(bot: &Bot, q_id: String, text: &str) -> ResponseResult<()> {
    let _ = bot.answer_callback_query(q_id).text(text).await;
    Ok(())
}

pub async fn show_error_state(
    bot: Bot,
    chat_id: ChatId,
    state: Arc<BotState>,
    user_id: UserId,
    error_msg: &str,
) -> ResponseResult<()> {
    let text = format!("❌ <b>Ошибка:</b>\n\n{}", error_msg);
    let markup = keyboards::back_to_hub_keyboard();
    show_state_screen(bot, chat_id, state, user_id, text, Some(markup)).await
}

pub async fn show_settings_screen(
    bot: Bot,
    chat_id: ChatId,
    state: Arc<BotState>,
    user_id: UserId,
) -> ResponseResult<()> {
    let current_fmt = {
        let map = state.user_formats.lock().await;
        *map.get(&user_id).unwrap_or(&state.config.default_format)
    }
    .to_string();

    let text = format!(
        "⚙️ <b>Настройки</b>\n\n\
         Текущий формат скачивания: <b>{}</b>\n\n\
         Этот формат применяется при скачивании любых материалов, включая уже сохраненные.",
        current_fmt.to_uppercase()
    );
    let markup = keyboards::settings_keyboard(&current_fmt);
    show_state_screen(bot, chat_id, state, user_id, text, Some(markup)).await
}

pub async fn show_stats_screen(
    bot: Bot,
    chat_id: ChatId,
    state: Arc<BotState>,
    user_id: UserId,
    section: &str,
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

    let markup = keyboards::stats_menu_keyboard();
    show_state_screen(bot, chat_id, state, user_id, text, Some(markup)).await
}

pub async fn show_articles_list_screen(
    bot: Bot,
    chat_id: ChatId,
    state: Arc<BotState>,
    user_id: UserId,
    status: &str,
    page: i64,
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
    let markup = keyboards::articles_list_keyboard(&paginated.articles, prev_cb, next_cb, "hub");
    show_state_screen(bot, chat_id, state, user_id, text, Some(markup)).await
}

pub async fn show_article_card_screen(
    bot: Bot,
    chat_id: ChatId,
    state: Arc<BotState>,
    user_id: UserId,
    article_id: i64,
) -> ResponseResult<()> {
    if article_id == 0 {
        show_hub(bot, chat_id, state, user_id).await?;
        return Ok(());
    }

    match state.bridge.get_article_content(article_id).await.tg_err() {
        Ok(content) => {
            let mut text = views::render_article_card(&content.article);
            
            // Show the user's current save format at the bottom of the card
            let current_fmt = {
                let map = state.user_formats.lock().await;
                *map.get(&user_id).unwrap_or(&state.config.default_format)
            }
            .to_string()
            .to_uppercase();
            text.push_str(&format!("\n<b>Текущий формат скачивания:</b> {}", current_fmt));

            let markup = keyboards::article_card_keyboard(
                content.article.id,
                &content.article.status,
                &content.article.url,
            );
            show_state_screen(bot, chat_id, state, user_id, text, Some(markup)).await?;
        }
        Err(_) => {
            show_error_state(bot, chat_id, state, user_id, "Материал не найден.").await?;
        }
    }
    Ok(())
}

pub async fn show_search_menu_screen(
    bot: Bot,
    chat_id: ChatId,
    state: Arc<BotState>,
    user_id: UserId,
) -> ResponseResult<()> {
    let session = state.get_search_session(user_id).await;
    
    // Calculate how many match active filters
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
            1,
            0,
        )
        .await
        .tg_err()?;
        
    let query_str = session.query.as_deref().unwrap_or("все");
    let status_str = match session.status.as_deref() {
        Some("read") => "прочитанные",
        Some("unread") => "непрочитанные",
        _ => "все",
    };
    let source_str = session.domain.as_deref().unwrap_or("все");
    let tag_str = session.tag.as_deref().unwrap_or("все");
    let rating_str = match session.rating {
        Some(r) => format!("{}", r),
        None => if session.no_rating { "без оценки".to_string() } else { "любая".to_string() },
    };
    let date_str = match session.date_added.as_deref() {
        Some("today") => "за сегодня",
        Some("week") => "за неделю",
        Some("month") => "за месяц",
        _ => "за всё время",
    };

    let text = format!(
        "🔎 <b>Поиск материалов</b>\n\n\
         <b>Запрос:</b> {}\n\
         <b>Статус:</b> {}\n\
         <b>Источник:</b> {}\n\
         <b>Тег:</b> {}\n\
         <b>Оценка:</b> {}\n\
         <b>Дата:</b> {}\n\n\
         <b>Найдено:</b> {}\n\n\
         Настройте фильтры кнопками ниже и нажмите <b>Искать</b>:",
        query_str, status_str, source_str, tag_str, rating_str, date_str, paginated.total_count
    );
    let markup = keyboards::search_menu_keyboard(&session);
    show_state_screen(bot, chat_id, state, user_id, text, Some(markup)).await
}

pub async fn show_tags_list_screen(
    bot: Bot,
    chat_id: ChatId,
    state: Arc<BotState>,
    user_id: UserId,
    page: i64,
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
    let markup = keyboards::tags_list_keyboard(&paged_tags, page, total_pages);
    show_state_screen(bot, chat_id, state, user_id, text, Some(markup)).await
}
