import re
import yt_dlp
import os
import subprocess
import time
from typing import Dict, Any

from django.conf import settings
from django.utils import timezone

from .models import STATUS_CHOICES, VideoDetail
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
        if not self.validate_youtube_url(url):
            raise InvalidUrlException(f"Invalid YouTube URL: {url}")
        

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

    def __init__(self):

        # Ensure ffmpeg is installed
        self._check_ffmpeg()
    

    # YouTube URL patterns for validation, including /live/ URLs
    YOUTUBE_URL_PATTERNS = [
        r'(?:https?://)?(?:www\.)?youtube\.com/watch\?v=([a-zA-Z0-9_-]{11})',
        r'(?:https?://)?(?:www\.)?youtu\.be/([a-zA-Z0-9_-]{11})',
        r'(?:https?://)?(?:www\.)?youtube\.com/embed/([a-zA-Z0-9_-]{11})',
        r'(?:https?://)?(?:www\.)?youtube\.com/v/([a-zA-Z0-9_-]{11})',
        r'(?:https?://)?(?:www\.)?youtube\.com/live/([a-zA-Z0-9_-]{11})(?:\?.*)?'
    ]
    
    def validate_youtube_url(self, url: str) -> bool:
        """
        Validate if the provided URL is a valid YouTube URL
        
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
                'format': 'best[height<=720]',
                'download_ranges': download_range_func(None, [(startSec, endSec)]),
                'force_keyframes_at_cuts': True,
                'outtmpl': out720pPathAbsolute,
                'merge_output_format': 'mp4',
                'quiet': True,
                'overwrites': True,
                'no_warnings': True,
                'extract_flat': False,
                 #  Disable Cache (Be a "new" user every time)
                'cachedir': False,
                
                #  Rotate Clients (iOS is currently the most reliable)
                'extractor_args': {
                    'youtube': {
                        'player_client': ['ios', 'android', 'web'] 
                    }
                }
            }

            with yt_dlp.YoutubeDL(ydl_opts_step1) as ydl:
                info = ydl.extract_info(clipRequest.youtube_url, download=True)
                
                out720pPathActual = ydl.prepare_filename(info)
       
                
                # --- Create/Update VideoDetail object ---
                video_id = info.get('id', '')
                video_duration = info.get('duration', None)
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
                if video_duration is not None and endSec > video_duration:
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
                    timeout=300,
                    stdin=subprocess.DEVNULL,
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


class SpeedEditService:
    """Service for processing speed edit requests"""
    
    def __init__(self):
        self._check_ffmpeg()
    
    def _check_ffmpeg(self):
        """Checks if ffmpeg is installed"""
        if not shutil.which("ffmpeg"):
            raise FileNotFoundError("ffmpeg is not installed or not in your system's PATH")
    
    @job('default', timeout='5m')
    def process_speed_edit_request(self, speedEditRequest) -> bool:
        """
        Process a speed edit request from either uploaded video or existing clip.
        
        Args:
            speedEditRequest: SpeedEditRequest model instance
            
        Returns:
            bool: True if successful, False otherwise
        """
        try:
            tStart = time()
            logger.info(f"Starting speed edit processing for request {speedEditRequest.id}")
            
            # Get source video path
            sourcePath = speedEditRequest.get_source_path()
            if not sourcePath or not os.path.exists(sourcePath):
                raise ProcessingFailedException("Source video not found")
            
            # Get original file info
            originalSizeBytes = os.path.getsize(sourcePath)
            speedEditRequest.original_size = round(originalSizeBytes / (1024 * 1024), 2)
            
            # Get original duration using ffprobe
            originalDuration = self._get_video_duration(sourcePath)
            speedEditRequest.original_duration = originalDuration
            speedEditRequest.save(update_fields=['original_size', 'original_duration'])
            
            # Create output directory
            outputDir = os.path.join(settings.MEDIA_ROOT, 'speed_edited_videos', str(speedEditRequest.id))
            os.makedirs(outputDir, exist_ok=True)
            
            # Generate output filename
            speedStr = str(speedEditRequest.speed_factor).replace('.', '_')
            outputFilename = f"speed_{speedStr}x.mp4"
            outputPath = os.path.join(outputDir, outputFilename)
            
            # Build FFmpeg command
            speedFactor = speedEditRequest.speed_factor
            
            # Video filter: setpts = 1/speed * PTS
            videoFilter = f"setpts={1/speedFactor}*PTS"
            
            # Audio filter: chain atempo for speeds outside 0.5-2.0 range
            audioFilters = []
            remainingSpeed = speedFactor
            
            while remainingSpeed > 2.0:
                audioFilters.append("atempo=2.0")
                remainingSpeed /= 2.0
            while remainingSpeed < 0.5:
                audioFilters.append("atempo=0.5")
                remainingSpeed /= 0.5
            
            # Add the final/remaining factor
            if abs(remainingSpeed - 1.0) > 0.01:  # Skip if practically 1.0
                audioFilters.append(f"atempo={remainingSpeed}")
            
            audioFilterChain = ",".join(audioFilters) if audioFilters else "atempo=1.0"
            
            ffmpegCmd = [
                'ffmpeg',
                '-i', sourcePath,
                '-filter_complex', f'[0:v]{videoFilter}[v];[0:a]{audioFilterChain}[a]',
                '-map', '[v]',
                '-map', '[a]',
                '-c:v', 'libx264', '-preset', 'fast', '-crf', '23',
                '-c:a', 'aac',
                '-y',
                outputPath
            ]
            
            logger.info(f"Running FFmpeg command: {' '.join(ffmpegCmd)}")
            
            # Execute FFmpeg
            subprocess.run(
                ffmpegCmd,
                check=True,
                capture_output=True,
                text=True,
                timeout=600,  # 10 minute timeout
                stdin=subprocess.DEVNULL
            )
            
            # Verify output
            if not os.path.exists(outputPath) or os.path.getsize(outputPath) == 0:
                raise ProcessingFailedException("Output video is empty or missing")
            
            # Get output file info
            outputSizeBytes = os.path.getsize(outputPath)
            outputSizeMb = round(outputSizeBytes / (1024 * 1024), 2)
            outputDuration = int(originalDuration / speedFactor)
            
            # Save output file to model
            with open(outputPath, 'rb') as f:
                speedEditRequest.output_video.save(outputFilename, File(f), save=False)
            
            # Update model
            tEnd = time()
            speedEditRequest.output_size = outputSizeMb
            speedEditRequest.output_duration = outputDuration
            speedEditRequest.processing_time = round(tEnd - tStart, 2)
            speedEditRequest.status = STATUS_CHOICES[1][0]  # completed
            speedEditRequest.save()
            
            logger.info(f"Speed edit completed for request {speedEditRequest.id} in {tEnd - tStart:.2f}s")
            return True
            
        except Exception as e:
            logger.error(f"Speed edit failed for request {speedEditRequest.id}: {str(e)}")
            logger.error(traceback.format_exc())
            
            speedEditRequest.status = STATUS_CHOICES[2][0]  # failed
            speedEditRequest.error_message = str(e)
            speedEditRequest.save(update_fields=['status', 'error_message'])
            return False
    
    def _get_video_duration(self, videoPath: str) -> int:
        """Get video duration in seconds using ffprobe"""
        try:
            cmd = [
                'ffprobe',
                '-v', 'error',
                '-show_entries', 'format=duration',
                '-of', 'default=noprint_wrappers=1:nokey=1',
                videoPath
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            duration = float(result.stdout.strip())
            return int(duration)
        except Exception as e:
            logger.warning(f"Failed to get video duration: {str(e)}")
            return 0


