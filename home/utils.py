"""
Utility and helper functions for file handling, directory cleanup, and task tracking.
"""
import os
import shutil

from django.conf import settings
from django.core.cache import cache

from utility.variables import crashCountTimeoutSeconds, maxWorkerCrashStarts
from yt_helper.settings import logger


def record_crash_attempt(taskRequest) -> bool:
    """
    Count starts of this task id for the current retry attempt.
    A child crash redelivers the same task id with the same retry count.
    A normal Celery autoretry increments the retry count, which resets the crash counter.
    Returns True when crash starts exceed maxWorkerCrashStarts to stop poison pill loops.
    """
    taskId = getattr(taskRequest, 'id', None)
    if not taskId:
        return False

    try:
        currentRetries = getattr(taskRequest, 'retries', 0)
        cacheKey = f"celery-worker-starts:{taskId}"
        cachedAttempt = cache.get(cacheKey)

        # If the task is restarting with the exact same retry count, the worker crashed.
        # If the retry count changed, Celery performed a normal autoretry, so reset crash starts to 0.
        if cachedAttempt and cachedAttempt.get("retries") == currentRetries:
            crashStarts = cachedAttempt.get("count", 0)
        else:
            crashStarts = 0

        if crashStarts > maxWorkerCrashStarts:
            logger.error(
                f"Poison pill: task {taskId} (retry {currentRetries}) already started {crashStarts} times. "
                "Aborting so a crashing job cannot loop forever."
            )
            return True

        cache.set(
            cacheKey,
            {"retries": currentRetries, "count": crashStarts + 1},
            timeout=crashCountTimeoutSeconds,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"Failed to record crash attempt in cache for task {taskId}: {exc}")

    return False


def clear_crash_attempt(taskId: str) -> None:
    """Remove task worker start counter from cache upon successful completion."""
    if taskId:
        try:
            cache.delete(f"celery-worker-starts:{taskId}")
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"Failed to clear crash attempt from cache for task {taskId}: {exc}")


def update_obj_status(requestObj=None, status: str = "", errorMessage: str = "") -> None:
    """
    Dynamically update status (and optionally error_message) for a request object.
    Safely no-ops if requestObj is None.
    """
    if not requestObj:
        return

    updateFields = []
    if status:
        requestObj.status = status
        updateFields.append('status')
    if errorMessage:
        requestObj.error_message = errorMessage
        updateFields.append('error_message')

    if updateFields:
        requestObj.save(update_fields=updateFields)


def allowed_temp_roots() -> tuple:
    """Return tuple of resolved absolute paths where temporary media files are permitted."""
    return (
        os.path.realpath(os.path.join(settings.MEDIA_ROOT, 'clips')),
        os.path.realpath(os.path.join(settings.MEDIA_ROOT, 'speed_edited_videos')),
        os.path.realpath(os.path.join(settings.MEDIA_ROOT, 'speed_edit_uploads')),
    )


def path_is_inside_temp(targetPath: str) -> bool:
    """Reject paths that resolve outside the clip and speed-edit folders."""
    realPath = os.path.realpath(targetPath)
    for rootPath in allowed_temp_roots():
        try:
            if os.path.commonpath([realPath, rootPath]) == rootPath:
                return True
        except ValueError:
            continue
    return False


def delete_request_dir(requestId: str, requestType: str) -> None:
    """Remove a processing folder after a time limit or a cancel. Skips anything outside temp roots."""
    folderMap = {
        'clip_request': 'clips',
        'speed_edit': 'speed_edited_videos',
    }
    folderName = folderMap.get(requestType)
    if not folderName:
        logger.error(f"Refusing cleanup for unknown request type {requestType}")
        return

    outputDir = os.path.join(settings.MEDIA_ROOT, folderName, str(requestId))
    if not path_is_inside_temp(outputDir):
        logger.error(f"Refusing to delete path outside temp dirs: {outputDir}")
        return

    try:
        if os.path.isdir(outputDir):
            shutil.rmtree(outputDir, ignore_errors=False)
            logger.info(f"Deleted temp dir {outputDir}")
    except OSError as exc:
        logger.error(f"Failed to delete temp dir {outputDir}: {exc}")


def remove_files_older_than(rootDir: str, cutoffTimestamp: float) -> int:
    """
    Walk with scandir so a large media folder is not loaded into a list.
    Only files under the known temp roots are removed.
    """
    if not os.path.isdir(rootDir) or not path_is_inside_temp(rootDir):
        return 0
    removedCount = 0
    with os.scandir(rootDir) as entries:
        for entry in entries:
            try:
                if entry.is_dir(follow_symlinks=False):
                    removedCount += remove_files_older_than(entry.path, cutoffTimestamp)
                    # Delete sub-directory if it became empty after removing expired files
                    try:
                        if not os.listdir(entry.path):
                            os.rmdir(entry.path)
                    except OSError:
                        pass
                elif entry.is_file(follow_symlinks=False) and entry.stat().st_mtime < cutoffTimestamp:
                    os.remove(entry.path)
                    removedCount += 1
            except OSError as exc:
                logger.error(f"Failed to scan or remove {entry.path}: {exc}")
    return removedCount

