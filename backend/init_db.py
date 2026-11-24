from backend.config import Settings
from backend.db import database, initialize


if __name__ == "__main__":
    engine, _ = database(Settings.from_env())
    try:
        initialize(engine)
        print("Database tables initialized.")
    finally:
        engine.dispose()
