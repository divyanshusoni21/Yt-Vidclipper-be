import re
import yt_dlp
import os
import subprocess
import time

from typing import Dict, Any, Optional

from django.conf import settings
from django.utils import timezone

from .models import STATUS_CHOICES, VideoDetail,ClipRequest
from .serializers import VideoDetailSerializer, ClipSerializer
import shutil
from time import time
from utility.functions import time_to_seconds, runSerializer
import traceback
from yt_helper.settings import logger
from django_rq import job
from django.core.files import File
import random
from utility.variables import proxies, cookiesFile
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

    def extract_video_id(self, url: str) -> Optional[str]:
        """
        Extract YouTube video ID from URL using regex patterns.
        
        Args:
            url (str): The YouTube URL
            
        Returns:
            Optional[str]: Video ID if found, None otherwise
        """
        if not url or not isinstance(url, str):
            return None
        
        for pattern in self.YOUTUBE_URL_PATTERNS:
            match = re.match(pattern, url.strip())
            if match:
                return match.group(1)
        
        return None
    
    def get_proxy(self) -> str:
        
        # get latest proxy used in clip request
        latestUsedProxy = ""
        clipRequest = ClipRequest.objects.order_by('-created_at').first()
        if clipRequest:
            latestUsedProxy = clipRequest.proxy
        

        if proxies:
            allProxies = proxies.copy()
            if latestUsedProxy:    
                if latestUsedProxy in allProxies:
                    allProxies.remove(latestUsedProxy)
                return random.choice(allProxies)
            else:
                return random.choice(allProxies)
        else:
            return ""

    def process_dual_input_clip(self, videoUrl: str, audioUrl: str, startSec: int, duration: int, proxyUrl: str,out720pPathAbsolute: str,out480pPathAbsolute: str) -> bool:
        """
        Takes separate video and audio URLs and generates 720p and 480p clips
        using an ISP Proxy to prevent IP blocks.
        """
        cmd = [
            'ffmpeg',
            '-y',               # Overwrite existing files
            # '-hide_banner',     # Clean up logs
            # '-loglevel', 'error', 
            
            # --- INPUT 0: Video Stream ---
            '-http_proxy', proxyUrl, # Use Proxy for Video
            '-ss', str(startSec),    # Seek on remote server
            '-t', str(duration),      # Duration to download
            '-i', videoUrl,
            
            # --- INPUT 1: Audio Stream ---
            '-http_proxy', proxyUrl, # Use Proxy for Audio
            '-ss', str(startSec),    
            '-t', str(duration),
            '-i', audioUrl,
            
            # --- FILTER COMPLEX ---
            # Splitting and Scaling
            '-filter_complex', 
            '[0:v]split=2[v_in_720][v_in_480];'
            '[v_in_720]scale=-2:720[v_out_720];'
            '[v_in_480]scale=-2:480[v_out_480]',
            
            # --- OUTPUT 1: 720p ---
            '-map', '[v_out_720]',    
            '-map', '1:a',            
            '-c:v', 'libx264',        
            '-preset', 'superfast',   # more the faster more the size of clip, options : ultrafast,superfast, fast, medium, slow, veryslow
            '-crf', '18',             
            '-c:a', 'aac',            
            out720pPathAbsolute,
            
            # --- OUTPUT 2: 480p ---
            '-map', '[v_out_480]',    
            '-map', '1:a',            
            '-c:v', 'libx264',
            '-preset', 'superfast',   # more the faster more the size of clip, options : ultrafast,superfast, fast, medium, slow, veryslow
            '-crf', '23',             
            '-c:a', 'aac',
            out480pPathAbsolute
        ]

        logger.info(f"Processing Clips .....")
        try:
            subprocess.run(cmd, 
                        check=True,
                        capture_output=True,
                        text=True,
                        timeout=300,
                        stdin=subprocess.DEVNULL,
            )
            logger.info(f"Success! Generated 720p & 480p.")
        except subprocess.CalledProcessError as e:
            raise ProcessingFailedException(f"FFmpeg Failed with error : {traceback.format_exc()}")


    def save_video_info(self, info: dict, clipRequest:ClipRequest,clipDurationSeconds: int,endSec: int) -> bool:
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
    
    def download_and_create_clips(self, clipRequest:ClipRequest, startSec: int, endSec: int, clipDurationSeconds: int,out720pPathAbsolute: str,out480pPathAbsolute: str) -> bool:

        proxy = self.get_proxy()

        ydlOpts = {
            'quiet': True,
            'force_ipv6': True,
            'format': 'best[height<=720][protocol^=http]',
            'cookiefile': cookiesFile, # important
            'js_runtimes': { 'node': {}}, # important  
            'no_warnings': True,
            'extract_flat': False, 
        }
        if proxy:
            ydlOpts['proxy'] = proxy

        videoUrl = None
        audioUrl = None

        
        with yt_dlp.YoutubeDL(ydlOpts) as ydl:
            info = ydl.extract_info(clipRequest.youtube_url, download=False) # download = false is important
            
            # Check if we got separate streams or a single combined stream
            if 'requested_formats' in info:
                # Separate streams found (High Quality)
                for f in info['requested_formats']:
                    if f['vcodec'] != 'none':
                        videoUrl = f['url']
                    elif f['acodec'] != 'none':
                        audioUrl = f['url']
            else:
                # Fallback to single stream if separate ones aren't available
                # pass that same URL to both video_url and audio_url arguments in ffmpeg command.
                videoUrl = info['url']
                audioUrl = info['url']
            
            self.save_video_info(info, clipRequest, clipDurationSeconds, endSec)

        if not videoUrl or not audioUrl:
            raise ProcessingFailedException("Could not find separate video and audio streams.")

        self.process_dual_input_clip(videoUrl, audioUrl, startSec, clipDurationSeconds, proxy, out720pPathAbsolute, out480pPathAbsolute)

        return proxy
    
    def create_clip_object(self, outPathAbsolute: str, clipRequest:ClipRequest, clipDurationSeconds: int, resolution: str) -> bool:
        clipBytes = os.path.getsize(outPathAbsolute)
        clipMb = round(clipBytes / (1024 * 1024), 2)  # Convert bytes to MB
        
        # Create Clip object using Django File object
        with open(outPathAbsolute, 'rb') as f:
            clipFile = File(f, name=os.path.basename(outPathAbsolute))
            clipData = {
                'clip_request': clipRequest.id,
                'clip': clipFile,
                'size': float(clipMb),
                'duration': clipDurationSeconds,
                'resolution': resolution,
            }
            clipObj, _ = runSerializer(ClipSerializer, clipData)
        return clipObj

    @job('default', timeout='5m')
    def process_clip_request(self, clipRequest:ClipRequest) -> bool:
        """
        Process a clip request using the optimized hybrid method (Smart Cut).
        Downloads the 720p video using ytdlp and then uses ffmpeg to generate 480p video.
        
        Args:
            clipRequest: ClipRequest model instance
            
        Returns:
            bool: True if processing successful, False otherwise
        """
        try:
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
    
            t2 = time()
            
            proxy = self.download_and_create_clips(clipRequest, startSec, endSec, clipDurationSeconds, out720pPathAbsolute, out480pPathAbsolute)

            t3 = time()
            
            self.log_processing_step(
                clipRequest,
                'download_720p_clip',
                'info',
                {'message': f'Downloaded 720p clip in {t3 - t2:.2f}s'}
            )
            
            # Verify output file exists and has content
            if not os.path.exists(out720pPathAbsolute) or os.path.getsize(out720pPathAbsolute) == 0:
                    raise ProcessingFailedException("Phase 1 failed: Output clip file is empty or missing")
                
            # --- Create Clip object for 720p ---
            self.create_clip_object(out720pPathAbsolute, clipRequest, clipDurationSeconds, '720p')
              
            # Verify output
            if not os.path.exists(out480pPathAbsolute) or os.path.getsize(out480pPathAbsolute) == 0:
                raise ProcessingFailedException("Output 480p file is empty or missing")
            # --- Create Clip object for 480p ---
            self.create_clip_object(out480pPathAbsolute, clipRequest, clipDurationSeconds, '480p')

            t4 = time()
            # --- Final Success Update ---
            clipRequest.status = STATUS_CHOICES[1][0] # completed
            clipRequest.processed_at = timezone.now()
            clipRequest.total_time_taken = int(t4 - t1)
            clipRequest.proxy = proxy
            clipRequest.save(update_fields=[
                'status', 'processed_at', 'total_time_taken', 'proxy'
            ])
            
            t4 = time()
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


