#![allow(dead_code, unused_imports)]

use ril_daemon::config::Config;
use ril_daemon::domain::SaveFormat;
use ril_daemon::python_bridge::PythonBridge;
use ril_daemon::telegram::state::BotState;
use std::sync::{Arc, Mutex};
use teloxide::types::{CallbackQuery, Chat, ChatId, Message, MessageId, User, UserId};
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::TcpListener;

#[derive(Debug, Clone)]
pub struct MockTelegramRequest {
    pub path: String,
    pub method: String,
    pub body: serde_json::Value,
}

pub struct MockTelegramServer {
    pub port: u16,
    pub requests: Arc<Mutex<Vec<MockTelegramRequest>>>,
    _shutdown_tx: tokio::sync::oneshot::Sender<()>,
}

impl MockTelegramServer {
    pub async fn start() -> Self {
        let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let addr = listener.local_addr().unwrap();
        let port = addr.port();
        let requests = Arc::new(Mutex::new(Vec::new()));
        let (shutdown_tx, mut shutdown_rx) = tokio::sync::oneshot::channel::<()>();

        let requests_clone = requests.clone();
        tokio::spawn(async move {
            loop {
                tokio::select! {
                    accept_res = listener.accept() => {
                        let (mut socket, _) = match accept_res {
                            Ok(res) => res,
                            Err(_) => continue,
                        };
                        let requests_clone2 = requests_clone.clone();
                        tokio::spawn(async move {
                            let mut buffer = vec![0; 8192];
                            let mut read_bytes = 0;

                            // Read headers
                            loop {
                                let n = match socket.read(&mut buffer[read_bytes..]).await {
                                    Ok(0) => break,
                                    Ok(n) => n,
                                    Err(_) => return,
                                };
                                read_bytes += n;
                                if let Some(pos) = buffer[..read_bytes].windows(4).position(|w| w == b"\r\n\r\n") {
                                    // Found end of headers
                                    let header_bytes = &buffer[..pos + 4];
                                    let header_str = String::from_utf8_lossy(header_bytes);

                                    // Parse Content-Length
                                    let mut content_len = 0;
                                    for line in header_str.lines() {
                                        if line.to_lowercase().starts_with("content-length:") {
                                            if let Some(val) = line.split(':').nth(1) {
                                                content_len = val.trim().parse::<usize>().unwrap_or(0);
                                            }
                                        }
                                    }

                                    // Parse Path & Method
                                    let request_line = header_str.lines().next().unwrap_or("");
                                    let mut parts = request_line.split_whitespace();
                                    let method = parts.next().unwrap_or("POST").to_string();
                                    let path = parts.next().unwrap_or("").to_string();

                                    // Read remaining body
                                    let body_start = pos + 4;
                                    let mut body_bytes = buffer[body_start..read_bytes].to_vec();
                                    while body_bytes.len() < content_len {
                                        let mut temp = vec![0; 4096];
                                        let n = match socket.read(&mut temp).await {
                                            Ok(0) => break,
                                            Ok(n) => n,
                                            Err(_) => return,
                                        };
                                        body_bytes.extend_from_slice(&temp[..n]);
                                    }

                                    let body_str = String::from_utf8_lossy(&body_bytes[..content_len]);
                                    let body_json: serde_json::Value = serde_json::from_str(&body_str)
                                        .unwrap_or(serde_json::Value::String(body_str.to_string()));

                                    requests_clone2.lock().unwrap().push(MockTelegramRequest {
                                        path,
                                        method,
                                        body: body_json,
                                    });
                                    break;
                                }
                            }

                            // Send Telegram API response
                            let response_json = serde_json::json!({
                                "ok": true,
                                "result": {
                                    "message_id": 1001,
                                    "date": 1600000000,
                                    "chat": {
                                        "id": 12345,
                                        "type": "private"
                                    },
                                    "text": "mock_response",
                                    "document": {
                                        "file_id": "mock_doc_id"
                                    }
                                }
                            });
                            let response_str = serde_json::to_string(&response_json).unwrap();
                            let http_response = format!(
                                "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nConnection: close\r\nContent-Length: {}\r\n\r\n{}",
                                response_str.len(),
                                response_str
                            );
                            let _ = socket.write_all(http_response.as_bytes()).await;
                        });
                    }
                    _ = &mut shutdown_rx => {
                        break;
                    }
                }
            }
        });

        MockTelegramServer {
            port,
            requests,
            _shutdown_tx: shutdown_tx,
        }
    }
}

pub fn make_mock_state() -> Arc<BotState> {
    let bridge = PythonBridge::new_mock();
    let config = Config {
        library_dir: None,
        db_path: None,
        telegram_token: Some("123:mock_token".to_string()),
        allowed_telegram_users: vec![],
        default_format: SaveFormat::Markdown,
        python_cmd: None,
        python_bin: None,
        python_workdir: None,
        bridge_timeout_seconds: 5,
    };
    Arc::new(BotState::new(bridge, config))
}

pub fn make_mock_state_with_allowed_users(allowed: Vec<i64>) -> Arc<BotState> {
    let bridge = PythonBridge::new_mock();
    let config = Config {
        library_dir: None,
        db_path: None,
        telegram_token: Some("123:mock_token".to_string()),
        allowed_telegram_users: allowed,
        default_format: SaveFormat::Markdown,
        python_cmd: None,
        python_bin: None,
        python_workdir: None,
        bridge_timeout_seconds: 5,
    };
    Arc::new(BotState::new(bridge, config))
}

pub fn make_message(text: &str, user_id: i64, chat_id: i64) -> Message {
    let json_val = serde_json::json!({
        "message_id": 1,
        "date": 1600000000,
        "chat": {
            "id": chat_id,
            "type": "private",
            "first_name": "Test User"
        },
        "from": {
            "id": user_id,
            "is_bot": false,
            "first_name": "Test User",
            "username": "testuser"
        },
        "text": text
    });
    serde_json::from_value(json_val).unwrap()
}

pub fn make_callback_query(data: &str, user_id: i64, chat_id: i64) -> CallbackQuery {
    let json_val = serde_json::json!({
        "id": "1",
        "from": {
            "id": user_id,
            "is_bot": false,
            "first_name": "Test User"
        },
        "message": {
            "message_id": 100,
            "date": 1600000000,
            "chat": {
                "id": chat_id,
                "type": "private"
            },
            "text": "Original message text"
        },
        "chat_instance": "1",
        "data": data
    });
    serde_json::from_value(json_val).unwrap()
}

pub fn setup_bot(port: u16) -> teloxide::Bot {
    let url = reqwest::Url::parse(&format!("http://127.0.0.1:{}", port)).unwrap();
    teloxide::Bot::new("mock_token").set_api_url(url)
}
