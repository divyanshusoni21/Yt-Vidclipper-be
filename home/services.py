import re
import yt_dlp
import os
import subprocess
import time
from typing import Dict, Any

from django.conf import settings
from django.utils import timezone

from .models import STATUS_CHOICES, VideoDetail, Clip
from .serializers import VideoDetailSerializer, ClipSerializer
import shutil
from time import time
from utility.functions import time_to_seconds, runSerializer
import traceback
from yt_helper.settings import logger
from django_rq import job
from django.core.files import File

class VideoNotAvailableException(Exception):
    """Exception raised when a video is not available or accessible."""
    pass


class InvalidUrlException(Exception):
    """Exception raised when a YouTube URL is invalid."""
    pass


class ProcessingFailedException(Exception):
    """Exception raised when video processing fails."""
    pass


class VideoInfoService:
    """Service class for handling YouTube video information extraction."""
    

    def getVideoInfo(self, url: str, ydlOpts: Dict[str, Any]=None) -> Dict[str, Any]:
        """
        Extract comprehensive video information using yt-dlp.
        
        Args:
            url (str): The YouTube URL
            
        Returns:
            Dict[str, Any]: Dictionary containing video information
            
        Raises:
            InvalidUrlException: If the URL is invalid
            VideoNotAvailableException: If the video is not accessible
        """
        if not self.validateYoutubeUrl(url):
            raise InvalidUrlException(f"Invalid YouTube URL: {url}")
        
        ydlOpts = ydlOpts or self.ydl_opts
            
        try:
            with yt_dlp.YoutubeDL(ydlOpts) as ydl:
                logger.info(f"Extracting video info for URL: {url}")
                info = ydl.extract_info(url, download=False)
                
                if not info:
                    raise VideoNotAvailableException(f"No information available for video: {url}")
                
                # Extract relevant information
                video_info = {
                    'title': info.get('title'),
                    'channel': info.get('channel'),
                    'channel_id': info.get('channel_id'),
                    'url': info.get('url'),
                }
                
                # Log successful extraction
                logger.info(f"Successfully extracted info for video: {video_info['title']}")
                
                return video_info
                
        except yt_dlp.DownloadError as e:
            error_msg = str(e)
            logger.error(f"yt-dlp download error for URL {url}: {error_msg}")
            
            # Handle specific error cases
            if 'private video' in error_msg.lower():
                raise VideoNotAvailableException(f"Video is private: {url}")
            elif 'video unavailable' in error_msg.lower():
                raise VideoNotAvailableException(f"Video is unavailable: {url}")
            elif 'age-restricted' in error_msg.lower():
                raise VideoNotAvailableException(f"Video is age-restricted: {url}")
            elif 'copyright' in error_msg.lower():
                raise VideoNotAvailableException(f"Video has copyright restrictions: {url}")
            elif 'geo-blocked' in error_msg.lower() or 'not available in your country' in error_msg.lower():
                raise VideoNotAvailableException(f"Video is geo-blocked: {url}")
            else:
                raise VideoNotAvailableException(f"Video not accessible: {error_msg}")
                
        except Exception as e:
            logger.error(f"Unexpected error extracting video info for URL {url}: {str(e)}")
            raise VideoNotAvailableException(f"Failed to extract video information: {str(e)}")
    

class ClipProcessingService:
    """Service class for processing video clips using hybrid methods."""
    
    # 10-minute threshold for processing method selection (in seconds)
    DURATION_THRESHOLD = 600

        # YouTube URL patterns for validation
    YOUTUBE_URL_PATTERNS = [
        r'(?:https?://)?(?:www\.)?youtube\.com/watch\?v=([a-zA-Z0-9_-]{11})',
        r'(?:https?://)?(?:www\.)?youtu\.be/([a-zA-Z0-9_-]{11})',
        r'(?:https?://)?(?:www\.)?youtube\.com/embed/([a-zA-Z0-9_-]{11})',
        r'(?:https?://)?(?:www\.)?youtube\.com/v/([a-zA-Z0-9_-]{11})',
    ]
    
    def validate_youtube_url(self,url: str) -> bool:
        """
        Validate if the provided URL is a valid YouTube URL.
        
        Args:
            url (str): The YouTube URL to validate
            
        Returns:
            bool: True if valid YouTube URL, False otherwise
        """
        if not url or not isinstance(url, str):
            return False
            
        # Check against all YouTube URL patterns
        for pattern in self.YOUTUBE_URL_PATTERNS:
            if re.match(pattern, url.strip()):
                return True
                
        return False
    
    
    def __init__(self):
        """Initialize the HybridProcessingService."""
        self.videoInfoService = VideoInfoService()
        self.base_ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'extract_flat': False,
        }
        
        # Ensure ffmpeg is installed
        self._check_ffmpeg()
    
    
    def _check_ffmpeg(self):
        """Checks if ffmpeg is installed and in the system's PATH."""
        if not shutil.which("ffmpeg"):
            raise FileNotFoundError(
                "ffmpeg is not installed or not in your system's PATH. "
                "Please install ffmpeg to use this script."
            )

    @job('default', timeout='5m')
    def process_clip_request(self, clipRequest) -> bool:
        """
        Process a clip request using the optimized hybrid method (Smart Cut).
        Downloads the 720p video using ytdlp and then uses ffmpeg to generate 480p video.
        
        Args:
            clipRequest: ClipRequest model instance
            
        Returns:
            bool: True if processing successful, False otherwise
        """
        try:
            from yt_dlp.utils import download_range_func
            t1 = time()
            
            # Log processing start
            self.log_processing_step(
                clipRequest, 
                'processing_start', 
                'info', 
                {'message': 'Starting clip processing (Smart Cut)'}
            )
            
            # Create directory for this request
            request_dir = os.path.join(settings.MEDIA_ROOT, 'clips', str(clipRequest.id))
            os.makedirs(request_dir, exist_ok=True)
            
            # Prepare output filenames
            out720pPath = "720p.mp4"
            out480pPath = "480p.mp4"

            # Absolute paths for file operations
            out720pPathAbsolute = os.path.join(request_dir, out720pPath)
            out480pPathAbsolute = os.path.join(request_dir, out480pPath)

            startSec = time_to_seconds(str(clipRequest.start_time))
            endSec = time_to_seconds(str(clipRequest.end_time))
            
            clipDurationSeconds = endSec - startSec

            # --- Phase 1: Download 720p (Smart Cut with yt-dlp) ---
            # This step downloads AND extracts metadata in one go
            
            ydl_opts_step1 = {
                **self.base_ydl_opts,
                'format': 'best[height<=720]',
                'download_ranges': download_range_func(None, [(startSec, endSec)]),
                'force_keyframes_at_cuts': True,
                'outtmpl': out720pPathAbsolute,
                'merge_output_format': 'mp4',
                'quiet': True,
                'overwrites': True,
            }
            
            with yt_dlp.YoutubeDL(ydl_opts_step1) as ydl:
                info = ydl.extract_info(clipRequest.youtube_url, download=True)
                out720pPathActual = ydl.prepare_filename(info)
                
                # --- Create/Update VideoDetail object ---
                video_id = info.get('id', '')
                video_duration = info.get('duration', 0)
                video_title = info.get('title', '')
                channel_name = info.get('channel', '')
                channel_id = info.get('channel_id', '')
                
                # Create or update VideoDetail
                videoDetailData = {
                    'video_id': video_id,
                    'video_duration': video_duration,
                    'video_title': video_title,
                    'channel_name': channel_name,
                    'channel_id': channel_id,
                }
                
                # Check if VideoDetail already exists for this video_id
                existingVideoDetail = VideoDetail.objects.filter(video_id=video_id).first()
                if existingVideoDetail:
                    videoDetail, _ = runSerializer(VideoDetailSerializer, videoDetailData, obj=existingVideoDetail)
                else:
                    videoDetail, _ = runSerializer(VideoDetailSerializer, videoDetailData)
                
                # Link VideoDetail to ClipRequest
                clipRequest.video_info = videoDetail
                clipRequest.clip_duration = clipDurationSeconds
                
                # Handle duration checks (cleanup logic)
                if endSec > video_duration:
                     # If user requested time beyond video length, update DB to reflect reality
                     # yt-dlp automatically clipped to end
                     clipRequest.end_time = info.get('duration_string', str(video_duration))
                
                clipRequest.save(update_fields=['video_info', 'clip_duration', 'end_time'])
            
            # Verify output file exists and has content
            if not os.path.exists(out720pPathActual) or os.path.getsize(out720pPathActual) == 0:
                # Fallback check if extension varied
                if not out720pPathActual.endswith('.mp4') and os.path.exists(out720pPathActual + '.mp4'):
                    out720pPathActual += '.mp4'
                else:
                    raise ProcessingFailedException("Phase 1 failed: Output clip file is empty or missing")
                
            clip720pBytes = os.path.getsize(out720pPathActual)
            clip720pMb = round(clip720pBytes / (1024 * 1024), 2)  # Convert bytes to MB
            # Create 720p Clip object using Django File object
           
            with open(out720pPathActual, 'rb') as f720p:
                clip720pFile = File(f720p, name=out720pPath)
                clip720pData = {
                    'clip_request': clipRequest.id,
                    'clip': clip720pFile,
                    'size': float(clip720pMb,),
                    'duration': clipDurationSeconds,
                    'resolution': '720p',
                }
                runSerializer(ClipSerializer, clip720pData)


            t2 = time()
            self.log_processing_step(
                clipRequest,
                'download_720p_clip',
                'info',
                {'message': f'Downloaded 720p clip in {t2 - t1:.2f}s'}
            )

            # --- Phase 2: Generate 480p from Local File ---
            t3 = time()
            
            ffmpegCmd480p = [
                'ffmpeg',
                '-i', out720pPathActual,
                '-vf', 'scale=-2:480',
                '-c:v', 'libx264', '-preset', 'fast', '-crf', '23',
                '-c:a', 'aac',
                '-y', out480pPathAbsolute
            ]
            
            try:
                subprocess.run(
                    ffmpegCmd480p, 
                    check=True, 
                    capture_output=True, 
                    text=True,
                    timeout=300
                )
            except (subprocess.TimeoutExpired, subprocess.CalledProcessError) as e:
                error_msg = f"FFmpeg 480p processing failed: {getattr(e, 'stderr', str(e))}"
                logger.error(error_msg)
                raise ProcessingFailedException(error_msg)
            
            # Verify output
            if not os.path.exists(out480pPathAbsolute) or os.path.getsize(out480pPathAbsolute) == 0:
                raise ProcessingFailedException("Output 480p file is empty or missing")
            
            t4 = time()
            self.log_processing_step(
                clipRequest,
                'generate_480p_clip',
                'info',
                {'message': f'Generated 480p clip in {t4 - t3:.2f}s'}
            )

            # --- Create Clip objects for 720p and 480p ---
            # Get file sizes in bytes and convert to MB
          
            clip480pBytes = os.path.getsize(out480pPathAbsolute)
            clip480pMb = round(clip480pBytes / (1024 * 1024), 2)  # Convert bytes to MB
            
            # Create 480p Clip object using Django File object
            with open(out480pPathAbsolute, 'rb') as f480p:
                clip480pFile = File(f480p, name=out480pPath)
                clip480pData = {
                    'clip_request': clipRequest.id,
                    'clip': clip480pFile,
                    'size': float(clip480pMb),
                    'duration': clipDurationSeconds,
                    'resolution': '480p',
                }
                runSerializer(ClipSerializer, clip480pData)
            
            # --- Final Success Update ---
            clipRequest.status = STATUS_CHOICES[1][0] # completed
            clipRequest.processed_at = timezone.now()
            clipRequest.total_time_taken = int(t4 - t1)
            clipRequest.save(update_fields=[
                'status', 'processed_at', 'total_time_taken'
            ])
            
            self.log_processing_step(
                clipRequest,
                'processing_complete',
                'success',
                {'message': f'Clip processing completed successfully, total time: {t4 - t1:.2f}s'}
            )
            return True

        except Exception as e:
            logger.error(traceback.format_exc())
            clipRequest.status = STATUS_CHOICES[2][0] # failed
            clipRequest.error_message = str(e)
            clipRequest.save(update_fields=['status', 'error_message'])
            
            self.log_processing_step(
                clipRequest,
                'processing_error',
                'error',
                {'error': str(e), 'exception_type': type(e).__name__}
            )
            return False

    def log_processing_step(self, clipRequest, step: str, status: str, details: dict) -> None:
        """
        Log a processing step to the clip request's processing log.
        
        Args:
            clipRequest: ClipRequest model instance
            step (str): The processing step identifier
            status (str): Status of the step ('info', 'warning', 'error', 'success')
            details (dict): Additional details about the step
        """
        try:
            # Ensure processing_log is a dict
            if not isinstance(clipRequest.processing_log, dict):
                clipRequest.processing_log = {}
            
            # Create log entry
            log_entry = {
                'timestamp': timezone.now().isoformat(),
                'step': step,
                'status': status,
                'details': details
            }
            
            # Add to processing log
            if 'steps' not in clipRequest.processing_log:
                clipRequest.processing_log['steps'] = []
            
            clipRequest.processing_log['steps'].append(log_entry)
            
            # Save the updated log
            clipRequest.save(update_fields=['processing_log'])
            
            # Also log to Django logger
            log_message = f"ClipRequest {clipRequest.id} - {step}: {details.get('message', str(details))}"
            if status == 'error':
                logger.error(log_message)
            elif status == 'warning':
                logger.warning(log_message)
            elif status == 'success':
                logger.info(f"SUCCESS: {log_message}")
            else:
                logger.info(log_message)
                
        except Exception as e:
            logger.error(f"Failed to log processing step for request {clipRequest.id}: {str(e)}")


