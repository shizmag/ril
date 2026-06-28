use ril_daemon::domain::{
    ArticleSummary, DynamicsStats, ExtendedReadingStats, SourceStat, TagStat,
};
use ril_daemon::telegram::views::{
    escape_html, format_domain, render_article_card, render_articles_list, render_dynamics_stats,
    render_ratings_stats, render_sources_stats, render_stats_overview, render_tags_stats,
};
use std::collections::HashMap;

#[test]
fn test_html_escaping() {
    assert_eq!(escape_html("Hello World"), "Hello World");
    assert_eq!(escape_html("Tom & Jerry"), "Tom &amp; Jerry");
    assert_eq!(
        escape_html("<script>alert(1)</script>"),
        "&lt;script&gt;alert(1)&lt;/script&gt;"
    );
    assert_eq!(escape_html(""), "");
}

#[test]
fn test_format_domain() {
    assert_eq!(format_domain("https://habr.com/ru/post/12345/"), "habr.com");
    assert_eq!(format_domain("http://example.org/path?q=1"), "example.org");
    assert_eq!(format_domain("not-a-url"), "not-a-url");
    assert_eq!(format_domain(""), "");
}

#[test]
fn test_render_article_card() {
    let art = ArticleSummary {
        id: 42,
        url: "https://habr.com/post/1/?q=1&param=2".to_string(),
        title: "<b>Habr Title</b>".to_string(),
        added_at: "2026-06-28T12:00:00".to_string(),
        status: "unread".to_string(),
        file_path: "/mock/file.md".to_string(),
        word_count: 500,
        char_count: 3000,
        rating: Some(4),
        comment: Some("My comment with <html_tags>".to_string()),
        tags: vec!["rust".to_string(), "backend".to_string()],
        snippet: None,
    };
    let card = render_article_card(&art);
    // Assert URL, title and comment are escaped
    assert!(card.contains("href=\"https://habr.com/post/1/?q=1&amp;param=2\""));
    assert!(card.contains("&lt;b&gt;Habr Title&lt;/b&gt;"));
    assert!(card.contains("My comment with &lt;html_tags&gt;"));
    assert!(card.contains("⭐"));
    assert!(card.contains("<code>#rust</code>"));
    assert!(card.contains("<code>#backend</code>"));
    assert!(card.contains("<b>ID:</b> <code>42</code>"));
    assert!(card.contains("2026-06-28"));

    // Card without rating or comment
    let art2 = ArticleSummary {
        id: 43,
        url: "https://google.com".to_string(),
        title: "Google".to_string(),
        added_at: "2026-06-28T12:00:00".to_string(),
        status: "read".to_string(),
        file_path: "/mock/file.md".to_string(),
        word_count: 100,
        char_count: 600,
        rating: None,
        comment: None,
        tags: vec![],
        snippet: None,
    };
    let card2 = render_article_card(&art2);
    assert!(card2.contains("<i>нет оценки</i>"));
    assert!(!card2.contains("Комментарий:"));
    assert!(card2.contains("<i>нет тегов</i>"));
}

#[test]
fn test_render_articles_list() {
    let list = vec![
        ArticleSummary {
            id: 1,
            url: "https://a.com".to_string(),
            title: "A".to_string(),
            added_at: "2026-06-28T12:00:00".to_string(),
            status: "unread".to_string(),
            file_path: "/mock/1".to_string(),
            word_count: 100,
            char_count: 600,
            rating: Some(5),
            comment: None,
            tags: vec!["tag1".to_string()],
            snippet: None,
        },
        ArticleSummary {
            id: 2,
            url: "https://b.com".to_string(),
            title: "B".to_string(),
            added_at: "2026-06-28T12:00:00".to_string(),
            status: "read".to_string(),
            file_path: "/mock/2".to_string(),
            word_count: 200,
            char_count: 1200,
            rating: None,
            comment: None,
            tags: vec![],
            snippet: None,
        },
    ];

    let rendered = render_articles_list(&list, "My List", 0, 2);
    assert!(rendered.contains("My List"));
    assert!(rendered.contains("Страница 1 из 2"));
    assert!(rendered.contains("📖 <b>[1]</b> A"));
    assert!(rendered.contains("✅ <b>[2]</b> B"));
    assert!(rendered.contains("⭐⭐⭐⭐⭐"));

    // Empty list
    let rendered_empty = render_articles_list(&[], "Empty", 0, 0);
    assert!(rendered_empty.contains("Список пуст."));
}

#[test]
fn test_render_stats_overview() {
    let stats = ExtendedReadingStats {
        total_articles: 10,
        read_articles: 4,
        unread_articles: 6,
        total_words: 5000,
        read_words: 2000,
        unread_words: 3000,
        avg_words_per_article: 500.0,
        no_tags_count: 3,
        no_rating_count: 5,
        avg_rating: 4.2,
        top_articles: vec![ArticleSummary {
            id: 1,
            url: "https://a.com".to_string(),
            title: "Top 1".to_string(),
            added_at: "2026-06-28T12:00:00".to_string(),
            status: "read".to_string(),
            file_path: "/mock/1".to_string(),
            word_count: 100,
            char_count: 600,
            rating: Some(5),
            comment: None,
            tags: vec![],
            snippet: None,
        }],
    };

    let rendered = render_stats_overview(&stats);
    assert!(rendered.contains("Общая статистика библиотеки"));
    assert!(rendered.contains("прогресс 40.0%"));
    assert!(rendered.contains("Средняя оценка: <b>4.20</b> ⭐"));
    assert!(rendered.contains("Top 1 - ⭐⭐⭐⭐⭐"));
}

#[test]
fn test_render_sources_stats() {
    let sources = vec![
        SourceStat {
            domain: "habr.com".to_string(),
            count: 5,
        },
        SourceStat {
            domain: "medium.com".to_string(),
            count: 3,
        },
    ];
    let rendered = render_sources_stats(&sources);
    assert!(rendered.contains("Топ доменов и источников"));
    assert!(rendered.contains("<code>habr.com</code> — <b>5</b>"));

    let empty = render_sources_stats(&[]);
    assert!(empty.contains("Нет данных об источниках."));
}

#[test]
fn test_render_tags_stats() {
    let tags = vec![TagStat {
        tag: "rust".to_string(),
        count: 8,
    }];
    let rendered = render_tags_stats(&tags);
    assert!(rendered.contains("Популярные теги"));
    assert!(rendered.contains("<code>#rust</code> — <b>8</b>"));

    let empty = render_tags_stats(&[]);
    assert!(empty.contains("Тегов пока нет."));
}

#[test]
fn test_render_ratings_stats() {
    let mut map = HashMap::new();
    map.insert("5".to_string(), 3);
    map.insert("4".to_string(), 1);

    let rendered = render_ratings_stats(&map);
    assert!(rendered.contains("Распределение оценок"));
    assert!(rendered.contains("5 ⭐: <b>3</b> 🟩🟩🟩"));
    assert!(rendered.contains("4 ⭐: <b>1</b> 🟩"));
    assert!(rendered.contains("3 ⭐: <b>0</b>"));
}

#[test]
fn test_render_dynamics_stats() {
    let d = DynamicsStats {
        today: 1,
        week: 4,
        month: 12,
    };
    let rendered = render_dynamics_stats(&d);
    assert!(rendered.contains("Динамика добавления материалов"));
    assert!(rendered.contains("сегодня: <b>1</b>"));
    assert!(rendered.contains("неделю (7 дней): <b>4</b>"));
    assert!(rendered.contains("месяц (30 дней): <b>12</b>"));
}
