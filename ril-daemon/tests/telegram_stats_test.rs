mod common;

use common::make_mock_state;
use ril_daemon::domain::SaveFormat;

#[tokio::test]
async fn test_stats_lifecycle() {
    let state = make_mock_state();

    // 1. Initial stats (1 article, status unread, word count 500)
    let stats = state.bridge.get_extended_stats().await.unwrap();
    assert_eq!(stats.total_articles, 1);
    assert_eq!(stats.unread_articles, 1);
    assert_eq!(stats.read_articles, 0);
    assert_eq!(stats.total_words, 500);
    assert_eq!(stats.avg_words_per_article, 500.0);
    assert_eq!(stats.no_tags_count, 1);
    assert_eq!(stats.no_rating_count, 1);
    assert_eq!(stats.avg_rating, 0.0);

    // 2. Add an article with 300 words
    state
        .bridge
        .process_url("http://example.com/2", SaveFormat::Html, false, false, false)
        .await
        .unwrap();

    let stats2 = state.bridge.get_extended_stats().await.unwrap();
    assert_eq!(stats2.total_articles, 2);
    assert_eq!(stats2.unread_articles, 2);
    assert_eq!(stats2.total_words, 700); // 500 (id 1) + 200 (id 2, since word_count is 100 * id in mock)
    assert_eq!(stats2.avg_words_per_article, 350.0);

    // 3. Mark article 2 as read
    state.bridge.mark_article_read(2).await.unwrap();

    let stats3 = state.bridge.get_extended_stats().await.unwrap();
    assert_eq!(stats3.total_articles, 2);
    assert_eq!(stats3.read_articles, 1);
    assert_eq!(stats3.unread_articles, 1);
    assert_eq!(stats3.read_words, 200);
    assert_eq!(stats3.unread_words, 500);

    // 4. Set rating for article 2 to 5 and article 1 to 4
    state.bridge.rate_article(2, Some(5)).await.unwrap();
    state.bridge.rate_article(1, Some(4)).await.unwrap();

    let stats4 = state.bridge.get_extended_stats().await.unwrap();
    assert_eq!(stats4.no_rating_count, 0);
    assert_eq!(stats4.avg_rating, 4.5); // (5 + 4) / 2
    assert_eq!(stats4.top_articles.len(), 2);
    assert_eq!(stats4.top_articles[0].id, 2); // rating 5 is first

    // 5. Delete article 1
    state.bridge.delete_article(1).await.unwrap();

    let stats5 = state.bridge.get_extended_stats().await.unwrap();
    assert_eq!(stats5.total_articles, 1);
    assert_eq!(stats5.read_articles, 1);
    assert_eq!(stats5.unread_articles, 0);
    assert_eq!(stats5.avg_rating, 5.0);
}
