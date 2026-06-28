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

    pub async fn get_and_clear_last_menu(&self, user_id: UserId) -> Option<i32> {
        let mut map = self.last_menu_messages.lock().await;
        map.remove(&user_id)
    }

    pub async fn set_last_menu(&self, user_id: UserId, msg_id: i32) {
        let mut map = self.last_menu_messages.lock().await;
        map.insert(user_id, msg_id);
    }
}
