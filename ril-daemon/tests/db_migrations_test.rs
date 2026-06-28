use std::process::Command;
use tempfile::NamedTempFile;

#[test]
fn test_db_migrations_lifecycle() {
    // Determine the workspace directory (python root)
    let project_root = std::path::PathBuf::from("/Users/vladimirkasterin/python/ril");

    // Create a temporary database file
    let temp_db = NamedTempFile::new().unwrap();
    let temp_db_path = temp_db.path().to_path_buf();

    // 1. Apply migration to a new empty database
    let status = Command::new("uv")
        .args(&[
            "run",
            "python",
            "-c",
            "from ril.db import init_db, get_db_connection; init_db(); conn = get_db_connection(); cursor = conn.cursor(); cursor.execute('PRAGMA table_info(articles)'); cols = [r[1] for r in cursor.fetchall()]; assert 'rating' in cols; assert 'comment' in cols; cursor.execute('SELECT name FROM sqlite_master WHERE type=\"table\"'); tables = [r[0] for r in cursor.fetchall()]; assert 'article_tags' in tables; assert 'articles_fts' in tables;"
        ])
        .current_dir(&project_root)
        .env("RIL_DB_PATH", &temp_db_path)
        .status()
        .expect("Failed to execute python db verification");

    assert!(status.success(), "init_db failed on new empty database");

    // 2. Apply migration to an existing old schema database (backward compatibility/migration test)
    // We recreate a new temp db and manually construct the old schema
    let temp_db_old = NamedTempFile::new().unwrap();
    let temp_db_old_path = temp_db_old.path().to_path_buf();

    // Create old schema (without rating and comment columns) and insert a test article
    let status_setup = Command::new("uv")
        .args(&[
            "run",
            "python",
            "-c",
            "import os, sqlite3; conn = sqlite3.connect(os.environ.get('RIL_DB_PATH')); cursor = conn.cursor(); cursor.execute('CREATE TABLE articles (id INTEGER PRIMARY KEY, url TEXT UNIQUE, title TEXT, added_at TEXT, file_path TEXT, word_count INTEGER)'); cursor.execute('INSERT INTO articles (url, title, added_at, file_path, word_count) VALUES (\"http://old.com\", \"Old Title\", \"2026-01-01\", \"/path\", 100)'); conn.commit();"
        ])
        .current_dir(&project_root)
        .env("RIL_DB_PATH", &temp_db_old_path)
        .status()
        .expect("Failed to setup old database");
    assert!(status_setup.success());

    // Run init_db() on the old database
    let status_migrate = Command::new("uv")
        .args(&[
            "run",
            "python",
            "-c",
            "from ril.db import init_db, get_db_connection; init_db(); conn = get_db_connection(); cursor = conn.cursor(); cursor.execute('SELECT url, title, rating, comment FROM articles WHERE url=\"http://old.com\"'); row = cursor.fetchone(); assert row[0] == 'http://old.com'; assert row[1] == 'Old Title'; assert row[2] is None; assert row[3] is None;"
        ])
        .current_dir(&project_root)
        .env("RIL_DB_PATH", &temp_db_old_path)
        .status()
        .expect("Failed to run migration on old database");

    assert!(
        status_migrate.success(),
        "Migration failed to preserve old data or add columns"
    );
}
