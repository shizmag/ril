use crate::config::Config;
use crate::domain::SaveFormat;
use crate::python_bridge::PythonBridge;
use std::collections::{HashMap, HashSet};
use teloxide::types::UserId;
use tokio::sync::Mutex;

#[derive(Debug, Clone, Default)]
pub struct SearchSession {
    pub query: Option<String>,
    pub status: Option<String>,
    pub tag: Option<String>,
    pub rating: Option<i32>,
    pub domain: Option<String>,
    pub no_tags: bool,
    pub no_rating: bool,
    pub date_added: Option<String>,
}

#[derive(Debug, Clone)]
pub enum PendingState {
    None,
    WaitingForComment { article_id: i64 },
    WaitingForTag { article_id: i64 },
    WaitingForSearchQuery,
    WaitingForFilterDomain,
}

pub struct BotState {
    pub bridge: PythonBridge,
    pub config: Config,
    pub user_formats: Mutex<HashMap<UserId, SaveFormat>>,
    pub pending_resets: Mutex<HashSet<UserId>>,
    pub pending_states: Mutex<HashMap<UserId, PendingState>>,
    pub search_sessions: Mutex<HashMap<UserId, SearchSession>>,
    pub last_menu_messages: Mutex<HashMap<UserId, i32>>,
    pub hub_messages: Mutex<HashMap<UserId, i32>>,
    pub state_messages: Mutex<HashMap<UserId, i32>>,
    pub last_imported_articles: Mutex<HashMap<UserId, Vec<i64>>>,
    pub last_import_errors: Mutex<HashMap<UserId, Vec<String>>>,
}

impl BotState {
    pub fn new(bridge: PythonBridge, config: Config) -> Self {
        Self {
            bridge,
            config,
            user_formats: Mutex::new(HashMap::new()),
            pending_resets: Mutex::new(HashSet::new()),
            pending_states: Mutex::new(HashMap::new()),
            search_sessions: Mutex::new(HashMap::new()),
            last_menu_messages: Mutex::new(HashMap::new()),
            hub_messages: Mutex::new(HashMap::new()),
            state_messages: Mutex::new(HashMap::new()),
            last_imported_articles: Mutex::new(HashMap::new()),
            last_import_errors: Mutex::new(HashMap::new()),
        }
    }

    pub async fn get_pending_state(&self, user_id: UserId) -> PendingState {
        let states = self.pending_states.lock().await;
        states.get(&user_id).cloned().unwrap_or(PendingState::None)
    }

    pub async fn set_pending_state(&self, user_id: UserId, state: PendingState) {
        let mut states = self.pending_states.lock().await;
        states.insert(user_id, state);
    }

    pub async fn clear_pending_state(&self, user_id: UserId) {
        let mut states = self.pending_states.lock().await;
        states.remove(&user_id);
    }

    pub async fn get_search_session(&self, user_id: UserId) -> SearchSession {
        let sessions = self.search_sessions.lock().await;
        sessions.get(&user_id).cloned().unwrap_or_default()
    }

    pub async fn update_search_session<F>(&self, user_id: UserId, f: F)
    where
        F: FnOnce(&mut SearchSession),
    {
        let mut sessions = self.search_sessions.lock().await;
        let session = sessions
            .entry(user_id)
            .or_insert_with(SearchSession::default);
        f(session);
    }

    pub async fn clear_search_session(&self, user_id: UserId) {
        let mut sessions = self.search_sessions.lock().await;
        sessions.remove(&user_id);
    }

    pub async fn get_hub_message(&self, user_id: UserId) -> Option<i32> {
        let map = self.hub_messages.lock().await;
        map.get(&user_id).cloned()
    }

    pub async fn set_hub_message(&self, user_id: UserId, msg_id: i32) {
        let mut map = self.hub_messages.lock().await;
        map.insert(user_id, msg_id);
    }

    pub async fn clear_hub_message(&self, user_id: UserId) -> Option<i32> {
        let mut map = self.hub_messages.lock().await;
        map.remove(&user_id)
    }

    pub async fn get_state_message(&self, user_id: UserId) -> Option<i32> {
        let map = self.state_messages.lock().await;
        map.get(&user_id).cloned()
    }

    pub async fn set_state_message(&self, user_id: UserId, msg_id: i32) {
        let mut map = self.state_messages.lock().await;
        map.insert(user_id, msg_id);
    }

    pub async fn clear_state_message(&self, user_id: UserId) -> Option<i32> {
        let mut map = self.state_messages.lock().await;
        map.remove(&user_id)
    }

    pub async fn get_and_clear_last_menu(&self, user_id: UserId) -> Option<i32> {
        self.clear_state_message(user_id).await
    }

    pub async fn set_last_menu(&self, user_id: UserId, msg_id: i32) {
        self.set_state_message(user_id, msg_id).await;
    }

    pub async fn get_last_imported(&self, user_id: UserId) -> Vec<i64> {
        let map = self.last_imported_articles.lock().await;
        map.get(&user_id).cloned().unwrap_or_default()
    }

    pub async fn set_last_imported(&self, user_id: UserId, ids: Vec<i64>) {
        let mut map = self.last_imported_articles.lock().await;
        map.insert(user_id, ids);
    }

    pub async fn get_last_errors(&self, user_id: UserId) -> Vec<String> {
        let map = self.last_import_errors.lock().await;
        map.get(&user_id).cloned().unwrap_or_default()
    }

    pub async fn set_last_errors(&self, user_id: UserId, errs: Vec<String>) {
        let mut map = self.last_import_errors.lock().await;
        map.insert(user_id, errs);
    }
}
