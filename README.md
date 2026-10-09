# YT VidClipper

[![Live Demo](https://img.shields.io/badge/Live-Demo-brightgreen.svg)](https://yt-vidclipper-fe.vercel.app/)
[![Frontend Repo](https://img.shields.io/badge/Frontend-Repository-blue.svg)](https://github.com/divyanshusoni21/Yt-vidclipper-fe)


A Django web application that allows users to extract specific segments from YouTube videos by providing a YouTube URL and timestamp range. The system handles video downloading, clipping, and serving the processed clip back to the user for download.

## Postman collection document
[Click here](https://documenter.getpostman.com/view/18000926/2sBXcHiedL)

## Features

- Extract clips from YouTube videos using URL and timestamp range
- Background processing with real-time status updates
- Adjust clip playback speed from 0.25x to 4.0x (upload your own video or use a generated clip)

## Efficiently Handles :
- Cancellation of an ongoing process of clip cutting and video playback speed adjustment
- Automated periodic cleanup of expired media files and orphan temporary files via Celery Beat

## Required Software

- **Python 3.8+** - Programming language runtime
- **FFmpeg** - Video processing library (required for clipping functionality)
- **Redis** - In-memory data store and message broker (required for Celery task processing and crash tracking)


## Installation

### 1. Python Environment Setup

```bash
# Clone the repository
git clone <repository-url>
cd yt_helper

# Create virtual environment
python -m venv venv

# Activate virtual environment
# On macOS/Linux:
source venv/bin/activate
# On Windows:
venv\Scripts\activate

# Install Python dependencies
pip install -r requirements.txt
```

### 2. Environment Configuration

Create a `.env` file in the project root:

```env
# Django Settings
SECRET_KEY=your-secret-key-here
DEBUG=True

# Redis Configuration (optional - defaults to localhost)
REDIS_HOST=localhost
REDIS_PORT=6379
REDIS_DB=0

ADMIN_EMAIL=divyanshusoni061@gmail.com
ADMIN_PHONE=917372958746
FRONTEND_URL=http://localhost:8000

DEFAULT_PASSWORD=defualtPassword # default password used to create user

CSRF_TRUSTED_ORIGINS=http://127.1.1:8000,http://localhost:3000,http://localhost:5173

ALLOWED_HOSTS=127.0.0.1,localhost

CORS_ALLOWED_ORIGINS=http://localhost:3000,http://localhost:5173

BREVO_API_KEY=brevoapikey # used to send emails

PROXIES=http://username:password@domain,http://username:password@domain2 # required if using the project in a server

```

### 3. System Dependencies

Ensure FFmpeg and Redis are installed:

- **macOS**: `brew install ffmpeg redis`
- **Ubuntu/Debian**: `sudo apt install ffmpeg redis-server`
- **Windows**: Download from [ffmpeg.org](https://ffmpeg.org/download.html) and [redis.io](https://redis.io/download)

### 4. Database Setup

```bash
# Run database migrations
python manage.py migrate

# Create superuser (optional)
python manage.py createsuperuser
```

### 5. Verify Installation

```bash
# Test FFmpeg installation
ffmpeg -version

# Test Redis connection
redis-cli ping

# Test Django setup
python manage.py check
```

## Running the Application

### Development Mode

1. **Start Redis** (if not running as a service):
   ```bash
   redis-server
   ```

2. **Start Django development server**:
   ```bash
   python manage.py runserver
   ```

3. **Start Celery workers** (in separate terminal windows):

   - **Terminal 1: Video Processing Worker** (for heavy CPU/memory video clipping and speed adjustments):
     ```bash
     celery -A yt_helper worker -Q video -c 1 -l info --prefetch-multiplier=1 --max-tasks-per-child=10 --max-memory-per-child=300000
     ```

   - **Terminal 2: Default Worker + Beat Scheduler** (for lightweight network I/O, email delivery, and periodic cleanups):
     ```bash
     celery -A yt_helper worker --pool=threads -Q default -c 2 -B  -l info
     ```

4. **Access the application**:
   - Admin interface: http://127.0.0.1:8000/admin/
   - API endpoints: http://127.0.0.1:8000/api/

### Logs

Check application logs in the `log_files/` directory:
- `info.log` - General application information
- `warning.log` - Warnings and errors

---

## Background Task Architecture & Celery Deep Dive

> **Note:** This section details the production reliability patterns and performance choices implemented in [yt_helper/celery.py] and [home/tasks.py]. If you just want to run the project locally, the quick-start commands above are all you need!

The project splits background workloads into two distinct queues (`video` and `default`) and runs them with two tailored worker configurations.

```
                     ┌──────────────────┐
                     │  Django Web App  │
                     └────────┬─────────┘
                              │ Dispatches Tasks
                              ▼
                     ┌──────────────────┐
                     │   Redis Broker   │
                     └────┬────────┬────┘
                          │        │
           Queue: 'video' │        │ Queue: 'default'
                          ▼        ▼
       ┌──────────────────────┐  ┌─────────────────────────┐
       │     Video Worker     │  │ Default + Beat Worker   │
       │  (Prefork Pool, c=1) │  │  (Threads Pool, c=2, -B)│
       ├──────────────────────┤  ├─────────────────────────┤
       │ • Video clipping     │  │ • Transactional emails  │
       │ • Video speed edits  │  │ • Cancelled folder wipe │
       │ • Heavy FFmpeg jobs  │  │ • Periodic file cleanup │
       └──────────────────────┘  └─────────────────────────┘
```

---

### 1. Why Two Different Worker Pools & Concurrency Levels?

Different background jobs demand completely different server resources. Treating them the same causes heavy tasks to choke lightweight, time-sensitive tasks.

| Worker | Queue | Pool Type | Concurrency (`-c`) | Primary Tasks | Why This Setup? |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Video Worker** | `video` | `prefork` (processes) | `1` | Video downloading, clipping, re-encoding | Heavy CPU & RAM usage. Isolated OS processes bypass Python's GIL. Limiting to 1 prevents server CPU starvation and RAM spikes. |
| **Default Worker** | `default` | `threads` | `2` | Email delivery, delayed folder cleanup, periodic cron | Lightweight network & disk I/O. Threads use almost zero RAM. Embedded Celery Beat (`-B`) runs periodic maintenance without an extra daemon. |

- **Queue Isolation**: Video encoding jobs can take minutes. If emails and video jobs shared one queue, a burst of video requests would delay user verification or notification emails indefinitely.
- **Prefork vs. Threads**:
  - **Prefork (Separate OS Processes)**: FFmpeg and `yt-dlp` run intense media operations. Running in separate child processes provides true parallel compute and total memory isolation.
  - **Threads**: Network requests (e.g., calling the Brevo Email API) spend 99% of their time waiting for the remote server to respond. Threads handle concurrent waiting efficiently without allocating separate process memory footprints.
- **Embedded Beat (`-B`)**: Runs the periodic scheduler inside the default thread worker, saving the overhead of managing a third background daemon.

---

### 2. Deep Dive: Memory & Queue Management Flags

The video worker command includes critical stability flags:
```bash
celery -A yt_helper worker -Q video -c 1 -l info --prefetch-multiplier=1 --max-tasks-per-child=10 --max-memory-per-child=300000
```

#### A. `--prefetch-multiplier=1` (Fair Queue Distribution)
- **What is prefetching?** By default, Celery grabs a batch of tasks from Redis in advance (usually 4 tasks per worker slot) to reduce roundtrips.
- **Why it matters here:** For tasks that take seconds or minutes, batching is harmful. If Worker A prefetches 4 long video downloads, it will hoard them while an idle Worker B sits with nothing to do. Setting `--prefetch-multiplier=1` guarantees the worker only claims **one task at a time**, ensuring fair and immediate task distribution.

#### B. `--max-tasks-per-child=10` (Preventing Memory Leaks)
- **The problem:** Python media tools, C-extensions, and FFmpeg subprocesses frequently encounter memory fragmentation and gradual memory retention over time.
- **The solution:** Celery gracefully replaces the child worker process with a fresh one after every 10 tasks. This returns all process memory cleanly back to the operating system.

#### C. `--max-memory-per-child=300000` (Out-of-Memory Safeguard)
- **The problem:** Video resolutions vary wildly. A massive 4K video or complex re-encode could temporarily inflate memory usage.
- **The solution:** Sets an RSS memory limit (~300 MB). If a worker process crosses this ceiling after finishing a job, Celery retires it and spawns a fresh worker, preventing Out-Of-Memory (OOM) crashes on the server.

---

### 3. Graceful vs. Hard Limits: Soft Time Limits & Hard Time Limits

Every task has two distinct timers to keep workers from freezing:

- **Soft Time Limit** (`soft_time_limit=180` / 3 mins for video; 60s default):
  - When reached, Celery raises a standard Python exception: `SoftTimeLimitExceeded`.
  - **Why this is critical:** It gives the task a graceful chance to clean up! In [home/tasks.py], catching this exception immediately deletes partial download chunks and temp folders from disk before re-raising. Disk space is protected, and no orphan files are left behind.
- **Hard Time Limit** (`time_limit=240` / 4 mins for video; 120s default):
  - Acts as an uncompromised safety net. If a task freezes completely (for example, FFmpeg gets stuck in an un-interruptible C-level loop ignoring Python signals), Celery forcefully terminates the worker with `SIGKILL`. This ensures a rogue job will never permanently block a worker slot.

---

### 4. Smart Auto-Retries & Error Handling

Automatic retries are powerful, but blindly retrying every error is wasteful. The implementation distinguishes between temporary glitches and permanent failures:

- **Transient Errors (Safe to Retry)**:
  - Network timeouts, connection drops, or temporary third-party API hiccups retry automatically with **exponential backoff** (`retry_backoff=True`) and **random jitter** (`retry_jitter=True`). Jitter prevents the "thundering herd" problem where multiple retrying workers hammer an external service at the exact same second.
- **Permanent Errors (Skip Retries Immediately)**:
  - Specified via `dont_autoretry_for`:
    - `ExtractorError`, `GeoRestrictedError` (video blocked in country)
    - `UnavailableVideoError` (video deleted, private, or not found)
    - `UnsupportedError` (invalid URL format)
    - `ValueError`, `FileNotFoundError`, `ProcessingFailedException`
  - Retrying a deleted video or invalid URL will never succeed and only burns server bandwidth.
- **Unwrapping yt-dlp `DownloadError`**:
  - `yt-dlp` often wraps inner extraction exceptions in a generic `DownloadError`. The task inspects `exc.exc_info` to extract the true cause so permanent errors bypass retries immediately.

---

### 5. Crash Recovery, Late Acknowledgements & Poison-Pill Defense

Background workers must survive unexpected power loss, worker crashes, or OS kills without losing work or entering infinite crash loops.

#### A. Late Acknowledgment (`acks_late=True` & `reject_on_worker_lost=True`)
- Standard Celery acknowledges and removes a task from Redis the moment a worker picks it up. If the worker crashes mid-way, the task is lost forever.
- With `acks_late=True`, the message stays in Redis until the task **successfully finishes**. If the worker process abruptly dies, Redis re-queues the task so another worker can pick it up.

#### B. Safe Email Exception (`acks_late=False`)
- In `send_email_task`, `acks_late=False` is intentionally set. Unlike video downloads (which can safely be re-downloaded), duplicate emails sent to users are confusing and undesirable. The task acknowledges before sending so a crash right after the HTTP call cannot trigger duplicate emails.

#### C. Idempotency & Status Checking
- When a task is redelivered after a worker crash, the task first verifies the database record. If the user already cancelled the request (`CANCELLED`) or if it already finished (`COMPLETED`), it skips processing immediately.

#### D. Poison-Pill Protection (`record_crash_attempt`)
- **The danger:** If a corrupted video file causes FFmpeg or Python to crash with a segmentation fault every time, `acks_late=True` would re-queue the task forever, crashing workers in an endless loop.
- **The solution:** The task tracks worker startup counts in Redis cache (`celery-worker-starts:<task_id>`). If a task crashes the worker repeatedly beyond `maxWorkerCrashStarts`, the system intercepts it as a poison pill, marks the database status as `FAILED`, cleans up temporary files, and stops the crash loop.

---

### 6. Task State Tracking & Automated Scheduling

- **Accurate State Tracking (`task_track_started=True`)**:
  - By default, Celery reports tasks as `PENDING` until they finish or fail. Setting `task_track_started=True` allows the frontend and API to know when a task is actually actively executing (`STARTED`) versus waiting in line.
- **Automated Periodic File Cleanup (Celery Beat)**:
  - Celery Beat runs on a cron schedule (`crontab(minute=0, hour='*/6')`) defined in [yt_helper/celery.py].
  - Every 6 hours, `cleanup_old_files_task` cleans out video clips, user uploads, and orphan files older than `oldFileRetentionMinutes` (12 hours), ensuring the disk never fills up with abandoned files.

## Production Deploy Requirements (Must have)
- Javascript runtime [deno](https://deno.com/) installed on machine.
- Proxies bought from service providers like ([proxy-seller](https://proxy-seller.com/)). PS: ipv4 or residential proxies are recommended.


## Commands which should be run frequently
- pip install --upgrade yt-dlp
- pip install --upgrade yt-dlp-ejs
These updates the ytdlp package so that project runs smoothly

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Add tests for new functionality
5. Submit a pull request

## License

This project is licensed under the MIT License - see the LICENSE file for details.

## Support

For support and questions:
- Create an issue on GitHub
- Check the troubleshooting section above
- Review the application logs for error details


## Future features
1. multiple timerange clips  of one video
2. Support of twitter media download
