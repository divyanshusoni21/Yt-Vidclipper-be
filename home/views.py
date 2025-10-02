import os
import logging
from django.shortcuts import render
from django.http import HttpResponse, Http404, FileResponse
from django.db import transaction, models
from django.conf import settings
from django.utils.decorators import method_decorator
from django.views.decorators.cache import cache_page
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import AllowAny
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.filters import SearchFilter, OrderingFilter

from .models import ClipRequest, ClipAnalytics
from .serializers import ClipRequestSerializer, ClipAnalyticsSerializer
from .services import VideoInfoService, HybridProcessingService
from utility.functions import runSerializer

logger = logging.getLogger('django')


class ClipRequestViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing clip requests with full CRUD operations
    Follows established patterns with proper error handling and logging
    """
    queryset = ClipRequest.objects.all()
    serializer_class = ClipRequestSerializer
    permission_classes = [AllowAny]
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = ['status', 'processing_method', 'channel_id']
    search_fields = ['youtube_url', 'original_title', 'channel_name']
    ordering_fields = ['created_at', 'updated_at', 'processed_at']
    ordering = ['-created_at']

    def get_queryset(self):
        """
        Optimize queryset with proper field selection and prefetching
        """
        queryset = super().get_queryset()
        
        # Optimize for list view
        if self.action == 'list':
            queryset = queryset.select_related().only(
                'id', 'youtube_url', 'start_time', 'end_time', 'status',
                'created_at', 'updated_at', 'original_title', 'channel_name',
                'processing_method', 'file_size'
            )
        
        return queryset

    def get_serializer_context(self):
        """
        Add field exclusion context for different actions
        """
        context = super().get_serializer_context()
        
        # Exclude sensitive fields in list view
        if self.action == 'list':
            context['exclude_fields'] = ['processing_log', 'error_message']
        
        return context

    @transaction.atomic
    def create(self, request, *args, **kwargs):
        """
        Create a new clip request using runSerializer with transaction management
        """
        try:
            logger.info(f"Creating new clip request with data: {request.data}")
            
            # Use runSerializer for consistent object creation
            clipRequest, serializer = runSerializer(
                ClipRequestSerializer, 
                request.data, 
                request=request
            )
            
            # Validate YouTube URL and get video info
            videoInfoService = VideoInfoService()
            try:
                videoInfo = videoInfoService.getVideoInfo(clipRequest.youtube_url)
                
                # Update clip request with video information
                clipRequest.original_title = videoInfo.get('title', '')
                clipRequest.video_duration = videoInfo.get('duration', 0)
                clipRequest.channel_name = videoInfo.get('channel', '')
                clipRequest.channel_id = videoInfo.get('channel_id', '')
                
                # Validate timestamp range against video duration
                if clipRequest.end_time > videoInfo.get('duration', 0):
                    logger.warning(f"End time {clipRequest.end_time} exceeds video duration {videoInfo.get('duration', 0)}")
                    return Response({
                        'error': 'End time exceeds video duration',
                        'video_duration': videoInfo.get('duration', 0)
                    }, status=status.HTTP_400_BAD_REQUEST)
                
                clipRequest.save()
                
                # TODO: Queue background processing task
                # process_clip_task.delay(clipRequest.id)
                
                logger.info(f"Successfully created clip request {clipRequest.id}")
                
                return Response(
                    ClipRequestSerializer(clipRequest).data,
                    status=status.HTTP_201_CREATED
                )
                
            except Exception as videoError:
                logger.error(f"Video validation failed for {clipRequest.youtube_url}: {str(videoError)}")
                return Response({
                    'error': 'Failed to validate YouTube URL',
                    'details': str(videoError)
                }, status=status.HTTP_400_BAD_REQUEST)
                
        except Exception as e:
            logger.error(f"Failed to create clip request: {str(e)}")
            return Response({
                'error': 'Failed to create clip request',
                'details': str(e)
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    def list(self, request, *args, **kwargs):
        """
        List clip requests with proper queryset optimization and field exclusion
        """
        try:
            logger.info(f"Listing clip requests with filters: {request.query_params}")
            
            queryset = self.filter_queryset(self.get_queryset())
            page = self.paginate_queryset(queryset)
            
            if page is not None:
                serializer = self.get_serializer(page, many=True)
                return self.get_paginated_response(serializer.data)
            
            serializer = self.get_serializer(queryset, many=True)
            return Response(serializer.data)
            
        except Exception as e:
            logger.error(f"Failed to list clip requests: {str(e)}")
            return Response({
                'error': 'Failed to retrieve clip requests',
                'details': str(e)
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    def retrieve(self, request, *args, **kwargs):
        """
        Retrieve individual clip request status with full details
        """
        try:
            instance = self.get_object()
            logger.info(f"Retrieving clip request {instance.id}")
            
            serializer = self.get_serializer(instance)
            return Response(serializer.data)
            
        except Http404:
            logger.warning(f"Clip request not found: {kwargs.get('pk')}")
            return Response({
                'error': 'Clip request not found'
            }, status=status.HTTP_404_NOT_FOUND)
        except Exception as e:
            logger.error(f"Failed to retrieve clip request: {str(e)}")
            return Response({
                'error': 'Failed to retrieve clip request',
                'details': str(e)
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=True, methods=['get'])
    def status(self, request, pk=None):
        """
        Get detailed status information for a clip request
        """
        try:
            clipRequest = self.get_object()
            logger.info(f"Getting status for clip request {clipRequest.id}")
            
            statusData = {
                'id': clipRequest.id,
                'status': clipRequest.status,
                'created_at': clipRequest.created_at,
                'updated_at': clipRequest.updated_at,
                'processed_at': clipRequest.processed_at,
                'processing_method': clipRequest.processing_method,
                'error_message': clipRequest.error_message,
                'file_size': clipRequest.file_size,
                'processing_log': clipRequest.processing_log,
            }
            
            # Add download URL if completed
            if clipRequest.status == 'completed' and clipRequest.file_path:
                statusData['download_url'] = f'/api/clips/{clipRequest.id}/download/'
            
            return Response(statusData)
            
        except Http404:
            return Response({
                'error': 'Clip request not found'
            }, status=status.HTTP_404_NOT_FOUND)
        except Exception as e:
            logger.error(f"Failed to get status: {str(e)}")
            return Response({
                'error': 'Failed to get status',
                'details': str(e)
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=False, methods=['get'])
    def search(self, request):
        """
        Advanced search functionality for clip requests
        """
        try:
            query = request.query_params.get('q', '')
            status_filter = request.query_params.get('status', '')
            channel_filter = request.query_params.get('channel', '')
            
            logger.info(f"Searching clip requests: query='{query}', status='{status_filter}', channel='{channel_filter}'")
            
            queryset = self.get_queryset()
            
            if query:
                queryset = queryset.filter(
                    models.Q(original_title__icontains=query) |
                    models.Q(channel_name__icontains=query) |
                    models.Q(youtube_url__icontains=query)
                )
            
            if status_filter:
                queryset = queryset.filter(status=status_filter)
            
            if channel_filter:
                queryset = queryset.filter(channel_name__icontains=channel_filter)
            
            serializer = self.get_serializer(queryset, many=True)
            return Response(serializer.data)
            
        except Exception as e:
            logger.error(f"Search failed: {str(e)}")
            return Response({
                'error': 'Search failed',
                'details': str(e)
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class ValidateUrlViewSet(viewsets.ViewSet):
    """
    ViewSet for YouTube URL validation endpoint
    Returns video information including duration and channel details
    """
    permission_classes = [AllowAny]

    @action(detail=False, methods=['post'])
    def validate(self, request):
        """
        Validate YouTube URL and return video information
        """
        try:
            youtubeUrl = request.data.get('youtubeUrl', '').strip()
            
            if not youtubeUrl:
                return Response({
                    'error': 'YouTube URL is required'
                }, status=status.HTTP_400_BAD_REQUEST)
            
            logger.info(f"Validating YouTube URL: {youtubeUrl}")
            
            # Use VideoInfoService to validate and get video info
            video_info_service = VideoInfoService()
            
            # First validate URL format
            if not video_info_service.validateYoutubeUrl(youtubeUrl):
                logger.warning(f"Invalid YouTube URL format: {youtubeUrl}")
                return Response({
                    'valid': False,
                    'error': 'Invalid YouTube URL format'
                }, status=status.HTTP_400_BAD_REQUEST)
            
            # Get video information
            try:
                video_info = video_info_service.getVideoInfo(youtubeUrl)
                
                logger.info(f"Successfully validated URL: {youtubeUrl}")
                
                response_data = {
                    'valid': True,
                    'video_info': {
                        'title': video_info.get('title', ''),
                        'duration': video_info.get('duration', 0),
                        'duration_formatted': self._format_duration(video_info.get('duration', 0)),
                        'channel': video_info.get('channel', ''),
                        'channel_id': video_info.get('channel_id', ''),
                        'thumbnail': video_info.get('thumbnail', ''),
                        'description': video_info.get('description', '')[:200] + '...' if video_info.get('description', '') else '',
                        'upload_date': video_info.get('upload_date', ''),
                        'view_count': video_info.get('view_count', 0),
                        'video_id': video_info_service.extractVideoId(youtubeUrl)
                    }
                }
                
                return Response(response_data, status=status.HTTP_200_OK)
                
            except Exception as video_error:
                error_message = str(video_error)
                logger.error(f"Failed to get video info for {youtubeUrl}: {error_message}")
                
                # Determine specific error type
                if 'private' in error_message.lower():
                    error_type = 'Video is private'
                elif 'unavailable' in error_message.lower():
                    error_type = 'Video is unavailable'
                elif 'age' in error_message.lower():
                    error_type = 'Video is age-restricted'
                elif 'copyright' in error_message.lower():
                    error_type = 'Video has copyright restrictions'
                elif 'geographic' in error_message.lower() or 'region' in error_message.lower():
                    error_type = 'Video is not available in your region'
                else:
                    error_type = 'Video is not accessible'
                
                return Response({
                    'valid': False,
                    'error': error_type,
                    'details': error_message
                }, status=status.HTTP_400_BAD_REQUEST)
                
        except Exception as e:
            logger.error(f"URL validation failed: {str(e)}")
            return Response({
                'valid': False,
                'error': 'URL validation failed',
                'details': str(e)
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    def _format_duration(self, seconds):
        """
        Format duration in seconds to HH:MM:SS or MM:SS format
        """
        if not seconds:
            return "00:00"
        
        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        secs = seconds % 60
        
        if hours > 0:
            return f"{hours:02d}:{minutes:02d}:{secs:02d}"
        else:
            return f"{minutes:02d}:{secs:02d}"

    @action(detail=False, methods=['get'])
    def info(self, request):
        """
        Get video information by URL query parameter
        Alternative endpoint for GET requests
        """
        try:
            youtubeUrl = request.query_params.get('url', '').strip()
            
            if not youtubeUrl:
                return Response({
                    'error': 'URL parameter is required'
                }, status=status.HTTP_400_BAD_REQUEST)
            
            # Reuse the validation logic
            mock_request = type('MockRequest', (), {
                'data': {'youtubeUrl': youtubeUrl}
            })()
            
            return self.validate(mock_request)
            
        except Exception as e:
            logger.error(f"Info endpoint failed: {str(e)}")
            return Response({
                'error': 'Failed to get video info',
                'details': str(e)
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

class DownloadClipViewSet(viewsets.ViewSet):
    """
    ViewSet for secure file download with proper headers
    Handles clip file serving with validation and error handling
    """
    permission_classes = [AllowAny]

    def retrieve(self, request, pk=None):
        """
        Download clip file by clip request ID
        """
        try:
            logger.info(f"Download request for clip {pk}")
            
            # Get the clip request
            try:
                clipRequest = ClipRequest.objects.get(id=pk)
            except ClipRequest.DoesNotExist:
                logger.warning(f"Clip request not found: {pk}")
                return Response({
                    'error': 'Clip request not found'
                }, status=status.HTTP_404_NOT_FOUND)
            
            # Check if clip is completed
            if clipRequest.status != 'completed':
                logger.warning(f"Clip {pk} is not ready for download. Status: {clipRequest.status}")
                return Response({
                    'error': f'Clip is not ready for download. Current status: {clipRequest.status}',
                    'status': clipRequest.status
                }, status=status.HTTP_400_BAD_REQUEST)
            
            # Check if file path exists
            if not clipRequest.file_path:
                logger.error(f"No file path found for completed clip {pk}")
                return Response({
                    'error': 'File path not found for this clip'
                }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
            
            # Construct full file path
            full_file_path = os.path.join(settings.MEDIA_ROOT, clipRequest.file_path)
            
            # Validate file existence
            if not os.path.exists(full_file_path):
                logger.error(f"File not found on disk: {full_file_path}")
                return Response({
                    'error': 'Clip file not found on server'
                }, status=status.HTTP_404_NOT_FOUND)
            
            # Validate file size
            try:
                file_size = os.path.getsize(full_file_path)
                if file_size == 0:
                    logger.error(f"Empty file found: {full_file_path}")
                    return Response({
                        'error': 'Clip file is empty'
                    }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
            except OSError as e:
                logger.error(f"Error accessing file {full_file_path}: {str(e)}")
                return Response({
                    'error': 'Error accessing clip file'
                }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
            
            # Generate appropriate filename
            filename = self._generate_download_filename(clipRequest)
            
            try:
                # Create file response with proper headers
                response = FileResponse(
                    open(full_file_path, 'rb'),
                    content_type='video/mp4',
                    as_attachment=True,
                    filename=filename
                )
                
                # Add additional security headers
                response['Content-Length'] = file_size
                response['Cache-Control'] = 'no-cache, no-store, must-revalidate'
                response['Pragma'] = 'no-cache'
                response['Expires'] = '0'
                
                # Add CORS headers if needed
                response['Access-Control-Allow-Origin'] = '*'
                response['Access-Control-Expose-Headers'] = 'Content-Disposition'
                
                logger.info(f"Successfully serving file {filename} for clip {pk}")
                
                # TODO: Uncomment when ready for analytics
                # Record download in analytics if available
                # try:
                #     analytics = clipRequest.analytics.first()
                #     if analytics:
                #         analytics.download_count = getattr(analytics, 'download_count', 0) + 1
                #         analytics.last_downloaded_at = timezone.now()
                #         analytics.save(update_fields=['download_count', 'last_downloaded_at'])
                # except Exception as analytics_error:
                #     logger.warning(f"Failed to record download analytics: {str(analytics_error)}")
                
                return response
                
            except IOError as e:
                logger.error(f"Error reading file {full_file_path}: {str(e)}")
                return Response({
                    'error': 'Error reading clip file'
                }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
                
        except Exception as e:
            logger.error(f"Download failed for clip {pk}: {str(e)}")
            return Response({
                'error': 'Download failed',
                'details': str(e)
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    def _generate_download_filename(self, clipRequest):
        """
        Generate appropriate filename for download
        """
        try:
            # Clean title for filename
            title = clipRequest.original_title or 'youtube_clip'
            # Remove invalid filename characters
            title = "".join(c for c in title if c.isalnum() or c in (' ', '-', '_')).rstrip()
            title = title.replace(' ', '_')
            
            # Format timestamps
            start_formatted = self._format_seconds_to_time(clipRequest.start_time)
            end_formatted = self._format_seconds_to_time(clipRequest.end_time)
            
            # Create filename with timestamp info
            filename = f"{title}_{start_formatted}-{end_formatted}.mp4"
            
            # Ensure filename isn't too long
            if len(filename) > 200:
                filename = f"clip_{clipRequest.id}_{start_formatted}-{end_formatted}.mp4"
            
            return filename
            
        except Exception as e:
            logger.warning(f"Error generating filename: {str(e)}")
            return f"clip_{clipRequest.id}.mp4"

    def _format_seconds_to_time(self, seconds):
        """
        Convert seconds to MM-SS or HH-MM-SS format for filename
        """
        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        secs = seconds % 60
        
        if hours > 0:
            return f"{hours:02d}-{minutes:02d}-{secs:02d}"
        else:
            return f"{minutes:02d}-{secs:02d}"

    @action(detail=True, methods=['get'])
    def info(self, request, pk=None):
        """
        Get download information without actually downloading the file
        """
        try:
            clipRequest = ClipRequest.objects.get(id=pk)
            
            if clipRequest.status != 'completed':
                return Response({
                    'ready': False,
                    'status': clipRequest.status,
                    'message': f'Clip is not ready for download. Current status: {clipRequest.status}'
                })
            
            if not clipRequest.file_path:
                return Response({
                    'ready': False,
                    'error': 'File path not found'
                }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
            
            full_file_path = os.path.join(settings.MEDIA_ROOT, clipRequest.file_path)
            
            if not os.path.exists(full_file_path):
                return Response({
                    'ready': False,
                    'error': 'File not found on server'
                }, status=status.HTTP_404_NOT_FOUND)
            
            file_size = os.path.getsize(full_file_path)
            filename = self._generate_download_filename(clipRequest)
            
            return Response({
                'ready': True,
                'filename': filename,
                'file_size': file_size,
                'file_size_mb': round(file_size / (1024 * 1024), 2),
                'download_url': f'/api/clips/{clipRequest.id}/download/',
                'clip_duration': clipRequest.end_time - clipRequest.start_time,
                'created_at': clipRequest.created_at,
                'processed_at': clipRequest.processed_at
            })
            
        except ClipRequest.DoesNotExist:
            return Response({
                'error': 'Clip request not found'
            }, status=status.HTTP_404_NOT_FOUND)
        except Exception as e:
            logger.error(f"Error getting download info: {str(e)}")
            return Response({
                'error': 'Failed to get download info',
                'details': str(e)
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)