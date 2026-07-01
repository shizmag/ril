use crate::domain::{ArticleSummary, DynamicsStats, ExtendedReadingStats, SourceStat, TagStat};
use std::collections::HashMap;

pub fn escape_html(text: &str) -> String {
    text.replace('&', "&amp;")
        .replace('<', "&lt;")
        .replace('>', "&gt;")
}

pub fn format_domain(url: &str) -> String {
    if let Some(stripped) = url.strip_prefix("https://") {
        stripped.split('/').next().unwrap_or(stripped).to_string()
    } else if let Some(stripped) = url.strip_prefix("http://") {
        stripped.split('/').next().unwrap_or(stripped).to_string()
    } else {
        url.to_string()
    }
}

pub fn render_article_card(art: &ArticleSummary) -> String {
    let status_emoji = if art.status == "read" { "✅" } else { "📖" };
    let status_text = if art.status == "read" {
        "Прочитано"
    } else {
        "Не прочитано"
    };

    let title_escaped = escape_html(&art.title);
    let domain = format_domain(&art.url);
    let domain_escaped = escape_html(&domain);

    let read_time = (art.word_count as f64 / 200.0).ceil() as i64;

    let tags_text = if art.tags.is_empty() {
        "<i>нет тегов</i>".to_string()
    } else {
        art.tags
            .iter()
            .map(|t| format!("<code>#{}</code>", escape_html(t)))
            .collect::<Vec<_>>()
            .join(", ")
    };

    let rating_text = match art.rating {
        Some(r) => {
            let mut stars = String::new();
            for _ in 0..r {
                stars.push('⭐');
            }
            stars
        }
        None => "<i>нет оценки</i>".to_string(),
    };

    let date_clean = art.added_at.chars().take(10).collect::<String>();

    let mut card = format!(
        "{} <b>{}</b>\n\n\
         <b>Название:</b> {}\n\
         <b>Источник:</b> <a href=\"{}\">{}</a>\n\
         <b>Слов:</b> {} (~{} мин. чтения)\n\
         <b>Статус:</b> {} {}\n\
         <b>Теги:</b> {}\n\
         <b>Оценка:</b> {}\n\
         <b>ID:</b> <code>{}</code>\n\
         <b>Добавлен:</b> {}\n",
        status_emoji,
        if art.status == "read" {
            "Прочитано"
        } else {
            "Новый материал"
        },
        title_escaped,
        escape_html(&art.url),
        domain_escaped,
        art.word_count,
        read_time,
        status_emoji,
        status_text,
        tags_text,
        rating_text,
        art.id,
        date_clean
    );

    if let Some(ref comment) = art.comment {
        if !comment.is_empty() {
            card.push_str(&format!(
                "\n<b>Комментарий:</b>\n<i>{}</i>\n",
                escape_html(comment)
            ));
        }
    }

    card
}

pub fn render_articles_list(
    articles: &[ArticleSummary],
    title: &str,
    page: i64,
    total_pages: i64,
) -> String {
    let mut text = format!("📚 <b>{}</b>\n", title);
    if total_pages > 0 {
        text.push_str(&format!(
            "<i>Страница {} из {}</i>\n\n",
            page + 1,
            total_pages
        ));
    } else {
        text.push_str("\n");
    }

    if articles.is_empty() {
        text.push_str("Список пуст.\n");
    } else {
        for (i, a) in articles.iter().enumerate() {
            let status_emoji = if a.status == "read" { "✅" } else { "📖" };
            let status_text = if a.status == "read" {
                "прочитано"
            } else {
                "непрочитано"
            };
            let title_escaped = escape_html(&a.title);
            let domain = format_domain(&a.url);
            let read_time = (a.word_count as f64 / 200.0).ceil() as i64;

            let rating_str = match a.rating {
                Some(r) => format!(" · ⭐ {}", r),
                None => "".to_string(),
            };

            text.push_str(&format!(
                "<b>{}. {}</b>\n   {} · {} мин · {} {}{}\n\n",
                i + 1,
                title_escaped,
                domain,
                read_time,
                status_emoji,
                status_text,
                rating_str
            ));
        }
    }

    text
}

pub fn render_stats_overview(stats: &ExtendedReadingStats) -> String {
    let progress = if stats.total_articles > 0 {
        (stats.read_articles as f64 / stats.total_articles as f64) * 100.0
    } else {
        0.0
    };

    let total_mins = (stats.total_words as f64 / 200.0).ceil() as i64;
    let read_mins = (stats.read_words as f64 / 200.0).ceil() as i64;
    let unread_mins = (stats.unread_words as f64 / 200.0).ceil() as i64;

    let mut text = format!(
        "📊 <b>Общая статистика библиотеки</b>\n\n\
         Всего материалов: <b>{}</b>\n\
         Прочитано: <b>{}</b> (прогресс {:.1}%)\n\
         Не прочитано: <b>{}</b>\n\n\
         ⏱️ <b>Время на чтение:</b>\n\
           • Всего: <b>~{} мин.</b>\n\
           • Прочитано: <b>~{} мин.</b>\n\
           • Осталось: <b>~{} мин.</b>\n\n\
         Всего слов: <b>{}</b>\n\
         Слов прочитано: <b>{}</b>\n\
         Слов не прочитано: <b>{}</b>\n\
         В среднем на статью: <b>{:.0}</b> слов\n\n\
         Без тегов: <b>{}</b> материалов\n\
         Без оценки: <b>{}</b> материалов\n\
         Средняя оценка: <b>{:.2}</b> ⭐\n\n",
        stats.total_articles,
        stats.read_articles,
        progress,
        stats.unread_articles,
        total_mins,
        read_mins,
        unread_mins,
        stats.total_words,
        stats.read_words,
        stats.unread_words,
        stats.avg_words_per_article,
        stats.no_tags_count,
        stats.no_rating_count,
        stats.avg_rating
    );

    if !stats.top_articles.is_empty() {
        text.push_str("🏆 <b>Топ материалов по оценке:</b>\n");
        for (i, a) in stats.top_articles.iter().enumerate() {
            let stars = "⭐".repeat(a.rating.unwrap_or(5) as usize);
            text.push_str(&format!(
                "{}. [{}] {} - {}\n",
                i + 1,
                a.id,
                escape_html(&a.title),
                stars
            ));
        }
    }

    text
}

pub fn render_sources_stats(sources: &[SourceStat]) -> String {
    let mut text = "🌐 <b>Топ доменов и источников</b>\n\n".to_string();
    if sources.is_empty() {
        text.push_str("Нет данных об источниках.\n");
    } else {
        for (i, s) in sources.iter().enumerate() {
            text.push_str(&format!(
                "{}. <code>{}</code> — <b>{}</b> материалов\n",
                i + 1,
                escape_html(&s.domain),
                s.count
            ));
        }
    }
    text
}

pub fn render_tags_stats(tags: &[TagStat]) -> String {
    let mut text = "🏷 <b>Популярные теги</b>\n\n".to_string();
    if tags.is_empty() {
        text.push_str("Тегов пока нет.\n");
    } else {
        for (i, t) in tags.iter().enumerate() {
            text.push_str(&format!(
                "{}. <code>#{}</code> — <b>{}</b> материалов\n",
                i + 1,
                escape_html(&t.tag),
                t.count
            ));
        }
    }
    text
}

pub fn render_ratings_stats(ratings: &HashMap<String, i64>) -> String {
    let mut text = "⭐ <b>Распределение оценок</b>\n\n".to_string();
    for star in (1..=5).rev() {
        let count = ratings.get(&star.to_string()).unwrap_or(&0);
        let bar = "🟩".repeat(*count as usize);
        text.push_str(&format!("{} ⭐: <b>{}</b> {}\n", star, count, bar));
    }
    text
}

pub fn render_dynamics_stats(dynamics: &DynamicsStats) -> String {
    format!(
        "📅 <b>Динамика добавления материалов</b>\n\n\
         Добавлено за сегодня: <b>{}</b>\n\
         Добавлено за неделю (7 дней): <b>{}</b>\n\
         Добавлено за месяц (30 дней): <b>{}</b>\n",
        dynamics.today, dynamics.week, dynamics.month
    )
}
