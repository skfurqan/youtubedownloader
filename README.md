# VORTEX YouTube Downloader

Flask + yt-dlp web app for fetching YouTube metadata and downloading videos with live SSE progress updates.

## Project Structure

- app.py
- requirements.txt
- .gitignore
- README.md
- downloads/
- templates/index.html

## Quick Start

1. Create and activate a virtual environment.
2. Install dependencies:

	pip install -r requirements.txt

3. Run the app:

	python app.py

4. Open http://127.0.0.1:5000

## API Endpoints

- POST /api/info
- POST /api/download
- GET /api/progress/<job_id>
- GET /api/file/<job_id>
- GET /api/downloads