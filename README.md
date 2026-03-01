# YouTube Clipper

A Django web application that allows users to extract specific segments from YouTube videos by providing a YouTube URL and timestamp range. The system handles video downloading, clipping, and serving the processed clip back to the user for download.

## Postman collection document
[Click here](https://documenter.getpostman.com/view/18000926/2sBXcHiedL)

## Features

- Extract clips from YouTube videos using URL and timestamp range
- Background processing with real-time status updates
- Adjust clip playback speed from 0.25x to 4.0x (upload your own video or use a generated clip)

## Efficiently Handles :
- Cancellation of a ongoing process of clip cutting and video playback speed adjustment
- Cleaning up the media files from directory which are older than 24 hours with a cron job

## Required Software

- **Python 3.8+** - Programming language runtime
- **FFmpeg** - Video processing library (required for clipping functionality)
- **Redis** - In-memory data store (required for background task processing)


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

COOKIES_FILE=cookies.txt # youtube cookie file path, required if using the project in a server

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

3. **Start RQ worker** (in a separate terminal):
   ```bash
   python manage.py rqworker default --with-scheduler
   ```

4. **Access the application**:
   - Admin interface: http://127.0.0.1:8000/admin/
   - API endpoints: http://127.0.0.1:8000/api/

### Logs

Check application logs in the `log_files/` directory:
- `info.log` - General application information
- `warning.log` - Warnings and errors

## Production Deploy Requirements (Must have)
- Nodejs installed on machine.
- Proxies bought from service providers like ([proxy-seller](https://proxy-seller.com/)). PS: ipv6 or residential proxies are recommended.
- A youtube cookie file generated from "Get cookies.txt LOCALLY" extension on chrome browser.

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
