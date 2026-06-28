use ril_daemon::telegram::helpers::calculate_pages;
use ril_daemon::telegram::keyboards::pagination_keyboard;

#[test]
fn test_pagination_first_page() {
    // 0-indexed: page 0 out of 5 total pages (total_count=50, limit=10)
    let (total, prev, next) = calculate_pages(0, 50, 10);
    assert_eq!(total, 5);
    assert_eq!(prev, None);
    assert_eq!(next, Some(1));

    let kb = pagination_keyboard(None, Some("next".to_string()), "home");
    // Assert next button is present, prev is not
    let rows = kb.inline_keyboard;
    assert_eq!(rows.len(), 2); // row of arrows, row of home
    assert_eq!(rows[0].len(), 1);
    assert_eq!(rows[0][0].text, "➡️ Далее");
}

#[test]
fn test_pagination_middle_page() {
    // page 2 out of 5 (page index 2 is third page)
    let (total, prev, next) = calculate_pages(2, 50, 10);
    assert_eq!(total, 5);
    assert_eq!(prev, Some(1));
    assert_eq!(next, Some(3));

    let kb = pagination_keyboard(Some("prev".to_string()), Some("next".to_string()), "home");
    let rows = kb.inline_keyboard;
    assert_eq!(rows[0].len(), 2);
    assert_eq!(rows[0][0].text, "⬅️ Назад");
    assert_eq!(rows[0][1].text, "➡️ Далее");
}

#[test]
fn test_pagination_last_page() {
    // page 4 out of 5
    let (total, prev, next) = calculate_pages(4, 50, 10);
    assert_eq!(total, 5);
    assert_eq!(prev, Some(3));
    assert_eq!(next, None);

    let kb = pagination_keyboard(Some("prev".to_string()), None, "home");
    let rows = kb.inline_keyboard;
    assert_eq!(rows[0].len(), 1);
    assert_eq!(rows[0][0].text, "⬅️ Назад");
}

#[test]
fn test_pagination_empty_list() {
    let (total, prev, next) = calculate_pages(0, 0, 10);
    assert_eq!(total, 0);
    assert_eq!(prev, None);
    assert_eq!(next, None);
}

#[test]
fn test_pagination_page_size_zero() {
    // Should handle limit <= 0 without division by zero panic
    let (total, prev, next) = calculate_pages(0, 50, 0);
    assert_eq!(total, 0);
    assert_eq!(prev, None);
    assert_eq!(next, None);

    let (total_neg, prev_neg, next_neg) = calculate_pages(0, 50, -5);
    assert_eq!(total_neg, 0);
    assert_eq!(prev_neg, None);
    assert_eq!(next_neg, None);
}

#[test]
fn test_pagination_page_out_of_bounds() {
    // page < 0 should map to page 0
    let (total_under, prev_under, next_under) = calculate_pages(-5, 50, 10);
    assert_eq!(total_under, 5);
    assert_eq!(prev_under, None);
    assert_eq!(next_under, Some(1));

    // page >= total_pages should map to last page
    let (total_over, prev_over, next_over) = calculate_pages(10, 50, 10);
    assert_eq!(total_over, 5);
    assert_eq!(prev_over, Some(3));
    assert_eq!(next_over, None);
}
