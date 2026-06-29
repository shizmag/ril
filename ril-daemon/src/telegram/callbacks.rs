use crate::telegram::state::{BotState, PendingState};
use crate::telegram::{keyboards, views, MapTgError};
use std::sync::Arc;
use teloxide::prelude::*;
use teloxide::types::{InlineKeyboardButton, InlineKeyboardMarkup, InputFile, Message, UserId};

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

    let mut custom_ack = None;

    if data == "hub" {
        super::helpers::show_hub(bot.clone(), msg.chat.id, state.clone(), user.id).await?;
    } else if data.starts_with("list:") {
        let parts: Vec<&str> = data.split(':').collect();
        if parts.len() == 3 {
            let status = parts[1];
            let page: i64 = parts[2].parse().unwrap_or(0);
            super::helpers::show_articles_list_screen(bot.clone(), msg.chat.id, state.clone(), user.id, status, page).await?;
        }
    } else if data.starts_with("get_file:") {
        let id: i64 = data
            .split(':')
            .nth(1)
            .and_then(|s| s.parse().ok())
            .unwrap_or(0);
        let user_format = {
            let map = state.user_formats.lock().await;
            *map.get(&user.id).unwrap_or(&state.config.default_format)
        };
        match state.bridge.export_article(id, user_format).await.tg_err() {
            Ok(export_res) => {
                let file_path = std::path::Path::new(&export_res.file_path);
                if file_path.exists() {
                    let doc = InputFile::file(file_path);
                    let caption = format!(
                        "📥 <b>{} [{}]</b>\n\n\
                         <b>Формат:</b> {}\n\
                         <b>Слов:</b> {} (~{} мин. чтения)\n\
                         <b>Статус:</b> {} {}\n\
                         <b>Оценка:</b> {}",
                        views::escape_html(&export_res.title),
                        export_res.article_id,
                        export_res.format.to_uppercase(),
                        export_res.word_count,
                        (export_res.word_count as f64 / 200.0).ceil() as i64,
                        if export_res.status == "read" { "✅" } else { "📖" },
                        if export_res.status == "read" { "Прочитано" } else { "Не прочитано" },
                        match export_res.rating {
                            Some(r) => "⭐".repeat(r as usize),
                            None => "нет оценки".to_string(),
                        }
                    );
                    let markup = keyboards::document_keyboard(id, &export_res.status);
                    if let Some(state_msg_id) = state.clear_state_message(user.id).await {
                        let _ = bot.delete_message(msg.chat.id, teloxide::types::MessageId(state_msg_id)).await;
                    }
                    let sent = bot.send_document(msg.chat.id, doc)
                        .caption(caption)
                        .reply_markup(markup)
                        .parse_mode(teloxide::types::ParseMode::Html)
                        .await?;
                    state.set_state_message(user.id, sent.id.0).await;
                } else {
                    super::helpers::show_error_state(bot.clone(), msg.chat.id, state.clone(), user.id, "Файл не найден на диске.").await?;
                }
            }
            Err(e) => {
                super::helpers::show_error_state(bot.clone(), msg.chat.id, state.clone(), user.id, &format!("Ошибка при экспорте статьи: {}", e)).await?;
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
            custom_ack = Some("Статус обновлен".to_string());
            
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
                state.clear_state_message(user.id).await;
            }
        }
    } else if data.starts_with("rate_doc:") {
        let parts: Vec<&str> = data.split(':').collect();
        if parts.len() == 3 {
            let id: i64 = parts[1].parse().unwrap_or(0);
            let val: i32 = parts[2].parse().unwrap_or(0);
            state.bridge.rate_article(id, Some(val)).await.tg_err()?;
            custom_ack = Some("Оценка выставлена".to_string());
            
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
        super::helpers::show_article_card_screen(bot.clone(), msg.chat.id, state.clone(), user.id, id).await?;
    } else if data.starts_with("read:") {
        let id: i64 = data
            .split(':')
            .nth(1)
            .and_then(|s| s.parse().ok())
            .unwrap_or(0);
        state.bridge.mark_article_read(id).await.tg_err()?;
        custom_ack = Some("Статус обновлен".to_string());
        super::helpers::show_article_card_screen(bot.clone(), msg.chat.id, state.clone(), user.id, id).await?;
    } else if data.starts_with("unread:") {
        let id: i64 = data
            .split(':')
            .nth(1)
            .and_then(|s| s.parse().ok())
            .unwrap_or(0);
        state.bridge.mark_article_unread(id).await.tg_err()?;
        custom_ack = Some("Статус обновлен".to_string());
        super::helpers::show_article_card_screen(bot.clone(), msg.chat.id, state.clone(), user.id, id).await?;
    } else if data.starts_with("art_del:") {
        let id: i64 = data
            .split(':')
            .nth(1)
            .and_then(|s| s.parse().ok())
            .unwrap_or(0);
        show_delete_confirm(bot.clone(), msg, id, state.clone()).await?;
    } else if data.starts_with("art_del_conf:") {
        let id: i64 = data
            .split(':')
            .nth(1)
            .and_then(|s| s.parse().ok())
            .unwrap_or(0);
        state.bridge.delete_article(id).await.tg_err()?;
        custom_ack = Some("Материал удален".to_string());
        let text = "🗑 <b>Материал успешно удален.</b>";
        let markup = keyboards::back_to_hub_keyboard();
        super::helpers::show_state_screen(bot.clone(), msg.chat.id, state.clone(), user.id, text.to_string(), Some(markup)).await?;
    } else if data.starts_with("art_rate:") {
        let id: i64 = data
            .split(':')
            .nth(1)
            .and_then(|s| s.parse().ok())
            .unwrap_or(0);
        let text = "⭐ <b>Пожалуйста, выберите оценку для материала:</b>";
        let markup = keyboards::rating_keyboard(id);
        super::helpers::show_state_screen(bot.clone(), msg.chat.id, state.clone(), user.id, text.to_string(), Some(markup)).await?;
    } else if data.starts_with("rate_set:") {
        let parts: Vec<&str> = data.split(':').collect();
        if parts.len() == 3 {
            let id: i64 = parts[1].parse().unwrap_or(0);
            let val: i32 = parts[2].parse().unwrap_or(0);
            let rating = if val == 0 { None } else { Some(val) };
            state.bridge.rate_article(id, rating).await.tg_err()?;
            custom_ack = Some("Оценка выставлена".to_string());

            if rating.is_some() {
                state
                    .set_pending_state(user.id, PendingState::WaitingForComment { article_id: id })
                    .await;
                let text = "⭐ <b>Оценка успешно установлена!</b>\n\nНапишите текстовый комментарий к этому материалу и отправьте его в ответном сообщении. Если комментарий не нужен, просто нажмите кнопку ниже.";
                let markup = keyboards::back_to_article_keyboard(id);
                super::helpers::show_state_screen(bot.clone(), msg.chat.id, state.clone(), user.id, text.to_string(), Some(markup)).await?;
            } else {
                super::helpers::show_article_card_screen(bot.clone(), msg.chat.id, state.clone(), user.id, id).await?;
            }
        }
    } else if data.starts_with("art_comm:") {
        let id: i64 = data
            .split(':')
            .nth(1)
            .and_then(|s| s.parse().ok())
            .unwrap_or(0);
        show_comment_menu(bot.clone(), msg, id, state.clone()).await?;
    } else if data.starts_with("comm_set:") {
        let id: i64 = data
            .split(':')
            .nth(1)
            .and_then(|s| s.parse().ok())
            .unwrap_or(0);
        state
            .set_pending_state(user.id, PendingState::WaitingForComment { article_id: id })
            .await;
        let text = "💬 <b>Пожалуйста, отправьте ваш комментарий следующим сообщением:</b>";
        let markup = keyboards::back_to_article_keyboard(id);
        super::helpers::show_state_screen(bot.clone(), msg.chat.id, state.clone(), user.id, text.to_string(), Some(markup)).await?;
    } else if data.starts_with("comm_del:") {
        let id: i64 = data
            .split(':')
            .nth(1)
            .and_then(|s| s.parse().ok())
            .unwrap_or(0);
        state.bridge.set_article_comment(id, None).await.tg_err()?;
        custom_ack = Some("Комментарий удален".to_string());
        super::helpers::show_article_card_screen(bot.clone(), msg.chat.id, state.clone(), user.id, id).await?;
    } else if data.starts_with("art_tags:") {
        let id: i64 = data
            .split(':')
            .nth(1)
            .and_then(|s| s.parse().ok())
            .unwrap_or(0);
        show_article_tags(bot.clone(), msg, id, state.clone()).await?;
    } else if data.starts_with("tag_add:") {
        let id: i64 = data
            .split(':')
            .nth(1)
            .and_then(|s| s.parse().ok())
            .unwrap_or(0);
        state
            .set_pending_state(user.id, PendingState::WaitingForTag { article_id: id })
            .await;
        let text = "🏷 <b>Пожалуйста, отправьте название тега (или несколько через запятую):</b>";
        let markup = keyboards::back_to_article_keyboard(id);
        super::helpers::show_state_screen(bot.clone(), msg.chat.id, state.clone(), user.id, text.to_string(), Some(markup)).await?;
    } else if data.starts_with("tag_rem:") {
        let parts: Vec<&str> = data.splitn(3, ':').collect();
        if parts.len() == 3 {
            let id: i64 = parts[1].parse().unwrap_or(0);
            let tag = parts[2];
            state.bridge.remove_tag(id, tag).await.tg_err()?;
            custom_ack = Some("Тег удален".to_string());
            show_article_tags(bot.clone(), msg, id, state.clone()).await?;
        }
    } else if data.starts_with("tags_list:") {
        let page: i64 = data
            .split(':')
            .nth(1)
            .and_then(|s| s.parse().ok())
            .unwrap_or(0);
        super::helpers::show_tags_list_screen(bot.clone(), msg.chat.id, state.clone(), user.id, page).await?;
    } else if data.starts_with("stag:") {
        let parts: Vec<&str> = data.splitn(3, ':').collect();
        if parts.len() == 3 {
            let tag = parts[1];
            let page: i64 = parts[2].parse().unwrap_or(0);
            show_articles_by_tag(bot.clone(), msg, tag, page, state.clone()).await?;
        }
    } else if data == "ratings_list" {
        show_ratings_list(bot.clone(), msg, state.clone()).await?;
    } else if data.starts_with("srate:") {
        let parts: Vec<&str> = data.split(':').collect();
        if parts.len() == 3 {
            let rating: i32 = parts[1].parse().unwrap_or(0);
            let page: i64 = parts[2].parse().unwrap_or(0);
            show_articles_by_rating(bot.clone(), msg, rating, page, state.clone()).await?;
        }
    } else if data.starts_with("stats:") {
        let section = data.split(':').nth(1).unwrap_or("overview");
        super::helpers::show_stats_screen(bot.clone(), msg.chat.id, state.clone(), user.id, section).await?;
    } else if data == "settings" {
        super::helpers::show_settings_screen(bot.clone(), msg.chat.id, state.clone(), user.id).await?;
    } else if data.starts_with("set_fmt:") {
        let fmt_str = data.split(':').nth(1).unwrap_or("markdown");
        if let Ok(fmt) = fmt_str.parse::<crate::domain::SaveFormat>() {
            let mut map = state.user_formats.lock().await;
            map.insert(user.id, fmt);
            custom_ack = Some(format!("Формат скачивания изменен на {}", fmt.to_string().to_uppercase()));
        }
        super::helpers::show_settings_screen(bot.clone(), msg.chat.id, state.clone(), user.id).await?;
    } else if data == "open_last_imported" {
        let ids = state.get_last_imported(user.id).await;
        let mut articles = vec![];
        for id in ids {
            if let Ok(content) = state.bridge.get_article_content(id).await {
                articles.push(content.article);
            }
        }
        let text = views::render_articles_list(
            &articles,
            "Последние добавленные материалы",
            0,
            1,
        );
        let markup = keyboards::articles_list_keyboard(&articles, None, None, "hub");
        super::helpers::show_state_screen(bot.clone(), msg.chat.id, state.clone(), user.id, text, Some(markup)).await?;
    } else if data == "show_import_errors" {
        let errs = state.get_last_errors(user.id).await;
        let mut text = "⚠️ <b>Ошибки при последнем импорте:</b>\n\n".to_string();
        if errs.is_empty() {
            text.push_str("Ошибок не обнаружено.");
        } else {
            for (i, err) in errs.iter().enumerate() {
                text.push_str(&format!("{}. {}\n", i + 1, views::escape_html(err)));
            }
        }
        let markup = keyboards::back_to_hub_keyboard();
        super::helpers::show_state_screen(bot.clone(), msg.chat.id, state.clone(), user.id, text, Some(markup)).await?;
    } else if data == "reset_lib_prompt" {
        let text = "⚠️ <b>ВНИМАНИЕ!</b>\n\nЭто действие безвозвратно удалит ВСЕ сохраненные материалы, файлы и записи в базе данных.\n\nВы уверены?";
        let markup = keyboards::reset_lib_confirm_keyboard();
        super::helpers::show_state_screen(bot.clone(), msg.chat.id, state.clone(), user.id, text.to_string(), Some(markup)).await?;
    } else if data == "reset_lib_confirm" {
        state.bridge.reset_library().await.tg_err()?;
        custom_ack = Some("Библиотека сброшена".to_string());
        let text = "✅ <b>Библиотека полностью очищена.</b>";
        let markup = keyboards::back_to_hub_keyboard();
        super::helpers::show_state_screen(bot.clone(), msg.chat.id, state.clone(), user.id, text.to_string(), Some(markup)).await?;
    } else if data == "search" {
        super::helpers::show_search_menu_screen(bot.clone(), msg.chat.id, state.clone(), user.id).await?;
    } else if data == "sf_query" {
        state
            .set_pending_state(user.id, PendingState::WaitingForSearchQuery)
            .await;
        let text = "🔎 <b>Введите поисковый запрос (текст или ключевые слова):</b>";
        let markup = keyboards::back_to_article_keyboard(0);
        super::helpers::show_state_screen(bot.clone(), msg.chat.id, state.clone(), user.id, text.to_string(), Some(markup)).await?;
    } else if data == "sf_domain" {
        state
            .set_pending_state(user.id, PendingState::WaitingForFilterDomain)
            .await;
        let text = "🌐 <b>Введите домен сайта для фильтрации (например, habr.com или medium.com):</b>";
        let markup = keyboards::back_to_article_keyboard(0);
        super::helpers::show_state_screen(bot.clone(), msg.chat.id, state.clone(), user.id, text.to_string(), Some(markup)).await?;
    } else if data == "sf_tag" {
        show_search_tag_select(bot.clone(), msg, state.clone()).await?;
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
        super::helpers::show_search_menu_screen(bot.clone(), msg.chat.id, state.clone(), user.id).await?;
    } else if data == "sf_status" {
        let text = "📝 <b>Выберите статус прочтения для фильтрации:</b>";
        let markup = keyboards::search_status_select_keyboard();
        super::helpers::show_state_screen(bot.clone(), msg.chat.id, state.clone(), user.id, text.to_string(), Some(markup)).await?;
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
        super::helpers::show_search_menu_screen(bot.clone(), msg.chat.id, state.clone(), user.id).await?;
    } else if data == "sf_rating" {
        let text = "⭐ <b>Выберите оценку для фильтрации:</b>";
        let markup = keyboards::search_rating_select_keyboard();
        super::helpers::show_state_screen(bot.clone(), msg.chat.id, state.clone(), user.id, text.to_string(), Some(markup)).await?;
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
        super::helpers::show_search_menu_screen(bot.clone(), msg.chat.id, state.clone(), user.id).await?;
    } else if data == "sf_date" {
        let text = "📅 <b>Выберите интервал добавления:</b>";
        let markup = keyboards::search_date_select_keyboard();
        super::helpers::show_state_screen(bot.clone(), msg.chat.id, state.clone(), user.id, text.to_string(), Some(markup)).await?;
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
        super::helpers::show_search_menu_screen(bot.clone(), msg.chat.id, state.clone(), user.id).await?;
    } else if data == "sf_reset" {
        state.clear_search_session(user.id).await;
        super::helpers::show_search_menu_screen(bot.clone(), msg.chat.id, state.clone(), user.id).await?;
    } else if data.starts_with("sf_run:") {
        let page: i64 = data
            .split(':')
            .nth(1)
            .and_then(|s| s.parse().ok())
            .unwrap_or(0);
        run_search(bot.clone(), msg, user.id, page, state).await?;
    }

    if let Some(text) = custom_ack {
        let _ = bot.answer_callback_query(q.id).text(text).await;
    } else {
        let _ = bot.answer_callback_query(q.id).await;
    }

    Ok(())
}

async fn show_delete_confirm(
    bot: Bot,
    msg: &Message,
    id: i64,
    state: Arc<BotState>,
) -> ResponseResult<()> {
    let user_id = msg.from().map(|u| u.id).unwrap_or(UserId(0));
    let content = state.bridge.get_article_content(id).await.tg_err()?;
    let text = format!(
        "⚠️ <b>Вы уверены, что хотите удалить этот материал?</b>\n\n<b>{}</b>",
        views::escape_html(&content.article.title)
    );
    let markup = keyboards::delete_confirm_keyboard(id);
    super::helpers::show_state_screen(bot, msg.chat.id, state, user_id, text, Some(markup)).await
}

async fn show_comment_menu(
    bot: Bot,
    msg: &Message,
    id: i64,
    state: Arc<BotState>,
) -> ResponseResult<()> {
    let user_id = msg.from().map(|u| u.id).unwrap_or(UserId(0));
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
    let markup = keyboards::comment_keyboard(id);
    super::helpers::show_state_screen(bot, msg.chat.id, state, user_id, text, Some(markup)).await
}

async fn show_article_tags(
    bot: Bot,
    msg: &Message,
    id: i64,
    state: Arc<BotState>,
) -> ResponseResult<()> {
    let user_id = msg.from().map(|u| u.id).unwrap_or(UserId(0));
    let content = state.bridge.get_article_content(id).await.tg_err()?;
    let mut rows = vec![];
    rows.push(vec![InlineKeyboardButton::callback(
        "➕ Добавить тег",
        format!("tag_add:{}", id),
    )]);

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
    super::helpers::show_state_screen(bot, msg.chat.id, state, user_id, text, Some(markup)).await
}

async fn show_articles_by_tag(
    bot: Bot,
    msg: &Message,
    tag: &str,
    page: i64,
    state: Arc<BotState>,
) -> ResponseResult<()> {
    let user_id = msg.from().map(|u| u.id).unwrap_or(UserId(0));
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
    let markup = keyboards::pagination_keyboard(
        prev_cb,
        next_cb,
        "tags_list:0",
    );
    super::helpers::show_state_screen(bot, msg.chat.id, state, user_id, text, Some(markup)).await
}

async fn show_articles_by_rating(
    bot: Bot,
    msg: &Message,
    rating: i32,
    page: i64,
    state: Arc<BotState>,
) -> ResponseResult<()> {
    let user_id = msg.from().map(|u| u.id).unwrap_or(UserId(0));
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
    let markup = keyboards::pagination_keyboard(
        prev_cb,
        next_cb,
        "ratings_list",
    );
    super::helpers::show_state_screen(bot, msg.chat.id, state, user_id, text, Some(markup)).await
}

async fn show_search_tag_select(
    bot: Bot,
    msg: &Message,
    state: Arc<BotState>,
) -> ResponseResult<()> {
    let user_id = msg.from().map(|u| u.id).unwrap_or(UserId(0));
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

    super::helpers::show_state_screen(bot, msg.chat.id, state, user_id, "🏷 <b>Выберите тег для фильтрации:</b>".to_string(), Some(markup)).await
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
    let markup = keyboards::articles_list_keyboard(&paginated.articles, prev_cb, next_cb, "search");
    super::helpers::show_state_screen(bot, msg.chat.id, state, user_id, text, Some(markup)).await
}

async fn show_ratings_list(bot: Bot, msg: &Message, state: Arc<BotState>) -> ResponseResult<()> {
    let user_id = msg.from().map(|u| u.id).unwrap_or(UserId(0));
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

    super::helpers::show_state_screen(bot, msg.chat.id, state, user_id, "⭐ <b>Выберите оценку для фильтрации:</b>".to_string(), Some(markup)).await
}
