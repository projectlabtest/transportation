"""WSGI entry point for production servers."""

import database
from app import create_app

app = create_app()
database.init_db()
