"""Unit tests for home serializers and validation rules."""

import datetime
import tempfile
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from utility.variables import maxSpeedEditBytes

from home.models import (
    CLIP_RESOLUTION,
    Clip,
    ClipRequest,
    SpeedEditRequest,
    User,
    VideoDetail,
)
from home.serializers import (
    ClipRequestSerializer,
    SpeedEditRequestSerializer,
    UserSerializer,
)


class UserSerializerTest(TestCase):
    """Tests for UserSerializer field exclusion and FieldMixin behavior."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="john",
            email="john@example.com",
            password="SecretPassword123",
        )

    def test_excludes_sensitive_auth_fields(self):
        # Password and privilege flags must never be exposed in serialized user output
        serializedData = UserSerializer(self.user).data
        self.assertEqual(serializedData["email"], "john@example.com")
        for excludedField in ("password", "is_staff", "is_superuser", "groups"):
            self.assertNotIn(excludedField, serializedData)

    def test_field_mixin_supports_dynamic_exclude_fields(self):
        # FieldMixin allows views to strip specific fields via serializer context
        serializedData = UserSerializer(
            self.user,
            context={"exclude_fields": ["phone", "username"]},
        ).data
        self.assertNotIn("phone", serializedData)
        self.assertNotIn("username", serializedData)
        self.assertIn("email", serializedData)


class ClipRequestSerializerTest(TestCase):
    """Tests for ClipRequestSerializer URL validation, timestamp checks, and output representation."""

    @patch("home.services.ClipProcessingService._check_ffmpeg")
    def test_valid_youtube_url_and_time_range_passes(self, mockCheckFfmpeg):
        # Standard YouTube URL with duration between 5s and 300s should be valid
        payload = {
            "youtube_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "start_time": "00:00:10",
            "end_time": "00:00:30",
        }
        serializer = ClipRequestSerializer(data=payload)
        self.assertTrue(serializer.is_valid(), serializer.errors)

    @patch("home.services.ClipProcessingService._check_ffmpeg")
    def test_invalid_youtube_url_fails_validation(self, mockCheckFfmpeg):
        # Reject non-YouTube domains early so invalid URLs are never queued in Celery
        payload = {
            "youtube_url": "https://vimeo.com/123456789",
            "start_time": "00:00:10",
            "end_time": "00:00:30",
        }
        serializer = ClipRequestSerializer(data=payload)
        self.assertFalse(serializer.is_valid())
        self.assertIn("youtube_url", serializer.errors)

    @patch("home.services.ClipProcessingService._check_ffmpeg")
    def test_end_time_before_or_equal_start_time_fails(self, mockCheckFfmpeg):
        # End time must be strictly after start time
        payload = {
            "youtube_url": "https://youtu.be/dQw4w9WgXcQ",
            "start_time": "00:00:30",
            "end_time": "00:00:20",
        }
        serializer = ClipRequestSerializer(data=payload)
        self.assertFalse(serializer.is_valid())
        self.assertIn("endTime", serializer.errors)

    @patch("home.services.ClipProcessingService._check_ffmpeg")
    def test_clip_duration_shorter_than_minimum_fails(self, mockCheckFfmpeg):
        # Duration shorter than minClipDurationInSec (5s) must be rejected
        payload = {
            "youtube_url": "https://youtu.be/dQw4w9WgXcQ",
            "start_time": "00:00:10",
            "end_time": "00:00:12",
        }
        serializer = ClipRequestSerializer(data=payload)
        self.assertFalse(serializer.is_valid())
        self.assertIn("endTime", serializer.errors)

    @patch("home.services.ClipProcessingService._check_ffmpeg")
    def test_clip_duration_longer_than_maximum_fails(self, mockCheckFfmpeg):
        # Duration exceeding maxClipDurationInSec (300s / 5 minutes) must be rejected
        payload = {
            "youtube_url": "https://youtu.be/dQw4w9WgXcQ",
            "start_time": "00:00:00",
            "end_time": "00:06:00",
        }
        serializer = ClipRequestSerializer(data=payload)
        self.assertFalse(serializer.is_valid())
        self.assertIn("endTime", serializer.errors)

    def test_to_representation_computes_duration_and_nests_video_info_and_clips(self):
        # Verify serialized output dynamically computes clip_duration and embeds video_info and clips
        videoDetail = VideoDetail.objects.create(
            video_id="dQw4w9WgXcQ",
            video_title="Test Title",
            video_duration=180,
        )
        clipRequest = ClipRequest.objects.create(
            youtube_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            start_time=datetime.time(0, 0, 10),
            end_time=datetime.time(0, 0, 45),
            video_info=videoDetail,
        )
        Clip.objects.create(
            clip_request=clipRequest,
            clip="clips/test/720p.mp4",
            resolution=CLIP_RESOLUTION.RES_720P,
            size=1.2,
            duration=35,
        )

        serializedData = ClipRequestSerializer(clipRequest).data
        self.assertEqual(serializedData["clip_duration"], 35)
        self.assertEqual(serializedData["video_info"]["video_id"], "dQw4w9WgXcQ")
        self.assertEqual(len(serializedData["clips"]), 1)
        self.assertEqual(serializedData["clips"][0]["resolution"], "720p")


@override_settings(MEDIA_ROOT=tempfile.gettempdir())
class SpeedEditRequestSerializerTest(TestCase):
    """Tests for SpeedEditRequestSerializer validation, creation, and representation."""

    def setUp(self):
        self.clipRequest = ClipRequest.objects.create(
            youtube_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            start_time=datetime.time(0, 0, 10),
            end_time=datetime.time(0, 0, 20),
        )
        self.clipObj = Clip.objects.create(
            clip_request=self.clipRequest,
            clip=SimpleUploadedFile("source.mp4", b"x" * 1024, content_type="video/mp4"),
            resolution=CLIP_RESOLUTION.RES_720P,
            size=1.0,
        )

    def test_validate_speed_factor_rejects_out_of_bounds_values(self):
        # Speed factor must be positive and within [0.25, 2.0]
        for invalidFactor in (-1.0, 0.0, 0.1, 2.5):
            serializer = SpeedEditRequestSerializer(
                data={"source_clip": self.clipObj.id, "speed_factor": invalidFactor}
            )
            self.assertFalse(serializer.is_valid())
            self.assertIn("speed_factor", serializer.errors)

    def test_validate_uploaded_video_rejects_files_exceeding_max_size(self):
        # Reject videos larger than maxSpeedEditBytes (50 MB) to prevent heavy re-encodes
        oversizedFile = SimpleUploadedFile("huge.mp4", b"a", content_type="video/mp4")
        oversizedFile.size = maxSpeedEditBytes + 1
        serializer = SpeedEditRequestSerializer(
            data={"uploaded_video": oversizedFile, "speed_factor": 1.5}
        )
        self.assertFalse(serializer.is_valid())
        self.assertIn("uploaded_video", serializer.errors)

    def test_validate_requires_exactly_one_source(self):
        # Reject when neither uploaded_video nor source_clip is provided
        emptySourceSerializer = SpeedEditRequestSerializer(data={"speed_factor": 1.5})
        self.assertFalse(emptySourceSerializer.is_valid())

        # Reject when both uploaded_video and source_clip are provided simultaneously
        validFile = SimpleUploadedFile("small.mp4", b"video-data", content_type="video/mp4")
        bothSourcesSerializer = SpeedEditRequestSerializer(
            data={
                "uploaded_video": validFile,
                "source_clip": self.clipObj.id,
                "speed_factor": 1.5,
            }
        )
        self.assertFalse(bothSourcesSerializer.is_valid())

    def test_create_sets_is_active_and_computes_original_size_mb(self):
        # Creating a SpeedEditRequest with an uploaded file should set is_active=True and compute original_size in MB
        twoMegabytes = 2 * 1024 * 1024
        uploadedVideo = SimpleUploadedFile("two_mb.mp4", b"0" * twoMegabytes, content_type="video/mp4")
        serializer = SpeedEditRequestSerializer(
            data={"uploaded_video": uploadedVideo, "speed_factor": 1.25}
        )
        self.assertTrue(serializer.is_valid(), serializer.errors)
        speedEditRequest = serializer.save()

        self.assertTrue(speedEditRequest.is_active)
        self.assertEqual(speedEditRequest.original_size, 2.0)

    def test_to_representation_embeds_nested_source_clip(self):
        # Serialized representation should expand source_clip into full ClipSerializer object
        speedEditRequest = SpeedEditRequest.objects.create(
            source_clip=self.clipObj,
            speed_factor=1.5,
        )
        serializedData = SpeedEditRequestSerializer(speedEditRequest).data
        self.assertIsInstance(serializedData["source_clip"], dict)
        self.assertEqual(serializedData["source_clip"]["id"], str(self.clipObj.id))
