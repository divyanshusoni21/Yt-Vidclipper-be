"""
Background tasks for YouTube clipper processing using Django-RQ.
"""
import os
import shutil
from yt_helper.settings import logger
from django.conf import settings
from django_rq import job
from .models import ClipRequest


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


@job('low', timeout=600)  # 10 minute timeout for bulk cleanup
def cleanup_old_files():
    """
    Background task to cleanup old clip files and temporary files based on retention settings.
    This should be run periodically (e.g., via cron job or scheduled task).
    """
    from datetime import datetime, timedelta
    import shutil
    
    try:
        # Calculate cutoff times
        clip_cutoff = datetime.now() - timedelta(hours=settings.CLIP_RETENTION_HOURS)
        temp_cutoff = datetime.now() - timedelta(hours=settings.TEMP_FILE_RETENTION_HOURS)
        
        # Cleanup old completed clips
        old_clips = ClipRequest.objects.filter(
            status='completed',
            processed_at__lt=clip_cutoff
        )
        
        clips_cleaned = 0
        for clip in old_clips:
            if clip.file_path and os.path.exists(clip.file_path):
                try:
                    os.remove(clip.file_path)
                    clips_cleaned += 1
                    logger.info(f"Removed old clip file: {clip.file_path}")
                except Exception as e:
                    logger.error(f"Failed to remove clip file {clip.file_path}: {str(e)}")
        
        # Cleanup old temporary directories
        temp_dirs_cleaned = 0
        if os.path.exists(settings.TEMP_STORAGE_ROOT):
            for item in os.listdir(settings.TEMP_STORAGE_ROOT):
                item_path = os.path.join(settings.TEMP_STORAGE_ROOT, item)
                if os.path.isdir(item_path):
                    # Check if directory is old enough
                    dir_mtime = datetime.fromtimestamp(os.path.getmtime(item_path))
                    if dir_mtime < temp_cutoff:
                        try:
                            shutil.rmtree(item_path)
                            temp_dirs_cleaned += 1
                            logger.info(f"Removed old temp directory: {item_path}")
                        except Exception as e:
                            logger.error(f"Failed to remove temp directory {item_path}: {str(e)}")
        
        logger.info(f"Cleanup completed: {clips_cleaned} clip files, {temp_dirs_cleaned} temp directories removed")
        
        return {
            'success': True,
            'clips_cleaned': clips_cleaned,
            'temp_dirs_cleaned': temp_dirs_cleaned
        }
        
    except Exception as e:
        logger.error(f"Error during bulk cleanup: {str(e)}")
        return {
            'success': False,
            'error': str(e)
        }

