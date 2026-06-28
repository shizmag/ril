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
