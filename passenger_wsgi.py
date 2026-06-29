import os
from dotenv import load_dotenv

# Load environment variables before importing the app
basedir = os.path.abspath(os.path.dirname(__file__))
load_dotenv(os.path.join(basedir, ".env"))

from app import app

# Expose the WSGI callable as 'application' for Gunicorn / uWSGI
application = app

if __name__ == "__main__":
    app.run()
