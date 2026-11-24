import logging

from backend.api import create_app

logging.getLogger("lost_and_found.agents").setLevel(logging.INFO)
logging.getLogger("lost_and_found.agents").addHandler(logging.StreamHandler())

app = create_app()
