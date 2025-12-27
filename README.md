# YouTube Clipper

A Django web application that allows users to extract specific segments from YouTube videos by providing a YouTube URL and timestamp range. The system handles video downloading, clipping, and serving the processed clip back to the user for download.

## Features

- Extract clips from YouTube videos using URL and timestamp range
- Hybrid processing methods for optimal performance based on video duration
- Background processing with real-time status updates
- Comprehensive analytics and monitoring
- REST API with Django REST Framework
- Simple web interface for easy clip creation

## System Requirements

### Required Software

- **Python 3.8+** - Programming language runtime
- **FFmpeg** - Video processing library (required for clipping functionality)
- **Redis** - In-memory data store (required for background task processing)

### Hardware Requirements

- **Minimum**: 2GB RAM, 1GB free disk space
- **Recommended**: 4GB+ RAM, 10GB+ free disk space for video processing

## Installation

### 1. System Dependencies

#### macOS (using Homebrew)
```bash
# Install FFmpeg
brew install ffmpeg

# Install Redis (if not already installed)
brew install redis

# Start Redis service
brew services start redis
```

#### Ubuntu/Debian
```bash
# Install FFmpeg
sudo apt update
sudo apt install ffmpeg

# Install Redis
sudo apt install redis-server

# Start Redis service
sudo systemctl start redis-server
sudo systemctl enable redis-server
```

#### Windows
1. Download and install FFmpeg from https://ffmpeg.org/download.html
2. Download and install Redis from https://redis.io/download
3. Add FFmpeg to your system PATH

### 2. Python Environment Setup

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

### 3. Environment Configuration

Create a `.env` file in the project root:

```env
# Django Settings
SECRET_KEY=your-secret-key-here
DEBUG=True

# Redis Configuration (optional - defaults to localhost)
REDIS_HOST=localhost
REDIS_PORT=6379
REDIS_DB=0

# File Retention Settings (optional)
CLIP_RETENTION_HOURS=24
TEMP_FILE_RETENTION_HOURS=1

# Email Settings (optional)
EMAIL_HOST=smtp.gmail.com
EMAIL_PORT=587
EMAIL_HOST_USER=your-email@gmail.com
EMAIL_HOST_PASSWORD=your-app-password
DEFAULT_FROM_EMAIL=your-email@gmail.com
```

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
   python manage.py rqworker default
   ```

4. **Access the application**:
   - Web interface: http://127.0.0.1:8000/
   - Admin interface: http://127.0.0.1:8000/admin/
   - API endpoints: http://127.0.0.1:8000/api/

### Production Deployment

For production deployment, consider:

- Use a proper WSGI server like Gunicorn
- Set up a reverse proxy with Nginx
- Use a process manager like Supervisor for RQ workers
- Configure proper logging and monitoring
- Set up SSL certificates
- Use a production database like PostgreSQL

## API Usage

### Create a Clip Request

```bash
curl -X POST http://127.0.0.1:8000/api/clips/create/ \
  -H "Content-Type: application/json" \
  -d '{
    "youtubeUrl": "https://www.youtube.com/watch?v=VIDEO_ID",
    "startTime": "1:30",
    "endTime": "2:45"
  }'
```

### Check Clip Status

```bash
curl http://127.0.0.1:8000/api/clips/{requestId}/status/
```

### Download Processed Clip

```bash
curl -O http://127.0.0.1:8000/api/clips/{requestId}/download/
```

## Processing Methods

The application uses three hybrid processing methods:

1. **Method A (< 10 minutes)**: Download full video and clip with FFmpeg
2. **Method B (> 10 minutes)**: Use yt-dlp --download-sections for efficiency
3. **Method C (Fallback)**: FFmpeg stream processing with direct URLs

## File Structure

```
yt_helper/
├── home/                   # Main Django app
├── utility/               # Utility functions and mixins
├── yt_helper/            # Django project settings
├── media/                # Media files storage
│   ├── clips/           # Processed clips
│   └── temp/            # Temporary files
├── log_files/           # Application logs
├── requirements.txt     # Python dependencies
└── manage.py           # Django management script
```

## Troubleshooting

### Common Issues

1. **FFmpeg not found**:
   - Ensure FFmpeg is installed and in your system PATH
   - Test with: `ffmpeg -version`

2. **Redis connection failed**:
   - Ensure Redis server is running
   - Test with: `redis-cli ping`

3. **Permission errors**:
   - Check file permissions for media directories
   - Ensure the application has write access to media/clips and media/temp

4. **Video download fails**:
   - Check internet connectivity
   - Verify the YouTube URL is accessible
   - Some videos may be geo-restricted or private

### Logs

Check application logs in the `log_files/` directory:
- `info.log` - General application information
- `warning.log` - Warnings and errors

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



Future features
1. multiple clips of one video
2. speed up or down




# analytics
3 min long clip  83s
   720p 38mb 60s
   480p 21mb 23s
