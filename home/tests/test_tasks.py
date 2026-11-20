"""Unit tests for Celery tasks in home.tasks."""

import datetime
import os
import shutil
import tempfile
from unittest.mock import patch

from celery.exceptions import SoftTimeLimitExceeded
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from yt_dlp.utils import DownloadError, UnavailableVideoError

from home.models import (
    CLIP_STATUS_CHOICES,
    ClipRequest,
    SpeedEditRequest,
)
from home.tasks import (
    cleanup_cancelled_dir_task,
    cleanup_old_files,
    process_clip_task,
    process_speed_edit_task,
    send_email_task,
)


class ProcessClipTaskTest(TestCase):
    """Tests for process_clip_task crash guard, cancellation check, error unwrapping, and cleanup."""

    def setUp(self):
        self.clipRequest = ClipRequest.objects.create(
            youtube_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            start_time=datetime.time(0, 0, 10),
            end_time=datetime.time(0, 0, 20),
        )

    @patch("home.tasks.delete_request_dir")
    @patch("home.tasks.record_crash_attempt", return_value=True)
    def test_aborts_when_poison_pill_crash_loop_detected(self, mockRecordCrash, mockDeleteDir):
        # When worker repeatedly crashes on the same task, mark FAILED, clean dir, and return False
        result = process_clip_task.run(str(self.clipRequest.id))
        self.assertFalse(result)
        self.clipRequest.refresh_from_db()
        self.assertEqual(self.clipRequest.status, CLIP_STATUS_CHOICES.FAILED)
        self.assertIn("crashed repeatedly", self.clipRequest.error_message)
        mockDeleteDir.assert_called_once_with(str(self.clipRequest.id), "clip_request")

    @patch("home.tasks.record_crash_attempt", return_value=False)
    def test_skips_when_clip_request_cancelled_or_completed(self, mockRecordCrash):
        # Redelivered tasks for already cancelled or completed requests must be skipped immediately
        self.clipRequest.status = CLIP_STATUS_CHOICES.CANCELLED
        self.clipRequest.save(update_fields=["status"])
        self.assertFalse(process_clip_task.run(str(self.clipRequest.id)))

    @patch("home.tasks.clear_crash_attempt")
    @patch("home.tasks.ClipProcessingService")
    @patch("home.tasks.record_crash_attempt", return_value=False)
    def test_successful_execution_clears_crash_counter(
        self, mockRecordCrash, mockServiceClass, mockClearCrash
    ):
        mockServiceClass.return_value.process_clip_request.return_value = True
        result = process_clip_task.run(str(self.clipRequest.id))
        self.assertTrue(result)
        mockClearCrash.assert_called_once()

    @patch("home.tasks.delete_request_dir")
    @patch("home.tasks.ClipProcessingService")
    @patch("home.tasks.record_crash_attempt", return_value=False)
    def test_soft_time_limit_deletes_request_dir_and_reraises(
        self, mockRecordCrash, mockServiceClass, mockDeleteDir
    ):
        # When SoftTimeLimitExceeded fires, partial files must be deleted before re-raising
        mockServiceClass.return_value.process_clip_request.side_effect = SoftTimeLimitExceeded()
        with self.assertRaises(SoftTimeLimitExceeded):
            process_clip_task.run(str(self.clipRequest.id))
        mockDeleteDir.assert_called_once_with(str(self.clipRequest.id), "clip_request")

    @patch("home.tasks.ClipProcessingService")
    @patch("home.tasks.record_crash_attempt", return_value=False)
    def test_unwraps_permanent_yt_dlp_exception_from_download_error(
        self, mockRecordCrash, mockServiceClass
    ):
        # Unwrapping UnavailableVideoError allows Celery's dont_autoretry_for to skip useless retries
        innerError = UnavailableVideoError("Video unavailable")
        downloadError = DownloadError("Download failed", exc_info=(UnavailableVideoError, innerError, None))
        mockServiceClass.return_value.process_clip_request.side_effect = downloadError

        with self.assertRaises(UnavailableVideoError):
            process_clip_task.run(str(self.clipRequest.id))


@override_settings(MEDIA_ROOT=tempfile.gettempdir())
class ProcessSpeedEditTaskTest(TestCase):
    """Tests for process_speed_edit_task guards and error handling."""

    def setUp(self):
        self.uploadedVideo = SimpleUploadedFile("video.mp4", b"video-data", content_type="video/mp4")
        self.speedEditRequest = SpeedEditRequest.objects.create(
            uploaded_video=self.uploadedVideo,
            speed_factor=1.5,
        )

    @patch("home.tasks.delete_request_dir")
    @patch("home.tasks.record_crash_attempt", return_value=True)
    def test_aborts_on_crash_loop(self, mockRecordCrash, mockDeleteDir):
        result = process_speed_edit_task.run(str(self.speedEditRequest.id))
        self.assertFalse(result)
        self.speedEditRequest.refresh_from_db()
        self.assertEqual(self.speedEditRequest.status, CLIP_STATUS_CHOICES.FAILED)
        mockDeleteDir.assert_called_once_with(str(self.speedEditRequest.id), "speed_edit")

    @patch("home.tasks.record_crash_attempt", return_value=False)
    def test_raises_value_error_when_source_video_missing(self, mockRecordCrash):
        # Missing source file raises ValueError (which is in dont_autoretry_for) and marks FAILED
        requestWithoutSource = SpeedEditRequest.objects.create(speed_factor=1.5)
        with self.assertRaises(ValueError):
            process_speed_edit_task.run(str(requestWithoutSource.id))
        requestWithoutSource.refresh_from_db()
        self.assertEqual(requestWithoutSource.status, CLIP_STATUS_CHOICES.FAILED)

    @patch("home.tasks.clear_crash_attempt")
    @patch("home.tasks.SpeedEditService")
    @patch("home.tasks.record_crash_attempt", return_value=False)
    def test_successful_speed_edit_task_clears_crash_attempt(
        self, mockRecordCrash, mockServiceClass, mockClearCrash
    ):
        mockServiceClass.return_value.process_speed_edit_request.return_value = True
        result = process_speed_edit_task.run(str(self.speedEditRequest.id))
        self.assertTrue(result)
        mockClearCrash.assert_called_once()


class CleanupAndEmailTasksTest(TestCase):
    """Tests for cleanup_old_files, cleanup_cancelled_dir_task, and send_email_task."""

    def setUp(self):
        self.tempMediaDir = tempfile.mkdtemp()
        self.overrideMedia = override_settings(MEDIA_ROOT=self.tempMediaDir)
        self.overrideMedia.enable()

    def tearDown(self):
        self.overrideMedia.disable()
        shutil.rmtree(self.tempMediaDir, ignore_errors=True)

    @patch("home.tasks.remove_files_older_than", return_value=2)
    def test_cleanup_old_files_scans_existing_media_folders(self, mockRemoveOlder):
        # Create the three media directories and verify cleanup_old_files scans each of them
        for folderName in ("clips", "speed_edited_videos", "speed_edit_uploads"):
            os.makedirs(os.path.join(self.tempMediaDir, folderName), exist_ok=True)

        cleanup_old_files()
        self.assertEqual(mockRemoveOlder.call_count, 3)

    @patch("home.tasks.delete_request_dir")
    def test_cleanup_cancelled_dir_task_only_deletes_when_status_is_cancelled(self, mockDeleteDir):
        clipRequest = ClipRequest.objects.create(
            youtube_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            start_time=datetime.time(0, 0, 10),
            end_time=datetime.time(0, 0, 20),
            status=CLIP_STATUS_CHOICES.COMPLETED,
        )
        # Should not delete folder if status is COMPLETED
        cleanup_cancelled_dir_task(str(clipRequest.id), "clip_request")
        mockDeleteDir.assert_not_called()

        # Should delete folder when status is CANCELLED
        clipRequest.status = CLIP_STATUS_CHOICES.CANCELLED
        clipRequest.save(update_fields=["status"])
        cleanup_cancelled_dir_task(str(clipRequest.id), "clip_request")
        mockDeleteDir.assert_called_once_with(str(clipRequest.id), "clip_request")

    @patch("home.tasks.Email.send_email")
    def test_send_email_task_calls_brevo_email(self, mockSendEmail):
        emailPayload = {"to_email": "user@example.com", "email_subject": "Test"}
        send_email_task.run(emailPayload)
        mockSendEmail.assert_called_once_with(emailPayload)
