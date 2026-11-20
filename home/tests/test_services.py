"""Unit tests for ClipProcessingService and SpeedEditService."""

import datetime
import os
import shutil
import tempfile
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from celery.exceptions import SoftTimeLimitExceeded
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from home.models import (
    CLIP_STATUS_CHOICES,
    Clip,
    ClipRequest,
    SpeedEditRequest,
)
from home.services import (
    ClipProcessingService,
    ProcessingFailedException,
    SpeedEditService,
)


class ClipProcessingServiceTest(TestCase):
    """Tests for ClipProcessingService URL validation, proxy rotation, and clip processing."""

    def setUp(self):
        self.tempMediaDir = tempfile.mkdtemp()
        self.overrideMedia = override_settings(MEDIA_ROOT=self.tempMediaDir)
        self.overrideMedia.enable()

        self.clipRequest = ClipRequest.objects.create(
            youtube_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            start_time=datetime.time(0, 0, 10),
            end_time=datetime.time(0, 0, 20),
        )

    def tearDown(self):
        self.overrideMedia.disable()
        shutil.rmtree(self.tempMediaDir, ignore_errors=True)

    @patch("home.services.shutil.which", return_value=None)
    def test_check_ffmpeg_raises_when_missing(self, mockWhich):
        # Service instantiation must fail fast if ffmpeg binary is missing from PATH
        with self.assertRaises(FileNotFoundError):
            ClipProcessingService()

    @patch("home.services.shutil.which", return_value="/usr/bin/ffmpeg")
    def test_validate_youtube_url_patterns(self, mockWhich):
        service = ClipProcessingService()
        validUrls = (
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "https://youtu.be/dQw4w9WgXcQ",
            "https://www.youtube.com/embed/dQw4w9WgXcQ",
            "https://www.youtube.com/v/dQw4w9WgXcQ",
            "https://www.youtube.com/live/dQw4w9WgXcQ?feature=share",
            "https://www.youtube.com/shorts/dQw4w9WgXcQ",
        )
        for validUrl in validUrls:
            self.assertTrue(service.validate_youtube_url(validUrl), f"Expected valid: {validUrl}")

        for invalidUrl in ("", None, 123, "https://example.com/watch?v=dQw4w9WgXcQ"):
            self.assertFalse(service.validate_youtube_url(invalidUrl))

    @patch("home.services.shutil.which", return_value="/usr/bin/ffmpeg")
    @patch("home.services.proxies", ["http://proxy1:8080", "http://proxy2:8080"])
    def test_get_proxy_rotates_away_from_last_used_proxy(self, mockWhich):
        # If the most recent ClipRequest used proxy1, get_proxy should select proxy2
        self.clipRequest.proxy = "http://proxy1:8080"
        self.clipRequest.save(update_fields=["proxy"])

        service = ClipProcessingService()
        chosenProxy = service.get_proxy()
        self.assertEqual(chosenProxy, "http://proxy2:8080")

    @patch("home.services.shutil.which", return_value="/usr/bin/ffmpeg")
    def test_save_video_info_creates_video_detail_and_caps_end_time(self, mockWhich):
        # When requested endSec exceeds actual video duration, end_time is capped to duration_string
        service = ClipProcessingService()
        infoDict = {
            "id": "dQw4w9WgXcQ",
            "duration": 15,
            "duration_string": "00:00:15",
            "title": "Rick Astley",
            "channel": "Official Channel",
            "channel_id": "UC123",
        }
        service.save_video_info(infoDict, self.clipRequest, clipDurationSeconds=10, endSec=20)
        self.clipRequest.refresh_from_db()

        self.assertIsNotNone(self.clipRequest.video_info)
        self.assertEqual(self.clipRequest.video_info.video_id, "dQw4w9WgXcQ")
        self.assertEqual(str(self.clipRequest.end_time), "00:00:15")

    @patch("home.services.shutil.which", return_value="/usr/bin/ffmpeg")
    @patch("home.services.yt_dlp.YoutubeDL")
    def test_download_and_create_clips_raises_if_start_beyond_duration(self, mockYdl, mockWhich):
        # Start time beyond total video duration is invalid and must raise ProcessingFailedException
        mockYdlInstance = MagicMock()
        mockYdlInstance.extract_info.return_value = {"id": "dQw4w9WgXcQ", "duration": 5}
        mockYdl.return_value.__enter__.return_value = mockYdlInstance

        service = ClipProcessingService()
        out720 = os.path.join(self.tempMediaDir, "720p.mp4")
        out480 = os.path.join(self.tempMediaDir, "480p.mp4")

        with self.assertRaises(ProcessingFailedException):
            service.download_and_create_clips(
                self.clipRequest,
                startSec=10,
                endSec=20,
                clipDurationSeconds=10,
                out720pPathAbsolute=out720,
                out480pPathAbsolute=out480,
            )

    @patch("home.services.shutil.which", return_value="/usr/bin/ffmpeg")
    def test_process_clip_request_success_creates_720p_and_480p_clips(self, mockWhich):
        service = ClipProcessingService()

        def fake_download(clipReq, startSec, endSec, durationSec, out720Path, out480Path):
            # Write non-empty dummy MP4 files to simulate yt-dlp output
            for filePath in (out720Path, out480Path):
                os.makedirs(os.path.dirname(filePath), exist_ok=True)
                with open(filePath, "wb") as fileHandle:
                    fileHandle.write(b"0" * (512 * 1024))
            return "http://proxy1:8080"

        with patch.object(service, "download_and_create_clips", side_effect=fake_download):
            result = service.process_clip_request(self.clipRequest)

        self.assertTrue(result)
        self.clipRequest.refresh_from_db()
        self.assertEqual(self.clipRequest.status, CLIP_STATUS_CHOICES.COMPLETED)
        self.assertEqual(self.clipRequest.proxy, "http://proxy1:8080")
        self.assertEqual(Clip.objects.filter(clip_request=self.clipRequest).count(), 2)

    @patch("home.services.shutil.which", return_value="/usr/bin/ffmpeg")
    def test_process_clip_request_preserves_cancelled_status(self, mockWhich):
        # If user cancels while download is running, process_clip_request must not overwrite CANCELLED with COMPLETED
        service = ClipProcessingService()

        def fake_download_Then_cancel(clipReq, startSec, endSec, durationSec, out720Path, out480Path):
            for filePath in (out720Path, out480Path):
                os.makedirs(os.path.dirname(filePath), exist_ok=True)
                with open(filePath, "wb") as fileHandle:
                    fileHandle.write(b"video-bytes")
            ClipRequest.objects.filter(id=clipReq.id).update(status=CLIP_STATUS_CHOICES.CANCELLED)
            return ""

        with patch.object(service, "download_and_create_clips", side_effect=fake_download_Then_cancel):
            result = service.process_clip_request(self.clipRequest)

        self.assertFalse(result)
        self.clipRequest.refresh_from_db()
        self.assertEqual(self.clipRequest.status, CLIP_STATUS_CHOICES.CANCELLED)

    @patch("home.services.shutil.which", return_value="/usr/bin/ffmpeg")
    def test_process_clip_request_handles_soft_time_limit_exceeded(self, mockWhich):
        # SoftTimeLimitExceeded must set friendly timeout message on error_message and re-raise
        service = ClipProcessingService()
        with (
            patch.object(
                service,
                "download_and_create_clips",
                side_effect=SoftTimeLimitExceeded(),
            ),
            self.assertRaises(SoftTimeLimitExceeded),
        ):
            service.process_clip_request(self.clipRequest)

        self.clipRequest.refresh_from_db()
        self.assertEqual(self.clipRequest.status, CLIP_STATUS_CHOICES.FAILED)
        self.assertIn("timed out", self.clipRequest.error_message)


class SpeedEditServiceTest(TestCase):
    """Tests for SpeedEditService FFmpeg invocation, duration probing, and status updates."""

    def setUp(self):
        self.tempMediaDir = tempfile.mkdtemp()
        self.overrideMedia = override_settings(MEDIA_ROOT=self.tempMediaDir)
        self.overrideMedia.enable()

        self.uploadedVideo = SimpleUploadedFile("input.mp4", b"source-video-bytes", content_type="video/mp4")
        self.speedEditRequest = SpeedEditRequest.objects.create(
            uploaded_video=self.uploadedVideo,
            speed_factor=0.25,
        )

    def tearDown(self):
        self.overrideMedia.disable()
        shutil.rmtree(self.tempMediaDir, ignore_errors=True)

    @patch("home.services.shutil.which", return_value="/usr/bin/ffmpeg")
    @patch("home.services.subprocess.run")
    def test_process_speed_edit_request_success_chains_atempo_filters(self, mockSubprocessRun, mockWhich):
        # For 0.25x speed, FFmpeg requires chaining two atempo=0.5 filters because atempo minimum is 0.5
        def fake_subprocess_run(cmd, **kwargs):
            if cmd[0] == "ffprobe":
                return SimpleNamespace(stdout="20.0\n")
            # Last element of ffmpeg command is outputPath; create non-empty output file
            outputPath = cmd[-1]
            os.makedirs(os.path.dirname(outputPath), exist_ok=True)
            with open(outputPath, "wb") as outHandle:
                outHandle.write(b"0" * (1024 * 1024))
            return SimpleNamespace(returncode=0)

        mockSubprocessRun.side_effect = fake_subprocess_run
        service = SpeedEditService()
        result = service.process_speed_edit_request(self.speedEditRequest)

        self.assertTrue(result)
        self.speedEditRequest.refresh_from_db()
        self.assertEqual(self.speedEditRequest.status, CLIP_STATUS_CHOICES.COMPLETED)
        self.assertEqual(self.speedEditRequest.original_duration, 20)
        self.assertEqual(self.speedEditRequest.output_duration, 80)

        # Verify ffmpeg filter_complex chained atempo=0.5,atempo=0.5
        ffmpegCallArgs = mockSubprocessRun.call_args_list[1][0][0]
        filterComplexArg = ffmpegCallArgs[ffmpegCallArgs.index("-filter_complex") + 1]
        self.assertIn("atempo=0.5,atempo=0.5", filterComplexArg)

    @patch("home.services.shutil.which", return_value="/usr/bin/ffmpeg")
    def test_process_speed_edit_fails_when_source_missing(self, mockWhich):
        # Missing source file on disk must mark request FAILED and raise ProcessingFailedException
        emptyRequest = SpeedEditRequest.objects.create(speed_factor=1.5)
        service = SpeedEditService()

        with self.assertRaises(ProcessingFailedException):
            service.process_speed_edit_request(emptyRequest)

        emptyRequest.refresh_from_db()
        self.assertEqual(emptyRequest.status, CLIP_STATUS_CHOICES.FAILED)
        self.assertIn("Source video not found", emptyRequest.error_message)

    @patch("home.services.shutil.which", return_value="/usr/bin/ffmpeg")
    @patch("home.services.subprocess.run", side_effect=OSError("ffprobe failed"))
    def test_get_video_duration_returns_zero_on_error(self, mockRun, mockWhich):
        # _get_video_duration should gracefully fall back to 0 when ffprobe fails
        service = SpeedEditService()
        self.assertEqual(service._get_video_duration("/nonexistent/file.mp4"), 0)
