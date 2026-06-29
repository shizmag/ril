use crate::config::Config;
use crate::domain::{
    ArticleContent, ArticleSummary, ProcessingResult, ReadingStats, SaveFormat, SearchResult,
};
use crate::error::{DaemonError, Result};
use serde::{Deserialize, Serialize};
use std::sync::Arc;
use tokio::sync::Mutex;

#[derive(Serialize)]
struct BridgeRequest<'a> {
    command: &'a str,
    args: serde_json::Value,
}

#[derive(Deserialize)]
struct BridgeResponse<T> {
    ok: bool,
    data: Option<T>,
    error: Option<BridgeErrorPayload>,
}

#[derive(Deserialize)]
struct BridgeErrorPayload {
    code: String,
    message: String,
    details: String,
}

pub struct MockState {
    pub articles: Vec<ArticleSummary>,
    pub stats: ReadingStats,
}

impl MockState {
    pub fn new() -> Self {
        let mut s = MockState {
            articles: vec![],
            stats: ReadingStats {
                total_articles: 0,
                read_articles: 0,
                unread_articles: 0,
                total_words: 0,
                read_words: 0,
                unread_words: 0,
                avg_words_per_article: 0.0,
            },
        };
        // Add one initial dummy article for quick verification in health-check/tests
        s.articles.push(ArticleSummary {
            id: 1,
            url: "http://example.com/initial".to_string(),
            title: "Initial Mock Article".to_string(),
            added_at: "2026-06-28T12:00:00".to_string(),
            status: "unread".to_string(),
            file_path: "/mock/library/1.md".to_string(),
            word_count: 500,
            char_count: 3000,
            rating: None,
            comment: None,
            tags: vec![],
            snippet: None,
        });
        s.recalc_stats();
        s
    }

    pub fn recalc_stats(&mut self) {
        let total = self.articles.len() as i64;
        let read = self.articles.iter().filter(|a| a.status == "read").count() as i64;
        let unread = total - read;
        let total_w: i64 = self.articles.iter().map(|a| a.word_count).sum();
        let read_w: i64 = self
            .articles
            .iter()
            .filter(|a| a.status == "read")
            .map(|a| a.word_count)
            .sum();
        self.stats = ReadingStats {
            total_articles: total,
            read_articles: read,
            unread_articles: unread,
            total_words: total_w,
            read_words: read_w,
            unread_words: total_w - read_w,
            avg_words_per_article: if total > 0 {
                total_w as f64 / total as f64
            } else {
                0.0
            },
        };
    }
}

#[derive(Clone)]
pub struct PythonBridge {
    config: Config,
    mock_mode: bool,
    mock_state: Arc<Mutex<MockState>>,
}

impl PythonBridge {
    pub fn new(config: Config) -> Self {
        PythonBridge {
            config,
            mock_mode: false,
            mock_state: Arc::new(Mutex::new(MockState::new())),
        }
    }

    pub fn new_mock() -> Self {
        PythonBridge {
            config: Config {
                library_dir: None,
                db_path: None,
                telegram_token: None,
                allowed_telegram_users: vec![],
                default_format: SaveFormat::Markdown,
                python_cmd: None,
                python_bin: None,
                python_workdir: None,
                bridge_timeout_seconds: 5,
            },
            mock_mode: true,
            mock_state: Arc::new(Mutex::new(MockState::new())),
        }
    }

    fn get_command_and_args(&self) -> (String, Vec<String>) {
        // 1. if RIL_PYTHON_CMD is set
        if let Some(cmd) = &self.config.python_cmd {
            let parts: Vec<&str> = cmd.split_whitespace().collect();
            if !parts.is_empty() {
                let command = parts[0].to_string();
                let mut args = parts[1..].iter().map(|s| s.to_string()).collect::<Vec<_>>();
                args.push("-m".to_string());
                args.push("ril.bridge_json".to_string());
                return (command, args);
            }
        }

        // 2. if RIL_PYTHON_BIN is set
        if let Some(bin) = &self.config.python_bin {
            return (
                bin.to_string_lossy().to_string(),
                vec!["-m".to_string(), "ril.bridge_json".to_string()],
            );
        }

        // 3. if "uv" is in project
        let workdir = self
            .config
            .python_workdir
            .clone()
            .unwrap_or_else(|| std::env::current_dir().unwrap_or_default());
        if workdir.join("uv.lock").exists() {
            return (
                "uv".to_string(),
                vec![
                    "run".to_string(),
                    "python".to_string(),
                    "-m".to_string(),
                    "ril.bridge_json".to_string(),
                ],
            );
        }

        // 4. fallback
        (
            "python".to_string(),
            vec!["-m".to_string(), "ril.bridge_json".to_string()],
        )
    }

    pub async fn call<TReq, TResp>(&self, command: &str, args: TReq) -> Result<TResp>
    where
        TReq: Serialize,
        TResp: serde::de::DeserializeOwned,
    {
        let args_val = serde_json::to_value(args)?;
        if self.mock_mode {
            self.call_mock(command, args_val).await
        } else {
            self.call_real(command, args_val).await
        }
    }

    async fn call_real<TResp>(&self, command: &str, args_val: serde_json::Value) -> Result<TResp>
    where
        TResp: serde::de::DeserializeOwned,
    {
        let (cmd, cmd_args) = self.get_command_and_args();
        let workdir = self
            .config
            .python_workdir
            .clone()
            .unwrap_or_else(|| std::env::current_dir().unwrap_or_default());

        tracing::debug!(
            "Spawning bridge process: {} {:?} in {:?}",
            cmd,
            cmd_args,
            workdir
        );

        let mut child = tokio::process::Command::new(&cmd)
            .args(&cmd_args)
            .current_dir(workdir)
            .stdin(std::process::Stdio::piped())
            .stdout(std::process::Stdio::piped())
            .stderr(std::process::Stdio::piped())
            .kill_on_drop(true)
            .spawn()
            .map_err(|e| {
                DaemonError::BridgeExec(format!("Failed to spawn Python process '{}': {}", cmd, e))
            })?;

        let req = BridgeRequest {
            command,
            args: args_val,
        };
        let payload = serde_json::to_vec(&req)?;

        if let Some(mut stdin) = child.stdin.take() {
            use tokio::io::AsyncWriteExt;
            stdin.write_all(&payload).await?;
            stdin.flush().await?;
        }

        let timeout_dur = std::time::Duration::from_secs(self.config.bridge_timeout_seconds);
        let output_res = tokio::time::timeout(timeout_dur, child.wait_with_output()).await;

        let output = match output_res {
            Ok(Ok(out)) => out,
            Ok(Err(e)) => return Err(DaemonError::BridgeExec(format!("Subprocess failed: {}", e))),
            Err(_) => {
                return Err(DaemonError::BridgeTimeout(
                    self.config.bridge_timeout_seconds,
                ));
            }
        };

        let stdout_str = String::from_utf8_lossy(&output.stdout);
        let stderr_str = String::from_utf8_lossy(&output.stderr);

        if !output.status.success() && stdout_str.trim().is_empty() {
            return Err(DaemonError::BridgeExec(format!(
                "Python process exited with status: {}\nStderr: {}",
                output.status, stderr_str
            )));
        }

        let resp: BridgeResponse<serde_json::Value> =
            serde_json::from_str(&stdout_str).map_err(|e| {
                DaemonError::BridgeExec(format!(
                    "Failed to parse bridge JSON: {}\nStdout was: {}\nStderr was: {}",
                    e, stdout_str, stderr_str
                ))
            })?;

        if resp.ok {
            if let Some(data) = resp.data {
                let typed_data = serde_json::from_value(data)?;
                Ok(typed_data)
            } else {
                let typed_data = serde_json::from_value(serde_json::Value::Null)?;
                Ok(typed_data)
            }
        } else if let Some(err) = resp.error {
            Err(DaemonError::BridgePython {
                code: err.code,
                message: err.message,
                details: err.details,
            })
        } else {
            Err(DaemonError::BridgePython {
                code: "UNKNOWN_ERROR".to_string(),
                message: "Unknown bridge error".to_string(),
                details: "".to_string(),
            })
        }
    }

    async fn call_mock<TResp>(&self, command: &str, args: serde_json::Value) -> Result<TResp>
    where
        TResp: serde::de::DeserializeOwned,
    {
        let mut state = self.mock_state.lock().await;

        let res_val = match command {
            "process_url" => {
                let url = args
                    .get("url")
                    .and_then(|v| v.as_str())
                    .unwrap_or("http://example.com");
                let fmt = args
                    .get("format")
                    .and_then(|v| v.as_str())
                    .unwrap_or("markdown");
                let id = (state.articles.len() + 1) as i64;
                let title = format!("Mock Article {}", id);
                let file_path = format!("/mock/library/{}.{}", id, fmt);
                let summary = ArticleSummary {
                    id,
                    url: url.to_string(),
                    title: title.clone(),
                    added_at: "2026-06-28T12:00:00".to_string(),
                    status: "unread".to_string(),
                    file_path: file_path.clone(),
                    word_count: 100 * id,
                    char_count: 600 * id,
                    rating: None,
                    comment: None,
                    tags: vec![],
                    snippet: None,
                };
                state.articles.push(summary);
                state.recalc_stats();
                serde_json::json!({
                    "id": id,
                    "url": url,
                    "title": title,
                    "file_path": file_path,
                    "word_count": 100 * id,
                    "char_count": 600 * id,
                    "status": "unread",
                    "rating": serde_json::Value::Null,
                    "comment": serde_json::Value::Null,
                    "tags": Vec::<String>::new(),
                    "snippet": serde_json::Value::Null
                })
            }
            "search_articles" => {
                let query = args
                    .get("query")
                    .and_then(|v| v.as_str())
                    .unwrap_or("")
                    .to_lowercase();
                let mut results = vec![];
                for art in &state.articles {
                    if art.title.to_lowercase().contains(&query)
                        || art.url.to_lowercase().contains(&query)
                    {
                        results.push(serde_json::json!({
                            "id": art.id,
                            "url": art.url,
                            "title": art.title,
                            "added_at": art.added_at,
                            "status": art.status,
                            "file_path": art.file_path,
                            "word_count": art.word_count,
                            "snippet": format!("Snippet matching ***{}***", query)
                        }));
                    }
                }
                serde_json::Value::Array(results)
            }
            "list_articles" => {
                let status = args.get("status").and_then(|v| v.as_str());
                let limit = args.get("limit").and_then(|v| v.as_i64()).unwrap_or(50) as usize;
                let mut list = vec![];
                for art in &state.articles {
                    if status.is_none() || status == Some("") || status == Some(art.status.as_str())
                    {
                        list.push(art.clone());
                    }
                }
                list.truncate(limit);
                serde_json::json!(list)
            }
            "mark_article_read" => {
                let id = args.get("article_id").and_then(|v| v.as_i64()).unwrap_or(0);
                let mut success = false;
                for art in &mut state.articles {
                    if art.id == id {
                        art.status = "read".to_string();
                        success = true;
                        break;
                    }
                }
                state.recalc_stats();
                serde_json::json!({ "success": success })
            }
            "mark_article_unread" => {
                let id = args.get("article_id").and_then(|v| v.as_i64()).unwrap_or(0);
                let mut success = false;
                for art in &mut state.articles {
                    if art.id == id {
                        art.status = "unread".to_string();
                        success = true;
                        break;
                    }
                }
                state.recalc_stats();
                serde_json::json!({ "success": success })
            }
            "get_reading_stats" => {
                serde_json::json!(state.stats)
            }
            "get_article_content" => {
                let id = args.get("article_id").and_then(|v| v.as_i64()).unwrap_or(0);
                let found = state.articles.iter().find(|art| art.id == id);
                if let Some(art) = found {
                    serde_json::json!({
                        "article": art,
                        "content": format!("# {}\nThis is the content of article {}", art.title, art.id)
                    })
                } else {
                    return Err(DaemonError::BridgePython {
                        code: "NOT_FOUND".to_string(),
                        message: format!("Article with ID {} not found", id),
                        details: "".to_string(),
                    });
                }
            }
            "delete_article" => {
                let id = args.get("article_id").and_then(|v| v.as_i64()).unwrap_or(0);
                let initial_len = state.articles.len();
                state.articles.retain(|art| art.id != id);
                let success = state.articles.len() < initial_len;
                state.recalc_stats();
                serde_json::json!({ "success": success })
            }
            "reset_library" => {
                state.articles.clear();
                state.recalc_stats();
                serde_json::json!({ "success": true })
            }
            "export_article" => {
                let id = args.get("article_id").and_then(|v| v.as_i64()).unwrap_or(0);
                let format = args.get("format").and_then(|v| v.as_str()).unwrap_or("markdown");
                let found = state.articles.iter().find(|art| art.id == id);
                if let Some(art) = found {
                    serde_json::json!({
                        "article_id": art.id,
                        "title": art.title,
                        "format": format,
                        "file_path": format!("/mock/library/{}.{}", art.id, format),
                        "filename": format!("{}.{}", art.id, format),
                        "word_count": art.word_count,
                        "status": art.status,
                        "rating": art.rating,
                        "tags": art.tags
                    })
                } else {
                    return Err(DaemonError::BridgePython {
                        code: "NOT_FOUND".to_string(),
                        message: format!("Article with ID {} not found", id),
                        details: "".to_string(),
                    });
                }
            }
            "search_articles_advanced" => {
                let status = args.get("status").and_then(|v| v.as_str());
                let tag = args.get("tag").and_then(|v| v.as_str());
                let limit = args.get("limit").and_then(|v| v.as_i64()).unwrap_or(10) as usize;
                let offset = args.get("offset").and_then(|v| v.as_i64()).unwrap_or(0) as usize;
                let rating = args
                    .get("rating")
                    .and_then(|v| v.as_i64())
                    .map(|r| r as i32);
                let query = args
                    .get("query")
                    .and_then(|v| v.as_str())
                    .map(|q| q.to_lowercase());

                let mut matched = vec![];
                for art in &state.articles {
                    if let Some(ref st) = status {
                        if art.status != *st {
                            continue;
                        }
                    }
                    if let Some(ref tg) = tag {
                        if !art.tags.contains(&tg.to_string()) {
                            continue;
                        }
                    }
                    if let Some(rt) = rating {
                        if art.rating != Some(rt) {
                            continue;
                        }
                    }
                    if let Some(ref q) = query {
                        if !art.title.to_lowercase().contains(q)
                            && !art.url.to_lowercase().contains(q)
                        {
                            continue;
                        }
                    }
                    matched.push(art.clone());
                }
                let total_count = matched.len() as i64;
                let mut paged = matched;
                if offset < paged.len() {
                    paged = paged.split_off(offset);
                } else {
                    paged.clear();
                }
                paged.truncate(limit);

                serde_json::json!({
                    "articles": paged,
                    "total_count": total_count
                })
            }
            "add_tags" => {
                let id = args.get("article_id").and_then(|v| v.as_i64()).unwrap_or(0);
                let tags_val = args.get("tags").and_then(|v| v.as_array());
                let mut success = false;
                if let Some(arr) = tags_val {
                    for art in &mut state.articles {
                        if art.id == id {
                            for item in arr {
                                if let Some(tag_str) = item.as_str() {
                                    if !art.tags.contains(&tag_str.to_string()) {
                                        art.tags.push(tag_str.to_string());
                                    }
                                }
                            }
                            success = true;
                            break;
                        }
                    }
                }
                serde_json::json!({ "success": success })
            }
            "remove_tag" => {
                let id = args.get("article_id").and_then(|v| v.as_i64()).unwrap_or(0);
                let tag = args.get("tag").and_then(|v| v.as_str()).unwrap_or("");
                let mut success = false;
                for art in &mut state.articles {
                    if art.id == id {
                        art.tags.retain(|t| t != tag);
                        success = true;
                        break;
                    }
                }
                serde_json::json!({ "success": success })
            }
            "list_tags" | "get_tags_stats" => {
                let mut counts = std::collections::HashMap::new();
                for art in &state.articles {
                    for tag in &art.tags {
                        *counts.entry(tag.clone()).or_insert(0) += 1;
                    }
                }
                let mut list: Vec<serde_json::Value> = counts
                    .into_iter()
                    .map(|(tag, count)| serde_json::json!({ "tag": tag, "count": count }))
                    .collect();
                list.sort_by(|a, b| b["count"].as_i64().cmp(&a["count"].as_i64()));
                serde_json::Value::Array(list)
            }
            "rate_article" => {
                let id = args.get("article_id").and_then(|v| v.as_i64()).unwrap_or(0);
                let rating = args
                    .get("rating")
                    .and_then(|v| v.as_i64())
                    .map(|r| r as i32);
                let mut success = false;
                for art in &mut state.articles {
                    if art.id == id {
                        art.rating = rating;
                        success = true;
                        break;
                    }
                }
                serde_json::json!({ "success": success })
            }
            "set_article_comment" => {
                let id = args.get("article_id").and_then(|v| v.as_i64()).unwrap_or(0);
                let comment = args
                    .get("comment")
                    .and_then(|v| v.as_str())
                    .map(|s| s.to_string());
                let mut success = false;
                for art in &mut state.articles {
                    if art.id == id {
                        art.comment = comment.clone();
                        success = true;
                        break;
                    }
                }
                serde_json::json!({ "success": success })
            }
            "get_extended_stats" => {
                let mut base = serde_json::to_value(&state.stats).unwrap();
                let no_tags_count =
                    state.articles.iter().filter(|a| a.tags.is_empty()).count() as i64;
                let no_rating_count =
                    state.articles.iter().filter(|a| a.rating.is_none()).count() as i64;
                let rated: Vec<_> = state.articles.iter().filter_map(|a| a.rating).collect();
                let avg_rating = if rated.is_empty() {
                    0.0
                } else {
                    rated.iter().sum::<i32>() as f64 / rated.len() as f64
                };
                let mut top_articles = state.articles.clone();
                top_articles.sort_by(|a, b| b.rating.cmp(&a.rating));
                top_articles.truncate(5);

                if let Some(obj) = base.as_object_mut() {
                    obj.insert(
                        "no_tags_count".to_string(),
                        serde_json::json!(no_tags_count),
                    );
                    obj.insert(
                        "no_rating_count".to_string(),
                        serde_json::json!(no_rating_count),
                    );
                    obj.insert("avg_rating".to_string(), serde_json::json!(avg_rating));
                    obj.insert("top_articles".to_string(), serde_json::json!(top_articles));
                }
                base
            }
            "get_sources_stats" => {
                let mut domains = std::collections::HashMap::new();
                for art in &state.articles {
                    let host = if let Some(stripped) = art.url.strip_prefix("https://") {
                        Some(stripped)
                    } else if let Some(stripped) = art.url.strip_prefix("http://") {
                        Some(stripped)
                    } else {
                        None
                    }
                    .map(|s| {
                        let part = s.split('/').next().unwrap_or(s);
                        if part.starts_with("www.") {
                            part[4..].to_string()
                        } else {
                            part.to_string()
                        }
                    });
                    if let Some(h) = host {
                        *domains.entry(h).or_insert(0) += 1;
                    }
                }
                let mut list: Vec<serde_json::Value> = domains
                    .into_iter()
                    .map(|(domain, count)| serde_json::json!({ "domain": domain, "count": count }))
                    .collect();
                list.sort_by(|a, b| b["count"].as_i64().cmp(&a["count"].as_i64()));
                serde_json::Value::Array(list)
            }
            "get_ratings_stats" => {
                let mut counts = std::collections::HashMap::new();
                for art in &state.articles {
                    if let Some(r) = art.rating {
                        *counts.entry(r.to_string()).or_insert(0) += 1;
                    }
                }
                for i in 1..=5 {
                    counts.entry(i.to_string()).or_insert(0);
                }
                serde_json::to_value(counts).unwrap()
            }
            "get_dynamics_stats" => {
                serde_json::json!({
                    "today": state.articles.len() as i64,
                    "week": state.articles.len() as i64,
                    "month": state.articles.len() as i64
                })
            }
            _ => {
                return Err(DaemonError::BridgePython {
                    code: "UNKNOWN_COMMAND".to_string(),
                    message: format!("Unknown command: {}", command),
                    details: "".to_string(),
                });
            }
        };

        let typed = serde_json::from_value(res_val)?;
        Ok(typed)
    }

    // Direct methods for easier usage:
    pub async fn process_url(&self, url: &str, format: SaveFormat) -> Result<ProcessingResult> {
        #[derive(Serialize)]
        struct Args<'a> {
            url: &'a str,
            format: &'a str,
        }
        self.call(
            "process_url",
            Args {
                url,
                format: &format.to_string(),
            },
        )
        .await
    }

    pub async fn search_articles(&self, query: &str) -> Result<Vec<SearchResult>> {
        #[derive(Serialize)]
        struct Args<'a> {
            query: &'a str,
        }
        self.call("search_articles", Args { query }).await
    }

    pub async fn list_articles(
        &self,
        status: Option<&str>,
        limit: Option<i64>,
    ) -> Result<Vec<ArticleSummary>> {
        #[derive(Serialize)]
        struct Args<'a> {
            status: Option<&'a str>,
            limit: Option<i64>,
        }
        self.call("list_articles", Args { status, limit }).await
    }

    pub async fn mark_article_read(&self, article_id: i64) -> Result<bool> {
        #[derive(Serialize)]
        struct Args {
            article_id: i64,
        }
        #[derive(Deserialize)]
        struct Resp {
            success: bool,
        }
        let res: Resp = self.call("mark_article_read", Args { article_id }).await?;
        Ok(res.success)
    }

    pub async fn mark_article_unread(&self, article_id: i64) -> Result<bool> {
        #[derive(Serialize)]
        struct Args {
            article_id: i64,
        }
        #[derive(Deserialize)]
        struct Resp {
            success: bool,
        }
        let res: Resp = self
            .call("mark_article_unread", Args { article_id })
            .await?;
        Ok(res.success)
    }

    pub async fn get_reading_stats(&self) -> Result<ReadingStats> {
        #[derive(Serialize)]
        struct Args {}
        self.call("get_reading_stats", Args {}).await
    }

    pub async fn get_article_content(&self, article_id: i64) -> Result<ArticleContent> {
        #[derive(Serialize)]
        struct Args {
            article_id: i64,
        }
        self.call("get_article_content", Args { article_id }).await
    }

    pub async fn delete_article(&self, article_id: i64) -> Result<bool> {
        #[derive(Serialize)]
        struct Args {
            article_id: i64,
        }
        #[derive(Deserialize)]
        struct Resp {
            success: bool,
        }
        let res: Resp = self.call("delete_article", Args { article_id }).await?;
        Ok(res.success)
    }

    pub async fn export_article(&self, article_id: i64, format: SaveFormat) -> Result<crate::domain::ExportResult> {
        #[derive(Serialize)]
        struct Args {
            article_id: i64,
            format: SaveFormat,
        }
        self.call("export_article", Args { article_id, format }).await
    }

    pub async fn reset_library(&self) -> Result<bool> {
        #[derive(Serialize)]
        struct Args {}
        #[derive(Deserialize)]
        struct Resp {
            success: bool,
        }
        let res: Resp = self.call("reset_library", Args {}).await?;
        Ok(res.success)
    }

    pub async fn search_articles_advanced(
        &self,
        query: Option<String>,
        status: Option<String>,
        tag: Option<String>,
        rating: Option<i32>,
        domain: Option<String>,
        no_tags: bool,
        no_rating: bool,
        date_added: Option<String>,
        limit: i64,
        offset: i64,
    ) -> Result<crate::domain::PaginatedArticles> {
        #[derive(Serialize)]
        struct Args {
            query: Option<String>,
            status: Option<String>,
            tag: Option<String>,
            rating: Option<i32>,
            domain: Option<String>,
            no_tags: bool,
            no_rating: bool,
            date_added: Option<String>,
            limit: i64,
            offset: i64,
        }
        self.call(
            "search_articles_advanced",
            Args {
                query,
                status,
                tag,
                rating,
                domain,
                no_tags,
                no_rating,
                date_added,
                limit,
                offset,
            },
        )
        .await
    }

    pub async fn add_tags(&self, article_id: i64, tags: Vec<String>) -> Result<bool> {
        #[derive(Serialize)]
        struct Args {
            article_id: i64,
            tags: Vec<String>,
        }
        #[derive(Deserialize)]
        struct Resp {
            success: bool,
        }
        let res: Resp = self.call("add_tags", Args { article_id, tags }).await?;
        Ok(res.success)
    }

    pub async fn remove_tag(&self, article_id: i64, tag: &str) -> Result<bool> {
        #[derive(Serialize)]
        struct Args<'a> {
            article_id: i64,
            tag: &'a str,
        }
        #[derive(Deserialize)]
        struct Resp {
            success: bool,
        }
        let res: Resp = self.call("remove_tag", Args { article_id, tag }).await?;
        Ok(res.success)
    }

    pub async fn list_tags(&self) -> Result<Vec<crate::domain::TagStat>> {
        #[derive(Serialize)]
        struct Args {}
        self.call("list_tags", Args {}).await
    }

    pub async fn rate_article(&self, article_id: i64, rating: Option<i32>) -> Result<bool> {
        #[derive(Serialize)]
        struct Args {
            article_id: i64,
            rating: Option<i32>,
        }
        #[derive(Deserialize)]
        struct Resp {
            success: bool,
        }
        let res: Resp = self
            .call("rate_article", Args { article_id, rating })
            .await?;
        Ok(res.success)
    }

    pub async fn set_article_comment(
        &self,
        article_id: i64,
        comment: Option<String>,
    ) -> Result<bool> {
        #[derive(Serialize)]
        struct Args {
            article_id: i64,
            comment: Option<String>,
        }
        #[derive(Deserialize)]
        struct Resp {
            success: bool,
        }
        let res: Resp = self
            .call(
                "set_article_comment",
                Args {
                    article_id,
                    comment,
                },
            )
            .await?;
        Ok(res.success)
    }

    pub async fn get_extended_stats(&self) -> Result<crate::domain::ExtendedReadingStats> {
        #[derive(Serialize)]
        struct Args {}
        self.call("get_extended_stats", Args {}).await
    }

    pub async fn get_sources_stats(&self, limit: i64) -> Result<Vec<crate::domain::SourceStat>> {
        #[derive(Serialize)]
        struct Args {
            limit: i64,
        }
        self.call("get_sources_stats", Args { limit }).await
    }

    pub async fn get_tags_stats(&self) -> Result<Vec<crate::domain::TagStat>> {
        #[derive(Serialize)]
        struct Args {}
        self.call("get_tags_stats", Args {}).await
    }

    pub async fn get_ratings_stats(&self) -> Result<std::collections::HashMap<String, i64>> {
        #[derive(Serialize)]
        struct Args {}
        self.call("get_ratings_stats", Args {}).await
    }

    pub async fn get_dynamics_stats(&self) -> Result<crate::domain::DynamicsStats> {
        #[derive(Serialize)]
        struct Args {}
        self.call("get_dynamics_stats", Args {}).await
    }
}
