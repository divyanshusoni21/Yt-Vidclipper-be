import re
import logging
import yt_dlp
import os
import subprocess
import time
from typing import Dict, Optional, Any
from urllib.parse import urlparse, parse_qs
from django.conf import settings
from django.utils import timezone
from django.db import models

logger = logging.getLogger(__name__)


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
    
    # YouTube URL patterns for validation
    YOUTUBE_URL_PATTERNS = [
        r'(?:https?://)?(?:www\.)?youtube\.com/watch\?v=([a-zA-Z0-9_-]{11})',
        r'(?:https?://)?(?:www\.)?youtu\.be/([a-zA-Z0-9_-]{11})',
        r'(?:https?://)?(?:www\.)?youtube\.com/embed/([a-zA-Z0-9_-]{11})',
        r'(?:https?://)?(?:www\.)?youtube\.com/v/([a-zA-Z0-9_-]{11})',
    ]
    
    def __init__(self):
        """Initialize the VideoInfoService with yt-dlp options."""
        self.ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'extract_flat': False,
            'skip_download': True,
        }
    
    def validateYoutubeUrl(self, url: str) -> bool:
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
    
    def extractVideoId(self, url: str) -> str:
        """
        Extract the video ID from a YouTube URL.
        
        Args:
            url (str): The YouTube URL
            
        Returns:
            str: The extracted video ID
            
        Raises:
            InvalidUrlException: If the URL is invalid or video ID cannot be extracted
        """
        if not self.validateYoutubeUrl(url):
            raise InvalidUrlException(f"Invalid YouTube URL: {url}")
            
        # Try to extract video ID using regex patterns
        for pattern in self.YOUTUBE_URL_PATTERNS:
            match = re.match(pattern, url.strip())
            if match:
                return match.group(1)
        
        # Fallback: try to extract from query parameters
        try:
            parsed_url = urlparse(url)
            if 'youtube.com' in parsed_url.netloc:
                query_params = parse_qs(parsed_url.query)
                if 'v' in query_params:
                    return query_params['v'][0]
        except Exception as e:
            logger.error(f"Error parsing URL {url}: {str(e)}")
            
        raise InvalidUrlException(f"Could not extract video ID from URL: {url}")
    
    def getVideoInfo(self, url: str) -> Dict[str, Any]:
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
            
        try:
            with yt_dlp.YoutubeDL(self.ydl_opts) as ydl:
                logger.info(f"Extracting video info for URL: {url}")
                info = ydl.extract_info(url, download=False)
                
                if not info:
                    raise VideoNotAvailableException(f"No information available for video: {url}")
                
                # Extract relevant information
                video_info = {
                    'id': info.get('id'),
                    'title': info.get('title'),
                    'duration': info.get('duration'),  # in seconds
                    'description': info.get('description'),
                    'upload_date': info.get('upload_date'),
                    'uploader': info.get('uploader'),
                    'uploader_id': info.get('uploader_id'),
                    'channel': info.get('channel'),
                    'channel_id': info.get('channel_id'),
                    'channel_url': info.get('channel_url'),
                    'view_count': info.get('view_count'),
                    'like_count': info.get('like_count'),
                    'thumbnail': info.get('thumbnail'),
                    'webpage_url': info.get('webpage_url'),
                    'availability': info.get('availability'),
                    'age_limit': info.get('age_limit', 0),
                    'is_live': info.get('is_live', False),
                    'was_live': info.get('was_live', False),
                }
                
                # Log successful extraction
                logger.info(f"Successfully extracted info for video: {video_info['id']} - {video_info['title']}")
                
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
    
    def getChannelInfo(self, url: str) -> Dict[str, Any]:
        """
        Extract channel information from a YouTube video URL.
        
        Args:
            url (str): The YouTube URL
            
        Returns:
            Dict[str, Any]: Dictionary containing channel information
            
        Raises:
            InvalidUrlException: If the URL is invalid
            VideoNotAvailableException: If the video/channel is not accessible
        """
        video_info = self.getVideoInfo(url)
        
        channel_info = {
            'channel_id': video_info.get('channel_id'),
            'channel_name': video_info.get('channel') or video_info.get('uploader'),
            'channel_url': video_info.get('channel_url'),
            'uploader': video_info.get('uploader'),
            'uploader_id': video_info.get('uploader_id'),
        }
        
        logger.info(f"Extracted channel info: {channel_info['channel_name']} ({channel_info['channel_id']})")
        
        return channel_info

class HybridProcessingService:
    """Service class for processing video clips using hybrid methods."""
    
    # 10-minute threshold for processing method selection (in seconds)
    DURATION_THRESHOLD = 600
    
    def __init__(self):
        """Initialize the HybridProcessingService."""
        self.videoInfoService = VideoInfoService()
        
        # Base yt-dlp options
        self.base_ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'extract_flat': False,
        }
        
        # Ensure media directories exist
        self._ensure_media_directories()
    
    def _ensure_media_directories(self):
        """Ensure media directories exist."""
        media_root = getattr(settings, 'MEDIA_ROOT', 'media')
        clips_dir = os.path.join(media_root, 'clips')
        temp_dir = os.path.join(media_root, 'temp')
        
        os.makedirs(clips_dir, exist_ok=True)
        os.makedirs(temp_dir, exist_ok=True)
    
    def processClipRequest(self, clipRequest) -> bool:
        """
        Process a clip request using the appropriate hybrid method.
        
        Args:
            clipRequest: ClipRequest model instance
            
        Returns:
            bool: True if processing successful, False otherwise
        """
        try:
            # Update status to processing
            clipRequest.status = 'processing'
            clipRequest.save()
            
            # Log processing start
            self.logProcessingStep(
                clipRequest, 
                'processing_start', 
                'info', 
                {'message': 'Starting clip processing'}
            )
            
            # Get video info if not already available
            if not clipRequest.video_duration:
                videoInfo = self.videoInfoService.getVideoInfo(clipRequest.youtube_url)
                clipRequest.video_duration = videoInfo.get('duration')
                clipRequest.original_title = videoInfo.get('title')
                clipRequest.channel_name = videoInfo.get('channel')
                clipRequest.channel_id = videoInfo.get('channel_id')
                clipRequest.save()
            
            # Determine processing method
            processingMethod = self.determineProcessingMethod(clipRequest.video_duration)
            clipRequest.processing_method = processingMethod
            clipRequest.save()
            
            # Log method selection
            self.logProcessingStep(
                clipRequest,
                'method_selection',
                'info',
                {
                    'selected_method': processing_method,
                    'video_duration': clipRequest.video_duration,
                    'threshold': self.DURATION_THRESHOLD
                }
            )
            
            # Execute the appropriate processing method
            success = False
            if processingMethod == 'download_and_clip':
                success = self.methodA_downloadAndClip(clipRequest)
            elif processingMethod == 'download_sections':
                success = self.methodB_downloadSections(clipRequest)
                # If Method B fails, try Method C as fallback
                if not success:
                    self.logProcessingStep(
                        clipRequest,
                        'fallback_to_method_c',
                        'warning',
                        {'message': 'Method B failed, trying Method C as fallback'}
                    )
                    clipRequest.processingMethod = 'ffmpeg_stream'
                    clipRequest.save()
                    success = self.methodC_ffmpegStream(clipRequest)
            else:  # ffmpeg_stream
                success = self.methodC_ffmpegStream(clipRequest)
            
            # Update final status
            if success:
                clipRequest.status = 'completed'
                clipRequest.processed_at = timezone.now()
                self.logProcessingStep(
                    clipRequest,
                    'processing_complete',
                    'success',
                    {'message': 'Clip processing completed successfully'}
                )
            else:
                clipRequest.status = 'failed'
                self.logProcessingStep(
                    clipRequest,
                    'processing_failed',
                    'error',
                    {'message': 'All processing methods failed'}
                )
            
            clipRequest.save()
            return success
            
        except Exception as e:
            logger.error(f"Error processing clip request {clipRequest.id}: {str(e)}")
            clipRequest.status = 'failed'
            clipRequest.error_message = str(e)
            clipRequest.save()

            
            self.logProcessingStep(
                clipRequest,
                'processing_error',
                'error',
                {'error': str(e), 'exception_type': type(e).__name__}
            )
            
            return False
    
    def determineProcessingMethod(self, videoDuration: int) -> str:
        """
        Determine the processing method based on video duration.
        
        Args:
            videoDuration (int): Video duration in seconds
            
        Returns:
            str: Processing method ('download_and_clip', 'download_sections', or 'ffmpeg_stream')
        """
        if videoDuration is None:
            # Default to download_sections if duration is unknown
            return 'download_sections'
        
        if videoDuration < self.DURATION_THRESHOLD:
            return 'download_and_clip'
        else:
            return 'download_sections'
    
    def methodA_downloadAndClip(self, clipRequest) -> bool:
        """
        Method A: Download full video and clip using FFmpeg.
        Used for videos under 10 minutes.
        
        Args:
            clipRequest: ClipRequest model instance
            
        Returns:
            bool: True if successful, False otherwise
        """
        try:
            self.logProcessingStep(
                clipRequest,
                'method_a_start',
                'info',
                {'message': 'Starting Method A: Download and Clip'}
            )
            
            # Create directories for this request
            media_root = getattr(settings, 'MEDIA_ROOT', 'media')
            request_dir = os.path.join(media_root, 'clips', str(clipRequest.id))
            temp_dir = os.path.join(media_root, 'temp', str(clipRequest.id))
            os.makedirs(request_dir, exist_ok=True)
            os.makedirs(temp_dir, exist_ok=True)
            
            # Download full video
            temp_video_path = os.path.join(temp_dir, 'full_video.%(ext)s')
            ydl_opts = {
                **self.base_ydl_opts,
                'outtmpl': temp_video_path,
                'format': 'best[height<=1080]',
            }
            
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                self.logProcessingStep(
                    clipRequest,
                    'method_a_download',
                    'info',
                    {'message': 'Downloading full video'}
                )
                
                ydl.download([clipRequest.youtube_url])
            
            # Find the downloaded file (yt-dlp adds extension)
            downloadedFiles = [f for f in os.listdir(temp_dir) if f.startswith('full_video.')]
            if not downloadedFiles:
                raise ProcessingFailedException("Downloaded video file not found")
            
            downloadedVideo = os.path.join(temp_dir, downloaded_files[0])
            
            # Clip the video using FFmpeg
            output_filename = f"clip_{clipRequest.start_time}_{clipRequest.end_time}.mp4"
            output_path = os.path.join(request_dir, output_filename)
            
            clip_duration = clipRequest.end_time - clipRequest.start_time
            
            ffmpegCmd = [
                'ffmpeg',
                '-i', downloadedVideo,
                '-ss', str(clipRequest.start_time),
                '-t', str(clip_duration),
                '-c', 'copy',  # Copy streams without re-encoding for speed
                '-avoid_negative_ts', 'make_zero',
                '-y',  # Overwrite output file
                output_path
            ]
            
            self.logProcessingStep(
                clipRequest,
                'method_a_clip',
                'info',
                {
                    'message': 'Clipping video with FFmpeg',
                    'start_time': clipRequest.start_time,
                    'duration': clip_duration,
                    'command': ' '.join(ffmpegCmd)
                }
            )
            
            result = subprocess.run(ffmpegCmd, capture_output=True, text=True)
            
            if result.returncode != 0:
                raise ProcessingFailedException(f"FFmpeg failed: {result.stderr}")
            
            # Verify output file exists and has content
            if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
                raise ProcessingFailedException("Output clip file is empty or missing")
            
            # Update clip request with file info
            clipRequest.file_path = os.path.relpath(output_path, getattr(settings, 'MEDIA_ROOT', 'media'))
            clipRequest.file_size = os.path.getsize(output_path)
            clipRequest.save()
            
            # Clean up temporary files
            try:
                os.remove(downloaded_video)
                os.rmdir(temp_dir)
            except Exception as e:
                logger.warning(f"Failed to clean up temp files for request {clipRequest.id}: {str(e)}")
            
            self.logProcessingStep(
                clipRequest,
                'method_a_success',
                'success',
                {
                    'message': 'Method A completed successfully',
                    'file_size': clipRequest.file_size,
                    'output_path': clipRequest.file_path
                }
            )
            
            return True
            
        except Exception as e:
            logger.error(f"Method A failed for request {clipRequest.id}: {str(e)}")
            self.logProcessingStep(
                clipRequest,
                'method_a_error',
                'error',
                {'error': str(e), 'exception_type': type(e).__name__}
            )
            return False
    
    def methodB_downloadSections(self, clipRequest) -> bool:
        """
        Method B: Download specific sections using yt-dlp --download-sections.
        Used for videos over 10 minutes.
        
        Args:
            clipRequest: ClipRequest model instance
            
        Returns:
            bool: True if successful, False otherwise
        """
        try:
            self.logProcessingStep(
                clipRequest,
                'method_b_start',
                'info',
                {'message': 'Starting Method B: Download Sections'}
            )
            
            # Create directory for this request
            media_root = getattr(settings, 'MEDIA_ROOT', 'media')
            request_dir = os.path.join(media_root, 'clips', str(clipRequest.id))
            os.makedirs(request_dir, exist_ok=True)
            
            # Prepare output filename
            output_filename = f"clip_{clipRequest.start_time}_{clipRequest.end_time}.mp4"
            output_path = os.path.join(request_dir, output_filename)
            
            # Format section for yt-dlp (start_time-end_time)
            section = f"{clipRequest.start_time}-{clipRequest.end_time}"
            
            ydl_opts = {
                **self.base_ydl_opts,
                'outtmpl': output_path.replace('.mp4', '.%(ext)s'),
                'format': 'best[height<=1080]',
                'download_sections': section,
            }
            
            self.logProcessingStep(
                clipRequest,
                'method_b_download',
                'info',
                {
                    'message': 'Downloading video section',
                    'section': section,
                    'start_time': clipRequest.start_time,
                    'end_time': clipRequest.end_time
                }
            )
            
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([clipRequest.youtube_url])
            
            # Find the downloaded file (yt-dlp might add different extension)
            downloaded_files = [f for f in os.listdir(request_dir) if f.startswith(f"clip_{clipRequest.start_time}_{clipRequest.end_time}.")]
            if not downloaded_files:
                raise ProcessingFailedException("Downloaded section file not found")
            
            actual_output = os.path.join(request_dir, downloaded_files[0])
            
            # If the file doesn't have .mp4 extension, rename it
            if not actual_output.endswith('.mp4'):
                os.rename(actual_output, output_path)
                actual_output = output_path
            
            # Verify output file exists and has content
            if not os.path.exists(actual_output) or os.path.getsize(actual_output) == 0:
                raise ProcessingFailedException("Output clip file is empty or missing")
            
            # Update clip request with file info
            clipRequest.file_path = os.path.relpath(actual_output, getattr(settings, 'MEDIA_ROOT', 'media'))
            clipRequest.file_size = os.path.getsize(actual_output)
            clipRequest.save()
            
            self.logProcessingStep(
                clipRequest,
                'method_b_success',
                'success',
                {
                    'message': 'Method B completed successfully',
                    'file_size': clipRequest.file_size,
                    'output_path': clipRequest.file_path
                }
            )
            
            return True
            
        except Exception as e:
            logger.error(f"Method B failed for request {clipRequest.id}: {str(e)}")
            self.logProcessingStep(
                clipRequest,
                'method_b_error',
                'error',
                {'error': str(e), 'exception_type': type(e).__name__}
            )
            return False
    
    def methodC_ffmpegStream(self, clipRequest) -> bool:
        """
        Method C: FFmpeg stream processing with direct URLs.
        Used as fallback when Method B fails.
        
        Args:
            clipRequest: ClipRequest model instance
            
        Returns:
            bool: True if successful, False otherwise
        """
        try:
            self.logProcessingStep(
                clipRequest,
                'method_c_start',
                'info',
                {'message': 'Starting Method C: FFmpeg Stream Processing'}
            )
            
            # Get direct streaming URL using yt-dlp
            ydl_opts = {
                **self.base_ydl_opts,
                'format': 'best[height<=1080]',
            }
            
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(clipRequest.youtube_url, download=False)
                if not info or 'url' not in info:
                    raise ProcessingFailedException("Could not extract streaming URL")
                
                streamingUrl = info['url']
            
            # Create directory for this request
            media_root = getattr(settings, 'MEDIA_ROOT', 'media')
            request_dir = os.path.join(media_root, 'clips', str(clipRequest.id))
            os.makedirs(request_dir, exist_ok=True)
            
            # Prepare output filename
            output_filename = f"clip_{clipRequest.start_time}_{clipRequest.end_time}.mp4"
            output_path = os.path.join(request_dir, output_filename)
            
            clip_duration = clipRequest.end_time - clipRequest.start_time
            
            # Use FFmpeg to seek and download only the required segment
            ffmpeg_cmd = [
                'ffmpeg',
                '-ss', str(clipRequest.start_time),  # Seek to start time
                '-i', streamingUrl,
                '-t', str(clip_duration),  # Duration of clip
                '-c', 'copy',  # Copy streams without re-encoding
                '-avoid_negative_ts', 'make_zero',
                '-y',  # Overwrite output file
                output_path
            ]
            
            self.logProcessingStep(
                clipRequest,
                'method_c_process',
                'info',
                {
                    'message': 'Processing stream with FFmpeg',
                    'start_time': clipRequest.start_time,
                    'duration': clip_duration,
                    'command': ' '.join(ffmpeg_cmd[:6] + ['[STREAMING_URL]'] + ffmpeg_cmd[7:])  # Hide actual URL in logs
                }
            )
            
            result = subprocess.run(ffmpeg_cmd, capture_output=True, text=True)
            
            if result.returncode != 0:
                raise ProcessingFailedException(f"FFmpeg stream processing failed: {result.stderr}")
            
            # Verify output file exists and has content
            if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
                raise ProcessingFailedException("Output clip file is empty or missing")
            
            # Update clip request with file info
            clipRequest.file_path = os.path.relpath(output_path, getattr(settings, 'MEDIA_ROOT', 'media'))
            clipRequest.file_size = os.path.getsize(output_path)
            clipRequest.save()
            
            self.logProcessingStep(
                clipRequest,
                'method_c_success',
                'success',
                {
                    'message': 'Method C completed successfully',
                    'file_size': clipRequest.file_size,
                    'output_path': clipRequest.file_path
                }
            )
            
            return True
            
        except Exception as e:
            logger.error(f"Method C failed for request {clipRequest.id}: {str(e)}")
            self.logProcessingStep(
                clipRequest,
                'method_c_error',
                'error',
                {'error': str(e), 'exception_type': type(e).__name__}
            )
            return False
    
    def logProcessingStep(self, clipRequest, step: str, status: str, details: dict) -> None:
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


# TODO: Uncomment when ready to implement analytics
# class AnalyticsService:
    # """Service class for comprehensive tracking and analytics."""
    
    # def __init__(self):
    #     """Initialize the AnalyticsService."""
    #     self.video_info_service = VideoInfoService()
    
    # def recordClipRequest(self, clipRequest, userInfo: dict = None) -> 'ClipAnalytics':
    #     """
    #     Create analytics entry for a new clip request.
        
    #     Args:
    #         clipRequest: ClipRequest model instance
    #         userInfo (dict): Dictionary containing user information (IP, user agent, referrer)
            
    #     Returns:
    #         ClipAnalytics: Created analytics instance
    #     """
    #     try:
    #         # Import here to avoid circular imports
    #         from .models import ClipAnalytics
            
    #         # Extract video ID from URL
    #         video_id = self.video_info_service.extractVideoId(clipRequest.youtube_url)
            
    #         # Calculate clip duration and percentage
    #         clip_duration = clipRequest.end_time - clipRequest.start_time
    #         clip_percentage = 0.0
    #         if clipRequest.video_duration and clipRequest.video_duration > 0:
    #             clip_percentage = (clip_duration / clipRequest.video_duration) * 100
            
    #         # Get system performance metrics
    #         system_metrics = self._getSystemMetrics()
            
    #         # Create analytics entry
    #         analytics = ClipAnalytics.objects.create(
    #             clip_request=clipRequest,
    #             video_id=video_id,
    #             video_duration=clipRequest.video_duration or 0,
    #             clip_duration=clip_duration,
    #             clip_percentage=clip_percentage,
    #             processing_method=clipRequest.processing_method or 'unknown',
    #             channel_name=clipRequest.channel_name,
    #             channel_id=clipRequest.channel_id,
    #             user_ip=userInfo.get('ip') if userInfo else None,
    #             user_agent=userInfo.get('user_agent') if userInfo else None,
    #             referrer=userInfo.get('referrer') if userInfo else None,
    #             server_load=system_metrics.get('server_load'),
    #             memory_usage=system_metrics.get('memory_usage'),
    #             success=False,  # Will be updated when processing completes
    #             retry_count=0
    #         )
            
    #         logger.info(f"Created analytics entry {analytics.id} for clip request {clipRequest.id}")
    #         return analytics
            
    #     except Exception as e:
    #         logger.error(f"Failed to create analytics entry for clip request {clipRequest.id}: {str(e)}")
    #         # Return None if analytics creation fails - don't block the main process
    #         return None
    
    # def updateProcessingMetrics(self, analytics: 'ClipAnalytics', processingDat dict) -> None:
    #     """
    #     Update processing metrics for an analytics entry.
        
    #     Args:
    #         analytics: ClipAnalytics model instance
    #         processingData (dict): Dictionary containing processing metrics
    #     """
    #     try:
    #         if not analytics:
    #             return
            
    #         # Update processing metrics
    #         if 'processing_time' in processingData:
    #             analytics.processing_time = processingData['processing_time']
            
    #         if 'download_size' in processingData:
    #             analytics.download_size = processingData['download_size']
            
    #         if 'processing_method' in processingData:
    #             analytics.processing_method = processingData['processing_method']
            
    #         if 'retry_count' in processingData:
    #             analytics.retry_count = processingData['retry_count']
            
    #         # Update system metrics
    #         system_metrics = self._getSystemMetrics()
    #         analytics.server_load = system_metrics.get('server_load')
    #         analytics.memory_usage = system_metrics.get('memory_usage')
            
    #         analytics.save()
            
    #         logger.info(f"Updated processing metrics for analytics {analytics.id}")
            
    #     except Exception as e:
    #         logger.error(f"Failed to update processing metrics for analytics {analytics.id if analytics else 'None'}: {str(e)}")
    
    # def recordSuccess(self, analytics: 'ClipAnalytics', finalFileSize: int) -> None:
    #     """
    #     Record successful completion of clip processing.
        
    #     Args:
    #         analytics: ClipAnalytics model instance
    #         finalFileSize (int): Size of the final clip file in bytes
    #     """
    #     try:
    #         if not analytics:
    #             return
            
    #         analytics.success = True
    #         analytics.final_file_size = finalFileSize
    #         analytics.error_type = None  # Clear any previous error
            
    #         # Update system metrics one final time
    #         system_metrics = self._getSystemMetrics()
    #         analytics.server_load = system_metrics.get('server_load')
    #         analytics.memory_usage = system_metrics.get('memory_usage')
            
    #         analytics.save()
            
    #         logger.info(f"Recorded success for analytics {analytics.id} - file size: {finalFileSize} bytes")
            
    #     except Exception as e:
    #         logger.error(f"Failed to record success for analytics {analytics.id if analytics else 'None'}: {str(e)}")
    
    # def recordFailure(self, analytics: 'ClipAnalytics', errorType: str) -> None:
    #     """
    #     Record failure of clip processing.
        
    #     Args:
    #         analytics: ClipAnalytics model instance
    #         errorType (str): Type/category of the error
    #     """
    #     try:
    #         if not analytics:
    #             return
            
    #         analytics.success = False
    #         analytics.error_type = errorType
            
    #         # Update system metrics
    #         system_metrics = self._getSystemMetrics()
    #         analytics.server_load = system_metrics.get('server_load')
    #         analytics.memory_usage = system_metrics.get('memory_usage')
            
    #         analytics.save()
            
    #         logger.info(f"Recorded failure for analytics {analytics.id} - error type: {errorType}")
            
    #     except Exception as e:
    #         logger.error(f"Failed to record failure for analytics {analytics.id if analytics else 'None'}: {str(e)}")
    
    # def getPopularChannels(self, days: int = 30) -> list:
    #     """
    #     Get list of popular channels based on clip requests.
        
    #     Args:
    #         days (int): Number of days to look back (default: 30)
            
    #     Returns:
    #         list: List of dictionaries containing channel information and stats
    #     """
    #     try:
    #         from .models import ClipAnalytics
    #         from django.db.models import Count, Avg, Sum
    #         from datetime import timedelta
            
    #         # Calculate date threshold
    #         date_threshold = timezone.now() - timedelta(days=days)
            
    #         # Query popular channels
    #         popular_channels = ClipAnalytics.objects.filter(
    #             created_at__gte=date_threshold,
    #             channel_id__isnull=False
    #         ).values(
    #             'channel_id', 'channel_name'
    #         ).annotate(
    #             request_count=Count('id'),
    #             success_count=Count('id', filter=models.Q(success=True)),
    #             avg_processing_time=Avg('processing_time'),
    #             total_clip_duration=Sum('clip_duration'),
    #             avg_clip_percentage=Avg('clip_percentage')
    #         ).order_by('-request_count')[:20]  # Top 20 channels
            
    #         # Calculate success rate for each channel
    #         result = []
    #         for channel in popular_channels:
    #             success_rate = 0.0
    #             if channel['request_count'] > 0:
    #                 success_rate = (channel['success_count'] / channel['request_count']) * 100
                
    #             result.append({
    #                 'channel_id': channel['channel_id'],
    #                 'channel_name': channel['channel_name'],
    #                 'request_count': channel['request_count'],
    #                 'success_count': channel['success_count'],
    #                 'success_rate': round(success_rate, 2),
    #                 'avg_processing_time': round(channel['avg_processing_time'] or 0, 2),
    #                 'total_clip_duration': channel['total_clip_duration'] or 0,
    #                 'avg_clip_percentage': round(channel['avg_clip_percentage'] or 0, 2)
    #             })
            
    #         logger.info(f"Retrieved {len(result)} popular channels for last {days} days")
    #         return result
            
    #     except Exception as e:
    #         logger.error(f"Failed to get popular channels: {str(e)}")
    #         return []
    
    # def getProcessingMethodStats(self) -> dict:
    #     """
    #     Get statistics about processing method effectiveness.
        
    #     Returns:
    #         dict: Dictionary containing processing method statistics
    #     """
    #     try:
    #         from .models import ClipAnalytics
    #         from django.db.models import Count, Avg, Q
            
    #         # Get stats for each processing method
    #         method_stats = ClipAnalytics.objects.values('processing_method').annotate(
    #             total_requests=Count('id'),
    #             successful_requests=Count('id', filter=Q(success=True)),
    #             avg_processing_time=Avg('processing_time'),
    #             avg_file_size=Avg('final_file_size'),
    #             avg_download_size=Avg('download_size')
    #         ).order_by('processing_method')
            
    #         # Calculate success rates and format results
    #         result = {
    #             'methods': {},
    #             'overall': {
    #                 'total_requests': 0,
    #                 'successful_requests': 0,
    #                 'overall_success_rate': 0.0
    #             }
    #         }
            
    #         total_requests = 0
    #         total_successful = 0
            
    #         for method in method_stats:
    #             method_name = method['processing_method']
    #             requests = method['total_requests']
    #             successful = method['successful_requests']
    #             success_rate = (successful / requests * 100) if requests > 0 else 0.0
                
    #             result['methods'][method_name] = {
    #                 'total_requests': requests,
    #                 'successful_requests': successful,
    #                 'success_rate': round(success_rate, 2),
    #                 'avg_processing_time': round(method['avg_processing_time'] or 0, 2),
    #                 'avg_file_size': method['avg_file_size'] or 0,
    #                 'avg_download_size': method['avg_download_size'] or 0
    #             }
                
    #             total_requests += requests
    #             total_successful += successful
            
    #         # Calculate overall stats
    #         result['overall']['total_requests'] = total_requests
    #         result['overall']['successful_requests'] = total_successful
    #         result['overall']['overall_success_rate'] = round(
    #             (total_successful / total_requests * 100) if total_requests > 0 else 0.0, 2
    #         )
            
    #         logger.info(f"Retrieved processing method stats: {total_requests} total requests")
    #         return result
            
    #     except Exception as e:
    #         logger.error(f"Failed to get processing method stats: {str(e)}")
    #         return {
    #             'methods': {},
    #             'overall': {
    #                 'total_requests': 0,
    #                 'successful_requests': 0,
    #                 'overall_success_rate': 0.0
    #             }
    #         }
    
    # def getUsageStatistics(self, days: int = 30) -> dict:
    #     """
    #     Get comprehensive usage statistics.
        
    #     Args:
    #         days (int): Number of days to look back (default: 30)
            
    #     Returns:
    #         dict: Dictionary containing usage statistics
    #     """
    #     try:
    #         from .models import ClipAnalytics
    #         from django.db.models import Count, Avg, Sum, Min, Max
    #         from datetime import timedelta
            
    #         # Calculate date threshold
    #         date_threshold = timezone.now() - timedelta(days=days)
            
    #         # Get basic statistics
    #         stats = ClipAnalytics.objects.filter(created_at__gte=date_threshold).aggregate(
    #             total_requests=Count('id'),
    #             successful_requests=Count('id', filter=models.Q(success=True)),
    #             avg_processing_time=Avg('processing_time'),
    #             total_clip_duration=Sum('clip_duration'),
    #             avg_clip_duration=Avg('clip_duration'),
    #             avg_clip_percentage=Avg('clip_percentage'),
    #             total_download_size=Sum('download_size'),
    #             avg_download_size=Avg('download_size'),
    #             total_file_size=Sum('final_file_size'),
    #             avg_file_size=Avg('final_file_size'),
    #             min_processing_time=Min('processing_time'),
    #             max_processing_time=Max('processing_time')
    #         )
            
    #         # Calculate success rate
    #         success_rate = 0.0
    #         if stats['total_requests'] and stats['total_requests'] > 0:
    #             success_rate = (stats['successful_requests'] / stats['total_requests']) * 100
            
    #         # Get unique channels and videos
    #         unique_stats = ClipAnalytics.objects.filter(created_at__gte=date_threshold).aggregate(
    #             unique_channels=Count('channel_id', distinct=True),
    #             unique_videos=Count('video_id', distinct=True)
    #         )
            
    #         result = {
    #             'period_days': days,
    #             'total_requests': stats['total_requests'] or 0,
    #             'successful_requests': stats['successful_requests'] or 0,
    #             'success_rate': round(success_rate, 2),
    #             'unique_channels': unique_stats['unique_channels'] or 0,
    #             'unique_videos': unique_stats['unique_videos'] or 0,
    #             'processing_time': {
    #                 'average': round(stats['avg_processing_time'] or 0, 2),
    #                 'minimum': round(stats['min_processing_time'] or 0, 2),
    #                 'maximum': round(stats['max_processing_time'] or 0, 2)
    #             },
    #             'clip_duration': {
    #                 'total_seconds': stats['total_clip_duration'] or 0,
    #                 'average_seconds': round(stats['avg_clip_duration'] or 0, 2),
    #                 'average_percentage': round(stats['avg_clip_percentage'] or 0, 2)
    #             },
    #             'data_usage': {
    #                 'total_download_bytes': stats['total_download_size'] or 0,
    #                 'average_download_bytes': stats['avg_download_size'] or 0,
    #                 'total_output_bytes': stats['total_file_size'] or 0,
    #                 'average_output_bytes': stats['avg_file_size'] or 0
    #             }
    #         }
            
    #         logger.info(f"Retrieved usage statistics for last {days} days: {result['total_requests']} requests")
    #         return result
            
    #     except Exception as e:
    #         logger.error(f"Failed to get usage statistics: {str(e)}")
    #         return {
    #             'period_days': days,
    #             'total_requests': 0,
    #             'successful_requests': 0,
    #             'success_rate': 0.0,
    #             'unique_channels': 0,
    #             'unique_videos': 0,
    #             'processing_time': {'average': 0, 'minimum': 0, 'maximum': 0},
    #             'clip_duration': {'total_seconds': 0, 'average_seconds': 0, 'average_percentage': 0},
    #             'data_usage': {'total_download_bytes': 0, 'average_download_bytes': 0, 'total_output_bytes': 0, 'average_output_bytes': 0}
    #         }
    
    # def _getSystemMetrics(self) -> dict:
    #     """
    #     Get current system performance metrics.
        
    #     Returns:
    #         dict: Dictionary containing system metrics
    #     """
    #     try:
    #         import psutil
            
    #         # Get CPU and memory usage
    #         cpu_percent = psutil.cpu_percent(interval=1)
    #         memory = psutil.virtual_memory()
    #         memory_usage_mb = memory.used / (1024 * 1024)  # Convert to MB
            
    #         return {
    #             'server_load': cpu_percent,
    #             'memory_usage': memory_usage_mb
    #         }
            
    #     except ImportError:
    #         # psutil not available, return None values
    #         logger.warning("psutil not available for system metrics")
    #         return {
    #             'server_load': None,
    #             'memory_usage': None
    #         }
    #     except Exception as e:
    #         logger.error(f"Failed to get system metrics: {str(e)}")
    #         return {
    #             'server_load': None,
    #             'memory_usage': None
    #         }