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
        "Read"
    } else {
        "Unread"
    };

    let title_escaped = escape_html(&art.title);
    let domain = format_domain(&art.url);
    let domain_escaped = escape_html(&domain);

    let read_time = (art.word_count as f64 / 200.0).ceil() as i64;

    let tags_text = if art.tags.is_empty() {
        "<i>no tags</i>".to_string()
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
        None => "<i>no rating</i>".to_string(),
    };

    let date_clean = art.added_at.chars().take(10).collect::<String>();

    let mut card = format!(
        "{} <b>{}</b>\n\n\
         <b>Title:</b> {}\n\
         <b>Source:</b> <a href=\"{}\">{}</a>\n\
         <b>Words:</b> {} (~{} min read)\n\
         <b>Status:</b> {} {}\n\
         <b>Tags:</b> {}\n\
         <b>Rating:</b> {}\n\
         <b>ID:</b> <code>{}</code>\n\
         <b>Added:</b> {}\n",
        status_emoji,
        if art.status == "read" {
            "Read"
        } else {
            "New article"
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
                "\n<b>Comment:</b>\n<i>{}</i>\n",
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
            "<i>Page {} of {}</i>\n\n",
            page + 1,
            total_pages
        ));
    } else {
        text.push_str("\n");
    }

    if articles.is_empty() {
        text.push_str("List is empty.\n");
    } else {
        for (i, a) in articles.iter().enumerate() {
            let status_emoji = if a.status == "read" { "✅" } else { "📖" };
            let status_text = if a.status == "read" {
                "read"
            } else {
                "unread"
            };
            let title_escaped = escape_html(&a.title);
            let domain = format_domain(&a.url);
            let read_time = (a.word_count as f64 / 200.0).ceil() as i64;

            let rating_str = match a.rating {
                Some(r) => format!(" · ⭐ {}", r),
                None => "".to_string(),
            };

            text.push_str(&format!(
                "<b>{}. {}</b>\n   {} · {} min · {} {}{}\n\n",
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
        "📊 <b>General Library Statistics</b>\n\n\
         Total articles: <b>{}</b>\n\
         Read: <b>{}</b> (progress {:.1}%)\n\
         Unread: <b>{}</b>\n\n\
         ⏱️ <b>Reading Time:</b>\n\
           • Total: <b>~{} min.</b>\n\
           • Read: <b>~{} min.</b>\n\
           • Remaining: <b>~{} min.</b>\n\n\
         Total words: <b>{}</b>\n\
         Words read: <b>{}</b>\n\
         Words unread: <b>{}</b>\n\
         Average per article: <b>{:.0}</b> words\n\n\
         Without tags: <b>{}</b> articles\n\
         Without rating: <b>{}</b> articles\n\
         Average rating: <b>{:.2}</b> ⭐\n\n",
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
        text.push_str("🏆 <b>Top articles by rating:</b>\n");
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
    let mut text = "🌐 <b>Top domains and sources</b>\n\n".to_string();
    if sources.is_empty() {
        text.push_str("No data on sources.\n");
    } else {
        for (i, s) in sources.iter().enumerate() {
            text.push_str(&format!(
                "{}. <code>{}</code> — <b>{}</b> articles\n",
                i + 1,
                escape_html(&s.domain),
                s.count
            ));
        }
    }
    text
}

pub fn render_tags_stats(tags: &[TagStat]) -> String {
    let mut text = "🏷 <b>Popular tags</b>\n\n".to_string();
    if tags.is_empty() {
        text.push_str("No tags yet.\n");
    } else {
        for (i, t) in tags.iter().enumerate() {
            text.push_str(&format!(
                "{}. <code>#{}</code> — <b>{}</b> articles\n",
                i + 1,
                escape_html(&t.tag),
                t.count
            ));
        }
    }
    text
}

pub fn render_ratings_stats(ratings: &HashMap<String, i64>) -> String {
    let mut text = "⭐ <b>Rating distribution</b>\n\n".to_string();
    for star in (1..=5).rev() {
        let count = ratings.get(&star.to_string()).unwrap_or(&0);
        let bar = "🟩".repeat(*count as usize);
        text.push_str(&format!("{} ⭐: <b>{}</b> {}\n", star, count, bar));
    }
    text
}

pub fn render_dynamics_stats(dynamics: &DynamicsStats) -> String {
    format!(
        "📅 <b>Article addition dynamics</b>\n\n\
         Added today: <b>{}</b>\n\
         Added this week (7 days): <b>{}</b>\n\
         Added this month (30 days): <b>{}</b>\n",
        dynamics.today, dynamics.week, dynamics.month
    )
}
