import os

from django.http import  FileResponse
from django.db import transaction
from yt_helper.settings import logger
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import AllowAny

from .models import ClipRequest, STATUS_CHOICES,User,Clip
from .tasks import get_task_status
from .serializers import ClipRequestSerializer
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
        Download clip file
        """
        try:
            logger.info(f"Download request for clip {pk}")
            
            # Get the clip object
            try:
                clip = Clip.objects.get(id=pk)
            except Clip.DoesNotExist:
                raise Exception(f"Clip request not found: {pk}")
            

            # Get full file path
            fullFilePath = clip.clip.path
            
            # Validate file existence
            if not os.path.exists(fullFilePath):
                raise Exception(f"File not found on disk: {fullFilePath}")
                
            # Validate file size
            try:
                file_size = os.path.getsize(fullFilePath)
                if file_size == 0:
                    raise Exception(f"Empty file found: {fullFilePath}")
                    
            except OSError as e:
                raise Exception(f"Error accessing file {fullFilePath}: {str(e)}")
            
            # Generate appropriate filename
            filename = self._generate_download_filename(clip)
            
            try:
                # Create file response with proper headers
                response = FileResponse(
                    open(fullFilePath, 'rb'),
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
                raise Exception(f"Error reading file {fullFilePath}: {str(e)}")

                
        except Exception as e:
            logger.error(f"Download failed for clip {pk}: {str(e)}")
            return Response({
                'error': 'Download failed',
                'details': str(e)
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    
    def _generate_download_filename(self, clip):
        """
        Generate download filename based on clip request and clip resolution
        """
        clipRequest = clip.clip_request
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
