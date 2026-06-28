pub mod protocol;
pub mod tools;

use crate::domain::SaveFormat;
use crate::mcp::protocol::{JsonRpcRequest, JsonRpcResponse};
use crate::mcp::tools::get_tools_list;
use crate::python_bridge::PythonBridge;
use serde_json::{json, Value};
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};

pub async fn run_mcp_server(bridge: PythonBridge) -> anyhow::Result<()> {
    let stdin = tokio::io::stdin();
    let mut reader = BufReader::new(stdin).lines();
    let mut stdout = tokio::io::stdout();

    tracing::info!("MCP server started over stdio");

    while let Some(line) = reader.next_line().await? {
        if line.trim().is_empty() {
            continue;
        }

        let request: JsonRpcRequest = match serde_json::from_str(&line) {
            Ok(req) => req,
            Err(e) => {
                let err_resp = JsonRpcResponse::error(
                    Value::Null,
                    -32700,
                    format!("Parse error: {}", e),
                    None,
                );
                let payload = serde_json::to_string(&err_resp)? + "\n";
                stdout.write_all(payload.as_bytes()).await?;
                stdout.flush().await?;
                continue;
            }
        };

        let id = request.id.clone().unwrap_or(Value::Null);

        let response = match request.method.as_str() {
            "initialize" => {
                let result = json!({
                    "protocolVersion": "2024-11-05",
                    "capabilities": {
                        "tools": {}
                    },
                    "serverInfo": {
                        "name": "ril-daemon",
                        "version": "0.1.0"
                    }
                });
                Some(JsonRpcResponse::success(id, result))
            }
            "notifications/initialized" => None,
            "ping" => Some(JsonRpcResponse::success(id, json!({}))),
            "tools/list" => Some(JsonRpcResponse::success(id, get_tools_list())),
            "tools/call" => {
                let params = request.params.as_ref();
                let name = params
                    .and_then(|p| p.get("name"))
                    .and_then(|v| v.as_str())
                    .unwrap_or("");
                let arguments = params
                    .and_then(|p| p.get("arguments"))
                    .unwrap_or(&Value::Null);

                match handle_tool_call(&bridge, name, arguments).await {
                    Ok(result) => Some(JsonRpcResponse::success(id, result)),
                    Err(e) => {
                        let result = json!({
                            "content": [
                                {
                                    "type": "text",
                                    "text": format!("Error: {}", e)
                                }
                            ],
                            "isError": true
                        });
                        Some(JsonRpcResponse::success(id, result))
                    }
                }
            }
            _ => Some(JsonRpcResponse::error(
                id,
                -32601,
                format!("Method not found: {}", request.method),
                None,
            )),
        };

        if let Some(resp) = response {
            let payload = serde_json::to_string(&resp)? + "\n";
            stdout.write_all(payload.as_bytes()).await?;
            stdout.flush().await?;
        }
    }

    Ok(())
}

pub(crate) async fn handle_tool_call(
    bridge: &PythonBridge,
    name: &str,
    arguments: &Value,
) -> std::result::Result<Value, String> {
    match name {
        "process_url" => {
            let url = arguments
                .get("url")
                .and_then(|v| v.as_str())
                .ok_or("url parameter is required")?;
            let fmt_str = arguments
                .get("format")
                .and_then(|v| v.as_str())
                .unwrap_or("markdown");
            let format = fmt_str.parse::<SaveFormat>()?;
            let res = bridge
                .process_url(url, format)
                .await
                .map_err(|e| e.to_string())?;
            Ok(json!({
                "content": [
                    {
                        "type": "text",
                        "text": serde_json::to_string_pretty(&res).unwrap_or_default()
                    }
                ],
                "isError": false
            }))
        }
        "search_articles" => {
            let query = arguments
                .get("query")
                .and_then(|v| v.as_str())
                .ok_or("query parameter is required")?;
            let res = bridge
                .search_articles(query)
                .await
                .map_err(|e| e.to_string())?;
            Ok(json!({
                "content": [
                    {
                        "type": "text",
                        "text": serde_json::to_string_pretty(&res).unwrap_or_default()
                    }
                ],
                "isError": false
            }))
        }
        "list_articles" => {
            let status = arguments.get("status").and_then(|v| v.as_str());
            let limit = arguments.get("limit").and_then(|v| v.as_i64());
            let res = bridge
                .list_articles(status, limit)
                .await
                .map_err(|e| e.to_string())?;
            Ok(json!({
                "content": [
                    {
                        "type": "text",
                        "text": serde_json::to_string_pretty(&res).unwrap_or_default()
                    }
                ],
                "isError": false
            }))
        }
        "mark_article_read" => {
            let article_id = arguments
                .get("article_id")
                .and_then(|v| v.as_i64())
                .ok_or("article_id parameter is required")?;
            let success = bridge
                .mark_article_read(article_id)
                .await
                .map_err(|e| e.to_string())?;
            Ok(json!({
                "content": [
                    {
                        "type": "text",
                        "text": format!("Article {} marked as read. Success: {}", article_id, success)
                    }
                ],
                "isError": false
            }))
        }
        "mark_article_unread" => {
            let article_id = arguments
                .get("article_id")
                .and_then(|v| v.as_i64())
                .ok_or("article_id parameter is required")?;
            let success = bridge
                .mark_article_unread(article_id)
                .await
                .map_err(|e| e.to_string())?;
            Ok(json!({
                "content": [
                    {
                        "type": "text",
                        "text": format!("Article {} marked as unread. Success: {}", article_id, success)
                    }
                ],
                "isError": false
            }))
        }
        "get_reading_stats" => {
            let res = bridge
                .get_reading_stats()
                .await
                .map_err(|e| e.to_string())?;
            Ok(json!({
                "content": [
                    {
                        "type": "text",
                        "text": serde_json::to_string_pretty(&res).unwrap_or_default()
                    }
                ],
                "isError": false
            }))
        }
        "get_article_content" => {
            let article_id = arguments
                .get("article_id")
                .and_then(|v| v.as_i64())
                .ok_or("article_id parameter is required")?;
            let res = bridge
                .get_article_content(article_id)
                .await
                .map_err(|e| e.to_string())?;
            Ok(json!({
                "content": [
                    {
                        "type": "text",
                        "text": serde_json::to_string_pretty(&res).unwrap_or_default()
                    }
                ],
                "isError": false
            }))
        }
        "delete_article" => {
            let article_id = arguments
                .get("article_id")
                .and_then(|v| v.as_i64())
                .ok_or("article_id parameter is required")?;
            let success = bridge
                .delete_article(article_id)
                .await
                .map_err(|e| e.to_string())?;
            Ok(json!({
                "content": [
                    {
                        "type": "text",
                        "text": format!("Article {} deleted. Success: {}", article_id, success)
                    }
                ],
                "isError": false
            }))
        }
        "reset_library" => {
            let success = bridge.reset_library().await.map_err(|e| e.to_string())?;
            Ok(json!({
                "content": [
                    {
                        "type": "text",
                        "text": format!("Library reset. Success: {}", success)
                    }
                ],
                "isError": false
            }))
        }
        _ => Err(format!("Unknown tool: {}", name)),
    }
}
