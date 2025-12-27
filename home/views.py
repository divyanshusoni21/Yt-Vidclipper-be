import os

from django.http import  FileResponse
from django.db import transaction
from yt_helper.settings import logger
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import AllowAny

from .models import ClipRequest, STATUS_CHOICES, Clip,User
from .tasks import get_task_status
from .serializers import ClipRequestSerializer,UserSerializer
from .services import ClipProcessingService

from utility.functions import runSerializer
from utility.variables import defaultPassword
import django_rq
import traceback
from threading import Thread
from utility.functions import sendMail



class ClipRequestViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing clip requests with full CRUD operations
    Follows established patterns with proper error handling and logging
    """
    queryset = ClipRequest.objects.all()
    serializer_class = ClipRequestSerializer

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
            
            youtubeUrl = request.data.get('youtube_url')

            # Validate YouTube URL and get video info
            clipProcessingService = ClipProcessingService()
            isValidYoutubeUrl = clipProcessingService.validate_youtube_url(youtubeUrl)

            if not isValidYoutubeUrl:
                raise Exception(f"Invalid YouTube URL: {youtubeUrl}")
        
            # create clip request
            clipRequest, serializer = runSerializer(
                ClipRequestSerializer, 
                request.data, 
                request=request
            )
            
            try:
                
                # TODO: Queue background processing task
                # queue = django_rq.get_queue('default')
                # rqJob = queue.enqueue(clipProcessingService.process_clip_request, clipRequest)
                
                # jobId = rqJob.id
                # logger.info(f"Queued background processing for clip request {clipRequest.id}, job ID: {jobId}")
                
                # # Add job_id to response for tracking
                # clipRequest.rq_job_id = jobId
                # clipRequest.save(update_fields=['rq_job_id'])

                thread = Thread(target=clipProcessingService.process_clip_request, args=(clipRequest,))
                thread.start()
                responseData = ClipRequestSerializer(clipRequest,context={'request': request}).data
                
                return Response(responseData, status=status.HTTP_201_CREATED)
                
            except Exception as e:
                clipRequest.status = STATUS_CHOICES[2][0] # failed
                clipRequest.error_message = str(e)
                clipRequest.save(update_fields=['status', 'error_message'])
                raise Exception(e)

        except Exception as e:
            logger.error(traceback.format_exc())
            
            return Response({
                'error': 'Failed to create clip request',
                'details': str(e)
            }, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=False, methods=['get'])
    def task_status(self, request, pk=None):
        """
        Get background task status for a clip request
        """
        try:
            clipRequestId = request.query_params.get('clip_request_id')
            if not clipRequestId:
                raise Exception('clip_request_id parameter is required')
            

            clipRequest = ClipRequest.objects.get(id=clipRequestId)
            if not clipRequest:
                raise Exception(f"Clip request not found: {clipRequestId}")

            serializer = ClipRequestSerializer(clipRequest,context={'request': request})
            return Response(serializer.data)
            
        except Exception as e:
            logger.error(traceback.format_exc())
            return Response({
                'error': 'Failed to get task status',
                'details': str(e)
            }, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=False, methods=['get'])   
    def send_clip_to_email(self,request):
        """
        Send email to the user with the clip request details
        """
        try:
            email = request.GET.get('email')

            user = request.user

            clipRequestId = request.GET.get('clip_request_id')
            if not clipRequestId:
                raise Exception('clip_request_id  is required')
            
            clipRequest = ClipRequest.objects.get(id=clipRequestId)
            if not clipRequest:
                raise Exception(f"Clip request not found: {clipRequestId}")

            if not email :
                if not user.is_authenticated:
                    raise Exception('User email is required')

                email = user.email
            
            if not clipRequest.user: 
            
                if not user.is_authenticated :
                    user = User.objects.filter(email__iexact=email).first()
                    
                    if not user:
                        # Create user without password
                        user = User(
                            username=email.split('@')[0],
                            email=email,
                            is_verified=True,
                        )
                        user.set_password(defaultPassword)
                        user.save()

                clipRequest.user = user
                clipRequest.save(update_fields=['user'])
            
            # send email to the user with the clip request details
            clipRequestSerializedData = ClipRequestSerializer(clipRequest,context={'request': request}).data
            
            email_body = {
                "clip_request": clipRequestSerializedData,
                'type': 'get_clips'
            }
            
            sendMail(email_body, email, subject='Your clip request is ready')
            
            return Response({"success": "Email sent successfully"}, status=status.HTTP_200_OK)

        except Exception as e:
            logger.error(traceback.format_exc())
            return Response({
                'error': str(e)
            }, status=status.HTTP_400_BAD_REQUEST)


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
            
            # Get resolution from query params (default to highest available)
            requested_resolution = request.query_params.get('resolution', None)
            
            # Get available clips for this request
            clips = clipRequest.clips.all()
            if not clips.exists():
                logger.error(f"No clips found for completed clip request {pk}")
                return Response({
                    'error': 'No clip files found for this request'
                }, status=status.HTTP_404_NOT_FOUND)
            
            # Select clip based on resolution preference
            clip = None
            if requested_resolution:
                clip = clips.filter(resolution=requested_resolution).first()
                if not clip:
                    available_resolutions = list(clips.values_list('resolution', flat=True))
                    return Response({
                        'error': f'Requested resolution {requested_resolution} not available',
                        'available_resolutions': available_resolutions
                    }, status=status.HTTP_400_BAD_REQUEST)
            else:
                # Default to highest resolution available (order: 1080p > 720p > 480p > 360p > 240p)
                resolution_priority = ['1080p', '720p', '480p', '360p', '240p']
                for res in resolution_priority:
                    clip = clips.filter(resolution=res).first()
                    if clip:
                        break
                if not clip:
                    clip = clips.first()  # Fallback to any available clip
            
            # Get file path from Clip model
            clip_file = clip.clip
            if not clip_file:
                logger.error(f"No file associated with clip {clip.id}")
                return Response({
                    'error': 'Clip file not found'
                }, status=status.HTTP_404_NOT_FOUND)
            
            # Get full file path
            full_file_path = clip_file.path
            
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
            filename = self._generate_download_filename(clipRequest, clip)
            
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
                
                logger.info(f"Successfully serving file {filename} for clip {pk} at resolution {clip.resolution}")
                       
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
    
    def _generate_download_filename(self, clipRequest, clip):
        """
        Generate download filename based on clip request and clip resolution
        """
        video_title = 'clip'
        if clipRequest.video_info and clipRequest.video_info.video_title:
            # Sanitize video title for filename
            video_title = clipRequest.video_info.video_title
            # Remove invalid filename characters
            invalid_chars = '<>:"/\\|?*'
            for char in invalid_chars:
                video_title = video_title.replace(char, '_')
            # Limit length
            if len(video_title) > 50:
                video_title = video_title[:50]
        
        resolution = clip.resolution or 'unknown'
        start_time_str = str(clipRequest.start_time).replace(':', '-')
        end_time_str = str(clipRequest.end_time).replace(':', '-')
        
        filename = f"{video_title}_{resolution}_{start_time_str}-{end_time_str}.mp4"
        return filename
