"""API viewset tests for clip requests, downloads, speed edits, cancellation, and cleanup."""

import datetime
import shutil
import tempfile
import uuid
from types import SimpleNamespace
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from home.models import (
    CLIP_RESOLUTION,
    CLIP_STATUS_CHOICES,
    Clip,
    ClipRequest,
    SpeedEditRequest,
    VideoDetail,
)
from home.views import DownloadClipViewSet


class ClipRequestViewSetTest(APITestCase):
    """Tests for ClipRequestViewSet endpoints (list, create, task_status, send_clip_to_email)."""

    def setUp(self):
        self.clipRequest = ClipRequest.objects.create(
            youtube_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            start_time=datetime.time(0, 0, 10),
            end_time=datetime.time(0, 0, 25),
            processing_log={"steps": [{"step": "init"}]},
            error_message="sample error",
            proxy="http://proxy.example.com:8080",
            rq_job_id="job-111",
        )

    def test_list_excludes_processing_log_and_error_message(self):
        # List endpoint should omit verbose/internal fields via get_serializer_context
        url = reverse("clip-request-list")
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)
        self.assertNotIn("processing_log", response.data[0])
        self.assertNotIn("error_message", response.data[0])

    @patch("home.services.ClipProcessingService._check_ffmpeg")
    @patch("home.views.process_clip_task.delay")
    def test_create_clip_request_success_dispatches_celery_task(self, mockDelay, mockCheckFfmpeg):
        # Creating a valid clip request saves the row, dispatches Celery task, and stores rq_job_id
        mockDelay.return_value = SimpleNamespace(id="celery-task-123")
        url = reverse("clip-request-list")
        payload = {
            "youtube_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "start_time": "00:00:05",
            "end_time": "00:00:20",
        }
        response = self.client.post(url, payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["rq_job_id"], "celery-task-123")
        mockDelay.assert_called_once_with(response.data["id"])

    @patch("home.services.ClipProcessingService._check_ffmpeg")
    @patch("home.views.process_clip_task.delay")
    def test_create_clip_request_marks_failed_when_celery_dispatch_errors(self, mockDelay, mockCheckFfmpeg):
        # If Celery broker is unreachable during create, the saved ClipRequest is marked FAILED
        mockDelay.side_effect = RuntimeError("Broker connection refused")
        url = reverse("clip-request-list")
        payload = {
            "youtube_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "start_time": "00:00:05",
            "end_time": "00:00:20",
        }
        response = self.client.post(url, payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        latestRequest = ClipRequest.objects.exclude(id=self.clipRequest.id).first()
        self.assertEqual(latestRequest.status, CLIP_STATUS_CHOICES.FAILED)
        self.assertIn("Broker connection refused", latestRequest.error_message)

    def test_task_status_requires_valid_clip_request_id(self):
        # Missing or non-existent clip_request_id returns 400; valid ID returns status without internal fields
        url = reverse("clip-request-task-status")

        missingParamRes = self.client.get(url)
        self.assertEqual(missingParamRes.status_code, status.HTTP_400_BAD_REQUEST)

        notFoundRes = self.client.get(url, {"clip_request_id": str(uuid.uuid4())})
        self.assertEqual(notFoundRes.status_code, status.HTTP_400_BAD_REQUEST)

        validRes = self.client.get(url, {"clip_request_id": str(self.clipRequest.id)})
        self.assertEqual(validRes.status_code, status.HTTP_200_OK)
        for excludedKey in ("processing_log", "rq_job_id", "proxy"):
            self.assertNotIn(excludedKey, validRes.data)

    @patch("home.views.sendMail")
    def test_send_clip_to_email_validates_state_and_creates_user_on_success(self, mockSendMail):
        url = reverse("clip-request-send-clip-to-email")

        # Missing email parameter returns 400
        noEmailRes = self.client.get(url, {"clip_request_id": str(self.clipRequest.id)})
        self.assertEqual(noEmailRes.status_code, status.HTTP_400_BAD_REQUEST)

        # Cannot send email while clip request is still PENDING
        pendingRes = self.client.get(
            url,
            {"email": "newuser@example.com", "clip_request_id": str(self.clipRequest.id)},
        )
        self.assertEqual(pendingRes.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("PENDING", pendingRes.data["error"])

        # Mark request COMPLETED and verify user creation + sendMail call
        self.clipRequest.status = CLIP_STATUS_CHOICES.COMPLETED
        self.clipRequest.save(update_fields=["status"])

        successRes = self.client.get(
            url,
            {"email": "newuser@example.com", "clip_request_id": str(self.clipRequest.id)},
        )
        self.assertEqual(successRes.status_code, status.HTTP_200_OK)
        self.clipRequest.refresh_from_db()
        self.assertIsNotNone(self.clipRequest.user)
        self.assertEqual(self.clipRequest.user.email, "newuser@example.com")
        mockSendMail.assert_called_once()


class DownloadClipViewSetTest(APITestCase):
    """Tests for DownloadClipViewSet file streaming, validation, and filename sanitization."""

    def setUp(self):
        self.tempMediaDir = tempfile.mkdtemp()
        self.overrideMedia = override_settings(MEDIA_ROOT=self.tempMediaDir)
        self.overrideMedia.enable()

        self.videoDetail = VideoDetail.objects.create(
            video_id="dQw4w9WgXcQ",
            video_title='My:Awesome*Video?"Title"<>|',
        )
        self.clipRequest = ClipRequest.objects.create(
            youtube_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            start_time=datetime.time(0, 0, 10),
            end_time=datetime.time(0, 0, 20),
            video_info=self.videoDetail,
        )

    def tearDown(self):
        self.overrideMedia.disable()
        shutil.rmtree(self.tempMediaDir, ignore_errors=True)

    def _close_streaming_response(self, response):
        # Calling response.close() on a streaming FileResponse fires Django's
        # request_finished signal outside ClientHandler, which triggers
        # close_old_connections() and closes TestCase's atomic DB connection.
        fileHandle = getattr(response, "file_to_stream", None)
        if fileHandle and not fileHandle.closed:
            fileHandle.close()

    def test_download_clip_success_and_headers(self):
        # Valid clip file on disk should stream as video/mp4 attachment with security headers
        clipFile = SimpleUploadedFile("720p.mp4", b"fake-mp4-binary-content", content_type="video/mp4")
        clipObj = Clip.objects.create(
            clip_request=self.clipRequest,
            clip=clipFile,
            resolution=CLIP_RESOLUTION.RES_720P,
        )
        url = reverse("download-clip-detail", kwargs={"pk": str(clipObj.id)})
        response = self.client.get(url, {"file_type": "clip"})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response["Content-Type"], "video/mp4")
        self.assertIn("attachment;", response["Content-Disposition"])
        self.assertEqual(response["Access-Control-Allow-Origin"], "*")
        self._close_streaming_response(response)

    def test_download_speed_edit_success(self):
        # Valid speed_edit output video should be served when file_type=speed_edit
        outputVideo = SimpleUploadedFile("speed_1_5x.mp4", b"speed-edited-bytes", content_type="video/mp4")
        speedEditObj = SpeedEditRequest.objects.create(
            speed_factor=1.5,
            output_video=outputVideo,
        )
        url = reverse("download-clip-detail", kwargs={"pk": str(speedEditObj.id)})
        response = self.client.get(url, {"file_type": "speed_edit"})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("speed_edit_1.5x.mp4", response["Content-Disposition"])
        self._close_streaming_response(response)

    def test_download_errors_on_invalid_type_missing_record_or_empty_file(self):
        # Invalid file_type returns 500
        invalidTypeUrl = reverse("download-clip-detail", kwargs={"pk": str(uuid.uuid4())})
        invalidRes = self.client.get(invalidTypeUrl, {"file_type": "invalid"})
        self.assertEqual(invalidRes.status_code, status.HTTP_500_INTERNAL_SERVER_ERROR)

        # Non-existent clip ID returns 500
        notFoundRes = self.client.get(invalidTypeUrl, {"file_type": "clip"})
        self.assertEqual(notFoundRes.status_code, status.HTTP_500_INTERNAL_SERVER_ERROR)

        # Empty (0-byte) file on disk is rejected with 500
        emptyClipFile = SimpleUploadedFile("empty.mp4", b"", content_type="video/mp4")
        emptyClip = Clip.objects.create(
            clip_request=self.clipRequest,
            clip=emptyClipFile,
            resolution=CLIP_RESOLUTION.RES_720P,
        )
        emptyUrl = reverse("download-clip-detail", kwargs={"pk": str(emptyClip.id)})
        emptyRes = self.client.get(emptyUrl, {"file_type": "clip"})
        self.assertEqual(emptyRes.status_code, status.HTTP_500_INTERNAL_SERVER_ERROR)

    def test_generate_download_filename_sanitizes_and_truncates_long_titles(self):
        # Ensure special characters are replaced with '_' and titles > 50 chars are truncated
        viewSet = DownloadClipViewSet()
        self.videoDetail.video_title = "A" * 60 + ":*?"
        self.videoDetail.save()
        clipObj = Clip(clip_request=self.clipRequest, resolution="480p")

        fileName = viewSet._generate_download_filename(clipObj, fileType="clip")
        self.assertEqual(fileName, f"{'A' * 50}_480p.mp4")


class SpeedEditViewSetTest(APITestCase):
    """Tests for SpeedEditViewSet create and status endpoints."""

    def setUp(self):
        self.tempMediaDir = tempfile.mkdtemp()
        self.overrideMedia = override_settings(MEDIA_ROOT=self.tempMediaDir)
        self.overrideMedia.enable()

        self.clipRequest = ClipRequest.objects.create(
            youtube_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            start_time=datetime.time(0, 0, 10),
            end_time=datetime.time(0, 0, 20),
        )
        self.clipObj = Clip.objects.create(
            clip_request=self.clipRequest,
            clip=SimpleUploadedFile("clip.mp4", b"clip-bytes", content_type="video/mp4"),
            resolution=CLIP_RESOLUTION.RES_720P,
            size=1.0,
        )

    def tearDown(self):
        self.overrideMedia.disable()
        shutil.rmtree(self.tempMediaDir, ignore_errors=True)

    @patch("home.views.process_speed_edit_task.delay")
    def test_create_speed_edit_request_success(self, mockDelay):
        # Creating a speed edit request dispatches process_speed_edit_task and stores rq_job_id
        mockDelay.return_value = SimpleNamespace(id="speed-job-999")
        url = reverse("speed-edit-list")
        payload = {
            "source_clip": str(self.clipObj.id),
            "speed_factor": 1.5,
        }
        response = self.client.post(url, payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["rq_job_id"], "speed-job-999")
        mockDelay.assert_called_once_with(response.data["id"])

    def test_status_action_returns_speed_edit_details_or_400(self):
        speedEdit = SpeedEditRequest.objects.create(
            source_clip=self.clipObj,
            speed_factor=1.5,
        )
        url = reverse("speed-edit-status")

        # Missing request_id returns 400
        missingRes = self.client.get(url)
        self.assertEqual(missingRes.status_code, status.HTTP_400_BAD_REQUEST)

        # Valid request_id returns 200
        validRes = self.client.get(url, {"request_id": str(speedEdit.id)})
        self.assertEqual(validRes.status_code, status.HTTP_200_OK)
        self.assertEqual(validRes.data["id"], str(speedEdit.id))


class CancelAndCleanupViewSetsTest(APITestCase):
    """Tests for CancelRequestViewSet and CleanupOldFilesViewSet."""

    def setUp(self):
        self.clipRequest = ClipRequest.objects.create(
            youtube_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            start_time=datetime.time(0, 0, 10),
            end_time=datetime.time(0, 0, 20),
            status=CLIP_STATUS_CHOICES.PROCESSING,
            rq_job_id="active-job-id",
        )

    @patch("home.views.cleanup_cancelled_dir_task.apply_async")
    @patch("home.views.celery_app.control.revoke")
    @patch("home.views.celery_app.AsyncResult")
    def test_cancel_running_request_terminates_worker_and_schedules_dir_cleanup(
        self, mockAsyncResult, mockRevoke, mockCleanupAsync
    ):
        # When task state is STARTED, revoke must use terminate=True and schedule delayed folder cleanup
        mockAsyncResult.return_value = SimpleNamespace(state="STARTED")
        url = reverse("cancel-request")
        response = self.client.post(
            url,
            {"request_type": "clip_request", "request_id": str(self.clipRequest.id)},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.clipRequest.refresh_from_db()
        self.assertEqual(self.clipRequest.status, CLIP_STATUS_CHOICES.CANCELLED)
        mockRevoke.assert_called_once_with("active-job-id", terminate=True, signal="SIGTERM")
        mockCleanupAsync.assert_called_once_with(
            args=[str(self.clipRequest.id), "clip_request"],
            countdown=30,
        )

    @patch("home.views.cleanup_cancelled_dir_task.apply_async")
    @patch("home.views.celery_app.control.revoke")
    @patch("home.views.celery_app.AsyncResult")
    def test_cancel_queued_request_revokes_without_terminate(
        self, mockAsyncResult, mockRevoke, mockCleanupAsync
    ):
        # When task state is PENDING, revoke drops it from queue without terminating worker or scheduling cleanup
        mockAsyncResult.return_value = SimpleNamespace(state="PENDING")
        url = reverse("cancel-request")
        response = self.client.post(
            url,
            {"request_type": "clip_request", "request_id": str(self.clipRequest.id)},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        mockRevoke.assert_called_once_with("active-job-id")
        mockCleanupAsync.assert_not_called()

    def test_cancel_completed_request_returns_400(self):
        # Already completed requests cannot be cancelled
        self.clipRequest.status = CLIP_STATUS_CHOICES.COMPLETED
        self.clipRequest.save(update_fields=["status"])
        url = reverse("cancel-request")
        response = self.client.post(
            url,
            {"request_type": "clip_request", "request_id": str(self.clipRequest.id)},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    @patch("home.views.cleanup_old_files.delay")
    def test_cleanup_old_files_endpoint_triggers_task(self, mockCleanupDelay):
        # GET /api/cleanup-old-files/ dispatches background cleanup task
        url = reverse("cleanup-old-files")
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        mockCleanupDelay.assert_called_once()
