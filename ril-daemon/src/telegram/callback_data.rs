use std::str::FromStr;

#[derive(Debug, Clone, PartialEq)]
pub enum CallbackAction {
    Hub,
    List { status: String, page: i64 },
    Article { id: i64 },
    MarkRead { id: i64 },
    MarkUnread { id: i64 },
    DeletePrompt { id: i64 },
    DeleteConfirm { id: i64 },
    RatePrompt { id: i64 },
    RateSet { id: i64, val: i32 },
    CommentMenu { id: i64 },
    CommentSetPrompt { id: i64 },
    CommentDelete { id: i64 },
    TagsMenu { id: i64 },
    TagAddPrompt { id: i64 },
    TagRemove { id: i64, tag: String },
    TagsList { page: i64 },
    SearchTag { tag: String, page: i64 },
    RatingsList,
    SearchRating { rating: i32, page: i64 },
    Stats { section: String },
    Settings,
    SetFormat { format: String },
    ResetLibraryPrompt,
    ResetLibraryConfirm,
    SearchMenu,
    SearchFilterQueryPrompt,
    SearchFilterDomainPrompt,
    SearchFilterTagPrompt,
    SearchFilterTagSelect { tag: String },
    SearchFilterStatusPrompt,
    SearchFilterStatusSelect { status: String },
    SearchFilterRatingPrompt,
    SearchFilterRatingSelect { rating: String },
    SearchFilterDatePrompt,
    SearchFilterDateSelect { date: String },
    SearchFilterReset,
    SearchFilterRun { page: i64 },
}

impl FromStr for CallbackAction {
    type Err = String;

    fn from_str(s: &str) -> Result<Self, Self::Err> {
        if s == "hub" {
            return Ok(CallbackAction::Hub);
        }
        if s == "ratings_list" {
            return Ok(CallbackAction::RatingsList);
        }
        if s == "settings" {
            return Ok(CallbackAction::Settings);
        }
        if s == "reset_lib_prompt" {
            return Ok(CallbackAction::ResetLibraryPrompt);
        }
        if s == "reset_lib_confirm" {
            return Ok(CallbackAction::ResetLibraryConfirm);
        }
        if s == "search" {
            return Ok(CallbackAction::SearchMenu);
        }
        if s == "sf_query" {
            return Ok(CallbackAction::SearchFilterQueryPrompt);
        }
        if s == "sf_domain" {
            return Ok(CallbackAction::SearchFilterDomainPrompt);
        }
        if s == "sf_tag" {
            return Ok(CallbackAction::SearchFilterTagPrompt);
        }
        if s == "sf_status" {
            return Ok(CallbackAction::SearchFilterStatusPrompt);
        }
        if s == "sf_rating" {
            return Ok(CallbackAction::SearchFilterRatingPrompt);
        }
        if s == "sf_date" {
            return Ok(CallbackAction::SearchFilterDatePrompt);
        }
        if s == "sf_reset" {
            return Ok(CallbackAction::SearchFilterReset);
        }

        if s.starts_with("list:") {
            let parts: Vec<&str> = s.split(':').collect();
            if parts.len() == 3 {
                let status = parts[1].to_string();
                let page = parts[2].parse::<i64>().map_err(|e| e.to_string())?;
                return Ok(CallbackAction::List { status, page });
            }
        }
        if s.starts_with("art:") {
            let id = s
                .split(':')
                .nth(1)
                .ok_or("Missing ID")?
                .parse::<i64>()
                .map_err(|e| e.to_string())?;
            return Ok(CallbackAction::Article { id });
        }
        if s.starts_with("read:") {
            let id = s
                .split(':')
                .nth(1)
                .ok_or("Missing ID")?
                .parse::<i64>()
                .map_err(|e| e.to_string())?;
            return Ok(CallbackAction::MarkRead { id });
        }
        if s.starts_with("unread:") {
            let id = s
                .split(':')
                .nth(1)
                .ok_or("Missing ID")?
                .parse::<i64>()
                .map_err(|e| e.to_string())?;
            return Ok(CallbackAction::MarkUnread { id });
        }
        if s.starts_with("art_del:") {
            let id = s
                .split(':')
                .nth(1)
                .ok_or("Missing ID")?
                .parse::<i64>()
                .map_err(|e| e.to_string())?;
            return Ok(CallbackAction::DeletePrompt { id });
        }
        if s.starts_with("art_del_conf:") {
            let id = s
                .split(':')
                .nth(1)
                .ok_or("Missing ID")?
                .parse::<i64>()
                .map_err(|e| e.to_string())?;
            return Ok(CallbackAction::DeleteConfirm { id });
        }
        if s.starts_with("art_rate:") {
            let id = s
                .split(':')
                .nth(1)
                .ok_or("Missing ID")?
                .parse::<i64>()
                .map_err(|e| e.to_string())?;
            return Ok(CallbackAction::RatePrompt { id });
        }
        if s.starts_with("rate_set:") {
            let parts: Vec<&str> = s.split(':').collect();
            if parts.len() == 3 {
                let id = parts[1].parse::<i64>().map_err(|e| e.to_string())?;
                let val = parts[2].parse::<i32>().map_err(|e| e.to_string())?;
                return Ok(CallbackAction::RateSet { id, val });
            }
        }
        if s.starts_with("art_comm:") {
            let id = s
                .split(':')
                .nth(1)
                .ok_or("Missing ID")?
                .parse::<i64>()
                .map_err(|e| e.to_string())?;
            return Ok(CallbackAction::CommentMenu { id });
        }
        if s.starts_with("comm_set:") {
            let id = s
                .split(':')
                .nth(1)
                .ok_or("Missing ID")?
                .parse::<i64>()
                .map_err(|e| e.to_string())?;
            return Ok(CallbackAction::CommentSetPrompt { id });
        }
        if s.starts_with("comm_del:") {
            let id = s
                .split(':')
                .nth(1)
                .ok_or("Missing ID")?
                .parse::<i64>()
                .map_err(|e| e.to_string())?;
            return Ok(CallbackAction::CommentDelete { id });
        }
        if s.starts_with("art_tags:") {
            let id = s
                .split(':')
                .nth(1)
                .ok_or("Missing ID")?
                .parse::<i64>()
                .map_err(|e| e.to_string())?;
            return Ok(CallbackAction::TagsMenu { id });
        }
        if s.starts_with("tag_add:") {
            let id = s
                .split(':')
                .nth(1)
                .ok_or("Missing ID")?
                .parse::<i64>()
                .map_err(|e| e.to_string())?;
            return Ok(CallbackAction::TagAddPrompt { id });
        }
        if s.starts_with("tag_rem:") {
            let parts: Vec<&str> = s.splitn(3, ':').collect();
            if parts.len() == 3 {
                let id = parts[1].parse::<i64>().map_err(|e| e.to_string())?;
                let tag = parts[2].to_string();
                return Ok(CallbackAction::TagRemove { id, tag });
            }
        }
        if s.starts_with("tags_list:") {
            let page = s
                .split(':')
                .nth(1)
                .ok_or("Missing page")?
                .parse::<i64>()
                .map_err(|e| e.to_string())?;
            return Ok(CallbackAction::TagsList { page });
        }
        if s.starts_with("stag:") {
            let parts: Vec<&str> = s.splitn(3, ':').collect();
            if parts.len() == 3 {
                let tag = parts[1].to_string();
                let page = parts[2].parse::<i64>().map_err(|e| e.to_string())?;
                return Ok(CallbackAction::SearchTag { tag, page });
            }
        }
        if s.starts_with("srate:") {
            let parts: Vec<&str> = s.split(':').collect();
            if parts.len() == 3 {
                let rating = parts[1].parse::<i32>().map_err(|e| e.to_string())?;
                let page = parts[2].parse::<i64>().map_err(|e| e.to_string())?;
                return Ok(CallbackAction::SearchRating { rating, page });
            }
        }
        if s.starts_with("stats:") {
            let section = s.split(':').nth(1).ok_or("Missing section")?.to_string();
            return Ok(CallbackAction::Stats { section });
        }
        if s.starts_with("set_fmt:") {
            let format = s.split(':').nth(1).ok_or("Missing format")?.to_string();
            return Ok(CallbackAction::SetFormat { format });
        }
        if s.starts_with("sft_select:") {
            let tag = s.split(':').nth(1).ok_or("Missing tag")?.to_string();
            return Ok(CallbackAction::SearchFilterTagSelect { tag });
        }
        if s.starts_with("sfs_") {
            let status = s.strip_prefix("sfs_").ok_or("Invalid status")?.to_string();
            return Ok(CallbackAction::SearchFilterStatusSelect { status });
        }
        if s.starts_with("sfr_") {
            let rating = s.strip_prefix("sfr_").ok_or("Invalid rating")?.to_string();
            return Ok(CallbackAction::SearchFilterRatingSelect { rating });
        }
        if s.starts_with("sfd_") {
            let date = s.strip_prefix("sfd_").ok_or("Invalid date")?.to_string();
            return Ok(CallbackAction::SearchFilterDateSelect { date });
        }
        if s.starts_with("sf_run:") {
            let page = s
                .split(':')
                .nth(1)
                .ok_or("Missing page")?
                .parse::<i64>()
                .map_err(|e| e.to_string())?;
            return Ok(CallbackAction::SearchFilterRun { page });
        }

        Err(format!("Unknown callback format: {}", s))
    }
}

impl CallbackAction {
    pub fn to_string(&self) -> String {
        match self {
            CallbackAction::Hub => "hub".to_string(),
            CallbackAction::List { status, page } => format!("list:{}:{}", status, page),
            CallbackAction::Article { id } => format!("art:{}", id),
            CallbackAction::MarkRead { id } => format!("read:{}", id),
            CallbackAction::MarkUnread { id } => format!("unread:{}", id),
            CallbackAction::DeletePrompt { id } => format!("art_del:{}", id),
            CallbackAction::DeleteConfirm { id } => format!("art_del_conf:{}", id),
            CallbackAction::RatePrompt { id } => format!("art_rate:{}", id),
            CallbackAction::RateSet { id, val } => format!("rate_set:{}:{}", id, val),
            CallbackAction::CommentMenu { id } => format!("art_comm:{}", id),
            CallbackAction::CommentSetPrompt { id } => format!("comm_set:{}", id),
            CallbackAction::CommentDelete { id } => format!("comm_del:{}", id),
            CallbackAction::TagsMenu { id } => format!("art_tags:{}", id),
            CallbackAction::TagAddPrompt { id } => format!("tag_add:{}", id),
            CallbackAction::TagRemove { id, tag } => format!("tag_rem:{}:{}", id, tag),
            CallbackAction::TagsList { page } => format!("tags_list:{}", page),
            CallbackAction::SearchTag { tag, page } => format!("stag:{}:{}", tag, page),
            CallbackAction::RatingsList => "ratings_list".to_string(),
            CallbackAction::SearchRating { rating, page } => format!("srate:{}:{}", rating, page),
            CallbackAction::Stats { section } => format!("stats:{}", section),
            CallbackAction::Settings => "settings".to_string(),
            CallbackAction::SetFormat { format } => format!("set_fmt:{}", format),
            CallbackAction::ResetLibraryPrompt => "reset_lib_prompt".to_string(),
            CallbackAction::ResetLibraryConfirm => "reset_lib_confirm".to_string(),
            CallbackAction::SearchMenu => "search".to_string(),
            CallbackAction::SearchFilterQueryPrompt => "sf_query".to_string(),
            CallbackAction::SearchFilterDomainPrompt => "sf_domain".to_string(),
            CallbackAction::SearchFilterTagPrompt => "sf_tag".to_string(),
            CallbackAction::SearchFilterTagSelect { tag } => format!("sft_select:{}", tag),
            CallbackAction::SearchFilterStatusPrompt => "sf_status".to_string(),
            CallbackAction::SearchFilterStatusSelect { status } => format!("sfs_{}", status),
            CallbackAction::SearchFilterRatingPrompt => "sf_rating".to_string(),
            CallbackAction::SearchFilterRatingSelect { rating } => format!("sfr_{}", rating),
            CallbackAction::SearchFilterDatePrompt => "sf_date".to_string(),
            CallbackAction::SearchFilterDateSelect { date } => format!("sfd_{}", date),
            CallbackAction::SearchFilterReset => "sf_reset".to_string(),
            CallbackAction::SearchFilterRun { page } => format!("sf_run:{}", page),
        }
    }

    pub fn validate_length(&self) -> Result<(), String> {
        let s = self.to_string();
        if s.len() > 64 {
            Err(format!(
                "Callback data exceeds 64 bytes limit (len={}): {}",
                s.len(),
                s
            ))
        } else {
            Ok(())
        }
    }
}
