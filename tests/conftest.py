import pytest
import tempfile
import shutil
from pathlib import Path
import os

@pytest.fixture(autouse=True)
def setup_test_environment(monkeypatch):
    """
    Isolate library storage and SQLite database for all tests.
    Uses tempfile to ensure each test operates on an isolated sandbox.
    """
    temp_dir = tempfile.mkdtemp()
    temp_path = Path(temp_dir)
    
    # Directories
    library_dir = temp_path / "library"
    db_path = library_dir / "metadata.db"
    
    library_dir.mkdir(parents=True, exist_ok=True)
    (library_dir / "images").mkdir(parents=True, exist_ok=True)
    
    # Monkeypatch the config module variables
    monkeypatch.setattr("ril.config.LIBRARY_DIR", library_dir)
    monkeypatch.setattr("ril.config.DB_PATH", db_path)
    
    # Re-initialize the database in the temporary path
    from ril import db
    db.init_db()
    
    yield {
        "temp_dir": temp_path,
        "library_dir": library_dir,
        "db_path": db_path
    }
    
    # Cleanup after test completes
    shutil.rmtree(temp_dir)
