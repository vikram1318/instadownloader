#!/usr/bin/env python3
"""
wsgi.py — Production WSGI entry point.
Usage:
  gunicorn wsgi:app --workers 2 --threads 4 --bind 0.0.0.0:5050
  waitress-serve --port=5050 wsgi:app        (Windows)
"""
from app import app

if __name__ == "__main__":
    app.run()

