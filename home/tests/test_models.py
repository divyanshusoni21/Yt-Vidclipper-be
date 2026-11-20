"""Unit tests for home models and upload path helper functions."""

import datetime
import tempfile
from types import SimpleNamespace

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from home.models import (
    CLIP_RESOLUTION,
    CLIP_STATUS_CHOICES,
    Clip,
    ClipRequest,
    SpeedEditRequest,
    User,
    VideoDetail,
    clip_file_path,
    speed_edit_output_path,
    speed_edit_upload_path,
)


class UploadPathFunctionsTest(TestCase):
    """Tests for dynamic file upload path generators."""

    def test_clip_file_path_with_request_id(self):
        # Verify clip upload path uses clip_request_id when present
        dummyInstance = SimpleNamespace(clip_request_id="req-123")
        generatedPath = clip_file_path(dummyInstance, "720p.mp4")
        self.assertEqual(generatedPath, "clips/req-123/720p.mp4")

    def test_clip_file_path_without_request_id_falls_back_to_unknown(self):
        # Fallback to 'unknown' prevents broken path strings if clip_request_id is None
        dummyInstance = SimpleNamespace(clip_request_id=None)
        generatedPath = clip_file_path(dummyInstance, "720p.mp4")
        self.assertEqual(generatedPath, "clips/unknown/720p.mp4")

    def test_speed_edit_upload_and_output_paths(self):
        # Verify speed edit upload and output paths organize files by request UUID
        dummyInstance = SimpleNamespace(id="speed-456")
        uploadPath = speed_edit_upload_path(dummyInstance, "input.mp4")
        outputPath = speed_edit_output_path(dummyInstance, "speed_1_5x.mp4")
        self.assertEqual(uploadPath, "speed_edit_uploads/speed-456/input.mp4")
        self.assertEqual(outputPath, "speed_edited_videos/speed-456/speed_1_5x.mp4")


class UserModelTest(TestCase):
    """Tests for custom User model methods and properties."""

    def setUp(self):
        # Create a test user for authentication and string representation checks
        self.user = User.objects.create_user(
            username="tester",
            email="tester@example.com",
            password="StrongPassword123!",
        )

    def test_user_str_returns_email(self):
        # Email is the USERNAME_FIELD, so __str__ should return the user's email
        self.assertEqual(str(self.user), "tester@example.com")

    def test_user_tokens_returns_refresh_and_access_jwt(self):
        # Verify tokens() generates non-empty SimpleJWT refresh and access tokens
        tokenData = self.user.tokens()
        self.assertIn("refresh", tokenData)
        self.assertIn("access", tokenData)
        self.assertTrue(isinstance(tokenData["refresh"], str) and len(tokenData["refresh"]) > 0)
        self.assertTrue(isinstance(tokenData["access"], str) and len(tokenData["access"]) > 0)


class VideoAndClipModelsTest(TestCase):
    """Tests for VideoDetail, ClipRequest, and Clip models."""

    def setUp(self):
        # Set up linked VideoDetail, ClipRequest, and Clip instances
        self.videoDetail = VideoDetail.objects.create(
            video_id="dQw4w9WgXcQ",
            video_duration=212,
            channel_name="Test Channel",
            channel_id="UC12345",
            video_title="Sample Video",
        )
        self.clipRequest = ClipRequest.objects.create(
            youtube_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            start_time=datetime.time(0, 0, 10),
            end_time=datetime.time(0, 0, 25),
            video_info=self.videoDetail,
        )

    def test_video_detail_str(self):
        # Confirm VideoDetail string representation combines title and video_id
        self.assertEqual(str(self.videoDetail), "Sample Video - dQw4w9WgXcQ")

    def test_clip_request_defaults_and_str(self):
        # New clip requests must start in PENDING state with an empty dict processing_log
        self.assertEqual(self.clipRequest.status, CLIP_STATUS_CHOICES.PENDING)
        self.assertEqual(self.clipRequest.processing_log, {})
        expectedStr = (
            f"ClipRequest {self.clipRequest.id} - "
            f"{self.clipRequest.youtube_url} ({self.clipRequest.start_time}-{self.clipRequest.end_time}s)"
        )
        self.assertEqual(str(self.clipRequest), expectedStr)

    def test_clip_str(self):
        # Confirm Clip string representation shows request ID and resolution
        clipObj = Clip.objects.create(
            clip_request=self.clipRequest,
            clip="clips/test/720p.mp4",
            size=2.5,
            duration=15,
            resolution=CLIP_RESOLUTION.RES_720P,
        )
        self.assertEqual(str(clipObj), f"{self.clipRequest.id} - 720p")


@override_settings(MEDIA_ROOT=tempfile.gettempdir())
class SpeedEditRequestModelTest(TestCase):
    """Tests for SpeedEditRequest string formatting and source path resolution."""

    def setUp(self):
        # Create a base ClipRequest and Clip to test source_clip reference
        self.clipRequest = ClipRequest.objects.create(
            youtube_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            start_time=datetime.time(0, 0, 5),
            end_time=datetime.time(0, 0, 15),
        )
        self.clipFile = SimpleUploadedFile("clip.mp4", b"dummy video content", content_type="video/mp4")
        self.clipObj = Clip.objects.create(
            clip_request=self.clipRequest,
            clip=self.clipFile,
            resolution=CLIP_RESOLUTION.RES_720P,
        )

    def test_speed_edit_with_uploaded_video(self):
        # When uploaded_video is set, __str__ indicates 'uploaded' and get_source_path returns its path
        uploadedVideo = SimpleUploadedFile("upload.mp4", b"uploaded video bytes", content_type="video/mp4")
        speedEditRequest = SpeedEditRequest.objects.create(
            uploaded_video=uploadedVideo,
            speed_factor=1.5,
        )
        self.assertEqual(str(speedEditRequest), f"SpeedEdit {speedEditRequest.id} - uploaded @ 1.5x")
        self.assertEqual(speedEditRequest.get_source_path(), speedEditRequest.uploaded_video.path)

    def test_speed_edit_with_source_clip(self):
        # When source_clip is set, __str__ references clip ID and get_source_path returns clip's file path
        speedEditRequest = SpeedEditRequest.objects.create(
            source_clip=self.clipObj,
            speed_factor=0.5,
        )
        self.assertEqual(
            str(speedEditRequest),
            f"SpeedEdit {speedEditRequest.id} - clip_{self.clipObj.id} @ 0.5x",
        )
        self.assertEqual(speedEditRequest.get_source_path(), self.clipObj.clip.path)

    def test_get_source_path_returns_none_when_no_source_present(self):
        # If neither uploaded_video nor source_clip is set, get_source_path safely returns None
        speedEditRequest = SpeedEditRequest.objects.create(speed_factor=1.25)
        self.assertIsNone(speedEditRequest.get_source_path())
