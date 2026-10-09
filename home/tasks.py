"""
Background tasks for YouTube clipper processing using Celery.
"""
import os
import time

import requests
from celery import shared_task
from celery.exceptions import SoftTimeLimitExceeded
from django.conf import settings
from yt_dlp.utils import (
    DownloadError,
    ExtractorError,
    GeoRestrictedError,
    UnavailableVideoError,
    UnsupportedError,
)

from email_func.brevo_email import Email
from utility.variables import oldFileRetentionMinutes
from yt_helper.settings import logger

from .models import CLIP_STATUS_CHOICES, ClipRequest, SpeedEditRequest
from .services import (
    ClipProcessingService,
    ProcessingFailedException,
    SpeedEditService,
)
from .utils import (
    clear_crash_attempt,
    delete_request_dir,
    record_crash_attempt,
    remove_files_older_than,
    update_obj_status,
)


@shared_task(
    bind=True,
    name="process_clip_task",
    acks_late=True, #  Re-queue task if worker crashes mid-download
    reject_on_worker_lost=True, # Reject & requeue if worker process dies abruptly
    autoretry_for=(Exception,), # Retry if standard Python error occurs during execution
    dont_autoretry_for=(
        ValueError,SoftTimeLimitExceeded,FileNotFoundError,ProcessingFailedException,
        ExtractorError,        # Generic extraction failure
        GeoRestrictedError,    # Video blocked in this region/country
        UnavailableVideoError, # Video is private, deleted, or not found
        UnsupportedError,      # Invalid or unsupported URL
    ),
    max_retries=1,
    retry_backoff=3,
    retry_jitter=True,
    soft_time_limit=3*60, # 3min
    time_limit=4*60, # 4min

)
def process_clip_task(self, clipRequestId: str) -> bool:
    """
    Download one YouTube section as 720p and 480p.
    The view passes the ClipRequest id. URL, start, end, and output folder are read from that row
    so the worker does not receive a large payload and always sees the latest cancel status.
    """
    if record_crash_attempt(self.request):
        clipRequest = ClipRequest.objects.filter(id=clipRequestId).first()
        update_obj_status(
            clipRequest,
            status=CLIP_STATUS_CHOICES.FAILED,
            errorMessage="Task aborted: worker process crashed repeatedly",
        )
        delete_request_dir(clipRequestId, 'clip_request')
        return False

    clipRequest = ClipRequest.objects.filter(id=clipRequestId).first()
    if not clipRequest:
        logger.error(f"ClipRequest {clipRequestId} not found for processing.")
        return False

    # acks_late can redeliver a task after the worker is killed. Skip work the user already cancelled or which is already completed.
    if clipRequest.status == CLIP_STATUS_CHOICES.CANCELLED or clipRequest.status == CLIP_STATUS_CHOICES.COMPLETED :
        logger.info(f"ClipRequest {clipRequestId} is in {clipRequest.status}. Skipping.")
        return False

    try:
        clipProcessingService = ClipProcessingService()
        didFinish = clipProcessingService.process_clip_request(clipRequest)
    except SoftTimeLimitExceeded:
        # The hard kill follows this signal. Clean up partial files before re-raising.
        logger.error(f"Clip task hit the soft time limit for {clipRequestId}")
        delete_request_dir(clipRequestId, 'clip_request')
        raise
    except DownloadError as exc:
        # If yt-dlp wrapped a permanent error (private, geo-blocked, not found, bad URL),
        # unwrap and raise the inner exception so dont_autoretry_for can immediately skip retries.
        if getattr(exc, 'exc_info', None) and exc.exc_info and exc.exc_info[1]:
            innerExc = exc.exc_info[1]
            if isinstance(innerExc, (ExtractorError, UnavailableVideoError, GeoRestrictedError, UnsupportedError)):
                raise innerExc from exc
        raise

    if didFinish:
        clear_crash_attempt(self.request.id)
    return didFinish


@shared_task(
    bind=True,
    name="process_speed_edit_task",
    acks_late=True,
    reject_on_worker_lost=True,
    soft_time_limit=3*60, # 3min
    time_limit=4*60, # 4min
    
)
def process_speed_edit_task(self, speedEditRequestId: str) -> bool:
    """
    Re-time an uploaded video. Input path, speed factor, and output path come from SpeedEditRequest.
    """
    if record_crash_attempt(self.request):
        speedEditRequest = SpeedEditRequest.objects.filter(id=speedEditRequestId).first()
        update_obj_status(
            speedEditRequest,
            status=CLIP_STATUS_CHOICES.FAILED,
            errorMessage="Speed edit aborted: worker process crashed repeatedly",
        )
        delete_request_dir(speedEditRequestId, 'speed_edit')
        return False

    speedEditRequest = SpeedEditRequest.objects.filter(id=speedEditRequestId).first()
    if not speedEditRequest:
        logger.error(f"SpeedEditRequest {speedEditRequestId} not found for processing.")
        return False
    # acks_late can redeliver a task after the worker is killed. Skip work the user already cancelled.
    if speedEditRequest.status == CLIP_STATUS_CHOICES.CANCELLED or speedEditRequest.status == CLIP_STATUS_CHOICES.COMPLETED:
        logger.info(f"SpeedEditRequest {speedEditRequestId} is in {speedEditRequest.status}. Skipping.")
        return False

    sourcePath = speedEditRequest.get_source_path()
    if not sourcePath or not os.path.exists(sourcePath):
        update_obj_status(
            speedEditRequest,
            status=CLIP_STATUS_CHOICES.FAILED,
            errorMessage="Source video not found",
        )
        raise ValueError(speedEditRequest.error_message)
    try:
        speedEditService = SpeedEditService()
        didFinish = speedEditService.process_speed_edit_request(speedEditRequest)
    except SoftTimeLimitExceeded:
        # The hard kill follows this signal. Clean up partial files before re-raising.
        logger.error(f"Speed edit task hit the soft time limit for {speedEditRequestId}")
        delete_request_dir(speedEditRequestId, 'speed_edit')
        raise

    if didFinish:
        clear_crash_attempt(self.request.id)
    return didFinish


@shared_task(name="cleanup_old_files_task", acks_late=True)
def cleanup_old_files():
    """
    Background task to cleanup old clip files, speed-edited videos, uploads,
    and temporary orphan files based on retention settings.
    Can be run directly or triggered periodically via Celery Beat.
    """
    try:
        # Calculate the cutoff timestamp in epoch seconds
        cutoffTimestamp = time.time() - (oldFileRetentionMinutes * 60)

        # Media folders containing video files generated or uploaded during processing:
        # 1. 'clips': raw download folders (clips/<requestId>/) and saved clip files
        # 2. 'speed_edited_videos': speed-adjusted output videos
        # 3. 'speed_edit_uploads': user-uploaded source videos
        #
        # Why this unified approach:
        # - Scanning these folders directly by file age (mtime) cleans up expired files in one pass.
        # - It also deletes "orphan" files left behind by crashed workers, cancellations, or failed
        #   downloads that never created a database row, preventing disk leaks over time.
        mediaFolders = ('clips', 'speed_edited_videos', 'speed_edit_uploads')
        filesCleaned = 0

        for mediaFolder in mediaFolders:
            folderPath = os.path.join(settings.MEDIA_ROOT, mediaFolder)
            if os.path.isdir(folderPath):
                filesCleaned += remove_files_older_than(folderPath, cutoffTimestamp)

        logger.info(
            f"Cleanup completed: {filesCleaned} aged and orphan media files removed "
            f"(older than {oldFileRetentionMinutes} minutes)."
        )

    except OSError:
        logger.exception("Error during bulk cleanup")


@shared_task(name="cleanup_cancelled_dir_task", acks_late=True)
def cleanup_cancelled_dir_task(requestId: str, requestType: str):
    """
    Delayed Celery task to delete partial output directory after cancellation.
    Called with a countdown so a stopped ffmpeg can exit before the folder is removed.
    """
    modelMap = {
        'clip_request': ClipRequest,
        'speed_edit': SpeedEditRequest,
    }
    modelClass = modelMap.get(requestType)
    if not modelClass:
        logger.error(f"Unknown cancel cleanup type: {requestType}")
        return

    requestObj = modelClass.objects.filter(id=requestId).first()
    # Only delete the directory if the request exists and is still in CANCELLED status
    if not requestObj or requestObj.status != CLIP_STATUS_CHOICES.CANCELLED:
        return

    delete_request_dir(requestId, requestType)


@shared_task(
    bind=True,
    name="send_email_task",
    # Ack before the send. A crash mid-send must not deliver the same email again.
    acks_late=False,
    autoretry_for=(requests.RequestException,),
    dont_autoretry_for=(SoftTimeLimitExceeded,),
    retry_backoff=True,
    retry_jitter=True,
    max_retries=2,
)
def send_email_task(self, emailData: dict):
    """
    Send one Brevo email. Network errors retry with backoff. Other errors are not retried.
    """
    Email.send_email(emailData)

        
