from django.test import TestCase
from unittest.mock import patch, MagicMock
from home.services import VideoInfoService, VideoNotAvailableException, InvalidUrlException
import yt_dlp


class VideoInfoServiceTest(TestCase):
    """Test cases for VideoInfoService class."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.service = VideoInfoService()
        self.valid_urls = [
            'https://www.youtube.com/watch?v=dQw4w9WgXcQ',
            'https://youtu.be/dQw4w9WgXcQ',
            'https://www.youtube.com/embed/dQw4w9WgXcQ',
            'https://www.youtube.com/v/dQw4w9WgXcQ',
            'youtube.com/watch?v=dQw4w9WgXcQ',
            'youtu.be/dQw4w9WgXcQ',
        ]
        self.invalid_urls = [
            'https://www.google.com',
            'not_a_url',
            '',
            None,
            'https://www.youtube.com/watch?v=invalid',
            'https://vimeo.com/123456789',
        ]
    
    def test_validateYoutubeUrl_valid_urls(self):
        """Test validation of valid YouTube URLs."""
        for url in self.valid_urls:
            with self.subTest(url=url):
                self.assertTrue(self.service.validateYoutubeUrl(url))
    
    def test_validateYoutubeUrl_invalid_urls(self):
        """Test validation of invalid URLs."""
        for url in self.invalid_urls:
            with self.subTest(url=url):
                self.assertFalse(self.service.validateYoutubeUrl(url))
    
    def test_extractVideoId_valid_urls(self):
        """Test video ID extraction from valid URLs."""
        expected_id = 'dQw4w9WgXcQ'
        for url in self.valid_urls:
            with self.subTest(url=url):
                video_id = self.service.extractVideoId(url)
                self.assertEqual(video_id, expected_id)
    
    def test_extractVideoId_invalid_urls(self):
        """Test video ID extraction from invalid URLs raises exception."""
        for url in self.invalid_urls:
            with self.subTest(url=url):
                with self.assertRaises(InvalidUrlException):
                    self.service.extractVideoId(url)
    
    @patch('yt_dlp.YoutubeDL')
    def test_getVideoInfo_success(self, mock_ydl_class):
        """Test successful video info extraction."""
        # Mock yt-dlp response
        mock_info = {
            'id': 'dQw4w9WgXcQ',
            'title': 'Test Video',
            'duration': 212,
            'description': 'Test description',
            'upload_date': '20091025',
            'uploader': 'Test Channel',
            'uploader_id': 'testchannel',
            'channel': 'Test Channel',
            'channel_id': 'UC123456789',
            'channel_url': 'https://www.youtube.com/channel/UC123456789',
            'view_count': 1000000,
            'like_count': 50000,
            'thumbnail': 'https://img.youtube.com/vi/dQw4w9WgXcQ/maxresdefault.jpg',
            'webpage_url': 'https://www.youtube.com/watch?v=dQw4w9WgXcQ',
            'availability': 'public',
            'age_limit': 0,
            'is_live': False,
            'was_live': False,
        }
        
        mock_ydl_instance = MagicMock()
        mock_ydl_instance.extract_info.return_value = mock_info
        mock_ydl_class.return_value.__enter__.return_value = mock_ydl_instance
        
        url = 'https://www.youtube.com/watch?v=dQw4w9WgXcQ'
        result = self.service.getVideoInfo(url)
        
        # Verify the result contains expected fields
        self.assertEqual(result['id'], 'dQw4w9WgXcQ')
        self.assertEqual(result['title'], 'Test Video')
        self.assertEqual(result['duration'], 212)
        self.assertEqual(result['channel'], 'Test Channel')
        self.assertEqual(result['channel_id'], 'UC123456789')
        
        # Verify yt-dlp was called correctly
        mock_ydl_instance.extract_info.assert_called_once_with(url, download=False)
    
    @patch('yt_dlp.YoutubeDL')
    def test_getVideoInfo_private_video(self, mock_ydl_class):
        """Test handling of private video error."""
        mock_ydl_instance = MagicMock()
        mock_ydl_instance.extract_info.side_effect = yt_dlp.DownloadError('Private video')
        mock_ydl_class.return_value.__enter__.return_value = mock_ydl_instance
        
        url = 'https://www.youtube.com/watch?v=dQw4w9WgXcQ'
        
        with self.assertRaises(VideoNotAvailableException) as context:
            self.service.getVideoInfo(url)
        
        self.assertIn('private', str(context.exception).lower())
    
    @patch('yt_dlp.YoutubeDL')
    def test_getVideoInfo_unavailable_video(self, mock_ydl_class):
        """Test handling of unavailable video error."""
        mock_ydl_instance = MagicMock()
        mock_ydl_instance.extract_info.side_effect = yt_dlp.DownloadError('Video unavailable')
        mock_ydl_class.return_value.__enter__.return_value = mock_ydl_instance
        
        url = 'https://www.youtube.com/watch?v=dQw4w9WgXcQ'
        
        with self.assertRaises(VideoNotAvailableException) as context:
            self.service.getVideoInfo(url)
        
        self.assertIn('unavailable', str(context.exception).lower())
    
    def test_getVideoInfo_invalid_url(self):
        """Test getVideoInfo with invalid URL."""
        invalid_url = 'https://www.google.com'
        
        with self.assertRaises(InvalidUrlException):
            self.service.getVideoInfo(invalid_url)
    
    @patch('yt_dlp.YoutubeDL')
    def test_getChannelInfo_success(self, mock_ydl_class):
        """Test successful channel info extraction."""
        mock_info = {
            'id': 'dQw4w9WgXcQ',
            'title': 'Test Video',
            'channel': 'Test Channel',
            'channel_id': 'UC123456789',
            'channel_url': 'https://www.youtube.com/channel/UC123456789',
            'uploader': 'Test Channel',
            'uploader_id': 'testchannel',
        }
        
        mock_ydl_instance = MagicMock()
        mock_ydl_instance.extract_info.return_value = mock_info
        mock_ydl_class.return_value.__enter__.return_value = mock_ydl_instance
        
        url = 'https://www.youtube.com/watch?v=dQw4w9WgXcQ'
        result = self.service.getChannelInfo(url)
        
        # Verify channel info
        self.assertEqual(result['channel_id'], 'UC123456789')
        self.assertEqual(result['channel_name'], 'Test Channel')
        self.assertEqual(result['channel_url'], 'https://www.youtube.com/channel/UC123456789')
        self.assertEqual(result['uploader'], 'Test Channel')
        self.assertEqual(result['uploader_id'], 'testchannel')
