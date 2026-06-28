mod common;

use common::make_mock_state;
use ril_daemon::telegram::helpers::validate_rating;

#[test]
fn test_rating_validation() {
    assert_eq!(validate_rating(Some(1)).unwrap(), Some(1));
    assert_eq!(validate_rating(Some(5)).unwrap(), Some(5));
    assert_eq!(validate_rating(None).unwrap(), None);

    assert!(validate_rating(Some(0)).is_err());
    assert!(validate_rating(Some(6)).is_err());
    assert!(validate_rating(Some(-1)).is_err());
}

#[tokio::test]
async fn test_rating_lifecycle() {
    let state = make_mock_state();

    // 1. Initial rating is None
    let art = state.bridge.get_article_content(1).await.unwrap().article;
    assert_eq!(art.rating, None);

    // 2. Set rating to 4
    let success = state.bridge.rate_article(1, Some(4)).await.unwrap();
    assert!(success);

    let art = state.bridge.get_article_content(1).await.unwrap().article;
    assert_eq!(art.rating, Some(4));

    // 3. Update rating to 5 (overwrites older rating)
    let success2 = state.bridge.rate_article(1, Some(5)).await.unwrap();
    assert!(success2);

    let art = state.bridge.get_article_content(1).await.unwrap().article;
    assert_eq!(art.rating, Some(5));

    // 4. Reset rating (set to None)
    let success3 = state.bridge.rate_article(1, None).await.unwrap();
    assert!(success3);

    let art = state.bridge.get_article_content(1).await.unwrap().article;
    assert_eq!(art.rating, None);
}
