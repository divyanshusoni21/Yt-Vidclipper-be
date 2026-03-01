"""
Background tasks for YouTube clipper processing using Django-RQ.
"""
import os
import shutil
from yt_helper.settings import logger
from django.conf import settings
from utility.variables import oldFileRetentionHours
from .models import Clip,SpeedEditRequest
from datetime import datetime, timedelta

def cleanup_cancelled_task_dir(requestObj, requestType):
    """
    Remove partial output directory for a cancelled speed edit request.
    Safe to call from a delayed job after send_stop_job_command.
    """
    try:
        logger.info(f"Cleaning up partial {requestType} dir for cancelled request {requestObj.id}")
        if not requestObj or requestObj.status != 'cancelled':
            return
        if requestType == 'clip_request':
            outputDir = os.path.join(settings.MEDIA_ROOT, 'clips', str(requestObj.id))
        elif requestType == 'speed_edit':
            outputDir = os.path.join(settings.MEDIA_ROOT, 'speed_edited_videos', str(requestObj.id))
        else:
            raise Exception('Invalid request type')

        if os.path.isdir(outputDir):
            shutil.rmtree(outputDir, ignore_errors=False)
            logger.info(f"Cleaned up partial {requestType} dir for cancelled request {requestObj.id}")
    except Exception as e:
        logger.error(f"Cleanup cancelled {requestType} dir failed for {requestObj.id}: {e}")



def cleanup_old_files():
    """
    Background task to cleanup old clip files and temporary files based on retention settings.
    This should be run periodically (e.g., via cron job or scheduled task).
    """

    try:
        # Calculate cutoff times
        clipCutoff = datetime.now() - timedelta(hours=oldFileRetentionHours)

        # Cleanup old completed clips
        oldClips = Clip.objects.filter(
            created_at__lt=clipCutoff,
        )
        oldSpeedEdits = SpeedEditRequest.objects.filter(
            created_at__lt=clipCutoff,
        )
        
        clipsCleaned = 0
        for clip in oldClips:
            if clip.clip and os.path.exists(clip.clip.path):
                try:
                    os.remove(clip.clip.path)
                    clipsCleaned += 1
                except Exception as e:
                    logger.error(f"Failed to remove clip file {clip.clip}: {str(e)}")
                    
        speedEditsCleaned = 0
        for speedEdit in oldSpeedEdits:
            if speedEdit.output_video and os.path.exists(speedEdit.output_video.path):
                try:
                    os.remove(speedEdit.output_video.path)
                    os.remove(speedEdit.output_video.path)
                    speedEditsCleaned += 1
                except Exception as e:
                    logger.error(f"Failed to remove speed edit file {speedEdit.output_video}: {str(e)}")
                    
            if speedEdit.uploaded_video and os.path.exists(speedEdit.uploaded_video.path):
                try:
                    os.remove(speedEdit.uploaded_video.path)
                    speedEditsCleaned += 1
                except Exception as e:
                    logger.error(f"Failed to remove uploaded video file {speedEdit.uploaded_video}: {str(e)}")
        
        logger.info(f"Cleanup completed: {clipsCleaned} clip files, {speedEditsCleaned} speed edit files removed.")
        
    except Exception as e:
        logger.error(f"Error during bulk cleanup: {str(e)}")
        
