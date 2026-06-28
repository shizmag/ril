use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "lowercase")]
pub enum SaveFormat {
    Markdown,
    Html,
    Epub,
}

impl std::fmt::Display for SaveFormat {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            SaveFormat::Markdown => write!(f, "markdown"),
            SaveFormat::Html => write!(f, "html"),
            SaveFormat::Epub => write!(f, "epub"),
        }
    }
}

impl std::str::FromStr for SaveFormat {
    type Err = String;

    fn from_str(s: &str) -> Result<Self, Self::Err> {
        match s.to_lowercase().as_str() {
            "markdown" | "md" => Ok(SaveFormat::Markdown),
            "html" => Ok(SaveFormat::Html),
            "epub" => Ok(SaveFormat::Epub),
            _ => Err(format!("Unknown save format: {}", s)),
        }
    }
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "lowercase")]
pub enum ArticleStatus {
    Read,
    Unread,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ArticleSummary {
    pub id: i64,
    pub url: String,
    pub title: String,
    pub added_at: String,
    pub status: String,
    pub file_path: String,
    pub word_count: i64,
    pub char_count: i64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SearchResult {
    pub id: i64,
    pub url: String,
    pub title: String,
    pub added_at: String,
    pub status: String,
    pub file_path: String,
    pub word_count: i64,
    pub snippet: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ProcessingResult {
    pub id: i64,
    pub url: String,
    pub title: String,
    pub file_path: String,
    pub word_count: i64,
    pub char_count: i64,
    pub status: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ReadingStats {
    pub total_articles: i64,
    pub read_articles: i64,
    pub unread_articles: i64,
    pub total_words: i64,
    pub read_words: i64,
    pub unread_words: i64,
    pub avg_words_per_article: f64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ArticleContent {
    pub article: ArticleSummary,
    pub content: String,
}
