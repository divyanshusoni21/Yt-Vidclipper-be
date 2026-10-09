import copy
import os
import random
import re
import shutil
import subprocess
import traceback
from concurrent.futures import ThreadPoolExecutor
from time import time

import yt_dlp
from celery.exceptions import SoftTimeLimitExceeded
from django.conf import settings
from django.utils import timezone

from utility.functions import runSerializer, time_to_seconds
from utility.variables import cookiesFile, proxies
from yt_helper.settings import logger

from .models import CLIP_STATUS_CHOICES, Clip, ClipRequest, VideoDetail
from .serializers import VideoDetailSerializer
from .utils import update_obj_status


class ProcessingFailedException(Exception):
    """Exception raised when video processing fails."""


class ClipProcessingService:
    """Service class for processing video clips using hybrid methods."""

    # YouTube URL patterns for validation, including shorts and live streams
    YOUTUBE_URL_PATTERNS = (
        r'(?:https?://)?(?:www\.)?youtube\.com/watch\?v=([a-zA-Z0-9_-]{11})',
        r'(?:https?://)?(?:www\.)?youtu\.be/([a-zA-Z0-9_-]{11})',
        r'(?:https?://)?(?:www\.)?youtube\.com/embed/([a-zA-Z0-9_-]{11})',
        r'(?:https?://)?(?:www\.)?youtube\.com/v/([a-zA-Z0-9_-]{11})',
        r'(?:https?://)?(?:www\.)?youtube\.com/live/([a-zA-Z0-9_-]{11})(?:\?.*)?',
        r'(?:https?://)?(?:www\.)?youtube\.com/shorts/([a-zA-Z0-9_-]{11})(?:\?.*)?',
    )

    def __init__(self):
        # Ensure ffmpeg is installed
        self._check_ffmpeg()

    def validate_youtube_url(self, url: str) -> bool:
        """
        Validate if the provided URL matches recognized YouTube video URL patterns.
        Used by ClipRequestSerializer to reject invalid URLs early before queuing.
        """
        if not url or not isinstance(url, str):
            return False

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

    def _section_format(self, maxHeight: int) -> str:
        """
        Native H.264 at or below maxHeight. HLS first so yt-dlp can pull only
        the ranged fragments, then progressive HTTP, then any stream at that height.
        The last `best` avoids a hard fail when that height is missing; the Clip
        row is still stored under the requested resolution label.
        """
        height = f"height<={maxHeight}"
        return (
            f'bestvideo[{height}][vcodec^=avc1][protocol*="m3u8"]+bestaudio[protocol*="m3u8"]/'
            f'best[{height}][vcodec^=avc1][protocol*="m3u8"]/'
            f'bestvideo[{height}][vcodec^=avc1][protocol^=http]+bestaudio[protocol^=http]/'
            f'best[{height}][vcodec^=avc1]/'
            f'bestvideo[{height}]+bestaudio/'
            f'best[{height}][ext=mp4]/'
            f'best[{height}]/'
            'best'
        )

    def _base_ydl_opts(self, startSec: int, endSec: int, proxy: str) -> dict:
        """
        Options shared by the one metadata fetch and both section downloads.
        download_ranges keeps the CDN fetch inside the requested window.
        Deno runs yt-dlp-ejs; web_safari is what exposes the HLS H.264 renditions.
        """
        ydlOpts = {
            'js_runtimes': {'deno': {}},
            'extractor_args': {
                'youtube': {
                    'player_client': ['web_safari', 'default'],
                }
            },
            'concurrent_fragment_downloads': 4,
            'download_ranges': yt_dlp.utils.download_range_func(None, [(startSec, endSec)]),
            'merge_output_format': 'mp4',
            'downloader_args': {
                'ffmpeg_i': [
                    # One thread so a section download cannot take every core.
                    '-threads', '1',
                    '-reconnect', '1',
                    '-reconnect_streamed', '1',
                    '-reconnect_delay_max', '5',
                    '-multiple_requests', '1',
                    '-buffer_size', '32M',
                ]
            },
            # Remux only. faststart moves the moov atom; this is not a re-encode.
            'postprocessor_args': {
                'ffmpeg': [
                    '-threads', '1',
                    '-movflags', '+faststart',
                ]
            },
            'overwrites': True,
            'no_warnings': False,
            'noplaylist': True,
            'quiet': True,
        }
        if cookiesFile:
            ydlOpts['cookiefile'] = cookiesFile
        if proxy:
            ydlOpts['proxy'] = proxy
        return ydlOpts

    def download_clip(self, baseOpts: dict, infoDict: dict, outputPath: str, maxHeight: int) -> None:
        """
        Download one resolution (720 or 480) from the cached manifest.
        Copies are required because this runs in parallel: yt-dlp mutates both
        dicts, and a shared dict would make the other thread hit YouTube again.
        """
        sectionOpts = copy.deepcopy(baseOpts)
        sectionOpts['outtmpl'] = outputPath
        sectionOpts['format'] = self._section_format(maxHeight)
        logger.info(f"Slicing {maxHeight}p clip from cached manifest")
        with yt_dlp.YoutubeDL(sectionOpts) as ydl:
            ydl.process_ie_result(copy.deepcopy(infoDict), download=True)

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
    
    def download_and_create_clips(self, clipRequest:ClipRequest, startSec: int, endSec: int, clipDurationSeconds: int,out720pPathAbsolute: str,out480pPathAbsolute: str) -> str:
        """
        Fetch YouTube metadata once, then download the 720p and 480p windows
        from that same manifest at the same time. No libx264 pass.
        """
        proxy = self.get_proxy()
        baseOpts = self._base_ydl_opts(startSec, endSec, proxy)

        for outPath in (out720pPathAbsolute, out480pPathAbsolute):
            dirName = os.path.dirname(outPath)
            if dirName:
                os.makedirs(dirName, exist_ok=True)

        # download=False: manifests and stream URLs only, no media bytes yet.
        with yt_dlp.YoutubeDL(baseOpts) as ydl:
            info = ydl.extract_info(clipRequest.youtube_url, download=False)

        videoDuration = info.get('duration', None)
        if videoDuration is not None and startSec > videoDuration:
            raise ProcessingFailedException("Start time cannot be beyond video duration.")

        self.save_video_info(info, clipRequest, clipDurationSeconds, endSec)

        # Same manifest, two independent CDN fetches. Two workers so neither waits on the other.
        clipJobs = (
            (out720pPathAbsolute, 720),
            (out480pPathAbsolute, 480),
        )
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(self.download_clip, baseOpts, info, outputPath, maxHeight)
                for outputPath, maxHeight in clipJobs
            ]
            for future in futures:
                future.result()

        return proxy
    
    def create_clip_object(self, outPathAbsolute: str, clipRequest: ClipRequest, clipDurationSeconds: int, resolution: str) -> Clip:
        clipBytes = os.path.getsize(outPathAbsolute)
        clipMb = round(clipBytes / (1024 * 1024), 2)  # Convert bytes to MB
        fileName = os.path.basename(outPathAbsolute)
        relativeClipPath = f"clips/{clipRequest.id}/{fileName}"

        # Point the Clip model directly to the downloaded file under clips/<clipRequest.id>/
        # Update or create to ensure idempotency if the task is retried by Celery
        clipObj, _ = Clip.objects.update_or_create(
            clip_request=clipRequest,
            resolution=resolution,
            defaults={
                'clip': relativeClipPath,
                'size': float(clipMb),
                'duration': clipDurationSeconds,
            },
        )
        return clipObj

    def process_clip_request(self, clipRequest:ClipRequest) -> bool:
        """
        Download the requested window as native 720p and 480p MP4s.
        One yt-dlp metadata fetch, then two CDN section downloads from that manifest.
        
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
                {'message': 'Starting clip processing'}
            )
            update_obj_status(clipRequest, status=CLIP_STATUS_CHOICES.PROCESSING)
            
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
                {'message': f'Downloaded 720p and 480p clips in {t3 - t2:.2f}s'}
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
            # Cancel can land while ffmpeg is still running. Do not overwrite that status.
            clipRequest.refresh_from_db(fields=['status'])
            if clipRequest.status == CLIP_STATUS_CHOICES.CANCELLED:
                return False
                
            # --- Final Success Update ---
            clipRequest.status = CLIP_STATUS_CHOICES.COMPLETED
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
            clipRequest.refresh_from_db(fields=['status'])
            if clipRequest.status == CLIP_STATUS_CHOICES.CANCELLED:
                return False

            errorMsg = (
                "Clip processing timed out (exceeded time limit)"
                if isinstance(e, SoftTimeLimitExceeded)
                else str(e)
            )
            update_obj_status(clipRequest, status=CLIP_STATUS_CHOICES.FAILED, errorMessage=errorMsg)

            self.log_processing_step(
                clipRequest,
                'processing_error',
                'error',
                {'error': errorMsg, 'exception_type': type(e).__name__}
            )
            # Re-raise so tasks.py can clean up directories and Celery handles task failure
            raise

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
            
            # Set status to processing so polling clients and cancellation know worker is active
            update_obj_status(speedEditRequest, status=CLIP_STATUS_CHOICES.PROCESSING)
            
            # Get source video path
            sourcePath = speedEditRequest.get_source_path()
            if not sourcePath or not os.path.exists(sourcePath):
                raise ProcessingFailedException("Source video not found")
            
            # Get original duration using ffprobe
            originalDuration = self._get_video_duration(sourcePath)
            speedEditRequest.original_duration = originalDuration
            speedEditRequest.save(update_fields=[ 'original_duration'])
            
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
                # Cap threads so a speed change does not use every core
                '-threads', '2',
                '-i', sourcePath,
                '-filter_complex', f'[0:v]{videoFilter}[v];[0:a]{audioFilterChain}[a]',
                '-map', '[v]',
                '-map', '[a]',
                '-c:v', 'libx264', '-preset', 'superfast', '-crf', '23',
                '-c:a', 'aac',
                '-y',
                outputPath
            ]
            
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
            
            # Point to the existing output file on disk directly instead of copying the file bytes again
            speedEditRequest.output_video.name = f"speed_edited_videos/{speedEditRequest.id}/{outputFilename}"
            
            # Update model
            tEnd = time()
            # Cancel can land while ffmpeg is still running. Do not overwrite that status.
            speedEditRequest.refresh_from_db(fields=['status'])
            if speedEditRequest.status == CLIP_STATUS_CHOICES.CANCELLED:
                return False
            speedEditRequest.output_size = outputSizeMb
            speedEditRequest.output_duration = outputDuration
            speedEditRequest.processing_time = round(tEnd - tStart, 2)
            speedEditRequest.status = CLIP_STATUS_CHOICES.COMPLETED
            speedEditRequest.save()
            
            logger.info(f"Speed edit completed for request {speedEditRequest.id} in {tEnd - tStart:.2f}s")
            return True
            
        except Exception as e:
            logger.error(f"Speed edit failed for request {speedEditRequest.id}: {e!s}")
            logger.error(traceback.format_exc())

            speedEditRequest.refresh_from_db(fields=['status'])
            if speedEditRequest.status == CLIP_STATUS_CHOICES.CANCELLED:
                return False

            errorMsg = (
                "Speed edit processing timed out (exceeded time limit)"
                if isinstance(e, SoftTimeLimitExceeded)
                else str(e)
            )
            update_obj_status(speedEditRequest, status=CLIP_STATUS_CHOICES.FAILED, errorMessage=errorMsg)
            raise
    
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
