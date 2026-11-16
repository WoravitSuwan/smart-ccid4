import os
import tempfile

# Isolated data directory and database for every test run (set before the app is imported).
_tmp = tempfile.mkdtemp(prefix="ccid4-test-")
os.environ["CCID4_DATA_DIR"] = _tmp
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp}/test.db"
os.environ["CCID4_SECRET_KEY"] = "test-secret"
os.environ.pop("CCID4_ASR_MODEL", None)
