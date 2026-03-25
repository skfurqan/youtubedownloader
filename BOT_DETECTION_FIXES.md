# Bot Detection Fixes for yt-dlp

## Changes Made

### 1. **Bot Detection Bypass Options** ✓
Added the following yt-dlp options to both `/api/info` and download routes:
- `extractor_args`: {'youtube': {'skip': ['dash', 'hls']}} — Skip dash/hls formats to reduce complexity
- `http_headers`: Custom User-Agent header to avoid bot detection
- `sleep_interval`: 2 seconds — Add delay between requests
- `max_sleep_interval`: 5 seconds — Maximum random delay between requests

### 2. **Cookie File Support** ✓
- Checks for `cookies.txt` in your project root directory
- Automatically loads cookies if file exists (useful for authenticated downloads)
- Credentials are never exposed in code, only loaded from file if present

### 3. **Helper Function** ✓
Created `_get_base_ydl_opts(skip_download=False)` that:
- Centrally manages all bot detection configurations
- Handles cookie file setup
- Reduces code duplication between routes

### 4. **Updated Routes**

#### `/api/info` (POST)
- Now uses bot detection fixes
- Better reliability for extracting video metadata even when YouTube has bot detection active

#### `/api/download` (POST)  
- Now uses bot detection fixes in the background worker
- Returns async job-based progress tracking (unchanged API)
- Still supports SSE streaming via `/api/progress/<job_id>`
- Files can be downloaded via `/api/file/<job_id>` after completion

#### NEW: `/api/download-direct` (POST) ✓
- Synchronous direct download endpoint
- Downloads video and immediately serves file to browser as attachment
- File automatically downloads to user's Downloads folder
- Simple payload: `{"url": "...", "quality": "best"}` or specific height (e.g., "720")
- Returns the actual video file (not JSON)
- No progress tracking (simple synchronous operation)
- Better for quick downloads where progress isn't critical

## Usage

### For Video Info (with bot detection fixes):
```bash
curl -X POST http://localhost:5000/api/info \
  -H "Content-Type: application/json" \
  -d '{"url": "https://www.youtube.com/watch?v=..."}'
```

### Option 1: Download with Progress Tracking (original flow):
```bash
# Start download
curl -X POST http://localhost:5000/api/download \
  -H "Content-Type: application/json" \
  -d '{"url": "https://www.youtube.com/watch?v=...", "quality": "best"}'
# Returns: {"job_id": "...", "status": "queued"}

# Track progress (SSE stream)
curl http://localhost:5000/api/progress/{job_id}

# Download file when ready
curl http://localhost:5000/api/file/{job_id} -O
```

### Option 2: Direct Download (NEW - Single Request):
```bash
# One request does everything - downloads and serves file
curl -X POST http://localhost:5000/api/download-direct \
  -H "Content-Type: application/json" \
  -d '{"url": "https://www.youtube.com/watch?v=...", "quality": "720"}' \
  -O  # Automatically saves to Downloads folder
```

## Supported Quality Parameters

- `"best"` — Best available quality
- `"1080"`, `"720"`, `"480"`, etc. — Specific height in pixels

## Optional: Add Cookies

If you have authentication cookies:
1. Export cookies from browser (using a tool like "EditThisCookie")
2. Save as `cookies.txt` in the project root (format: Netscape cookie jar)
3. Restart Flask app — cookies will be automatically loaded

## Requirements

- yt-dlp with bot detection bypass support
- Flask with `send_file` capability
- Existing package dependencies from `requirements.txt`

## Testing

The app will continue to work as before, but with improved bot detection resistance on YouTube. If you still encounter "Sign in to confirm you're not a bot" errors:

1. Try the `/api/download-direct` endpoint
2. Ensure `cookies.txt` is properly formatted if using authentication
3. Update yt-dlp: `pip install --upgrade yt-dlp`
4. Check YouTube's rate limiting policies
