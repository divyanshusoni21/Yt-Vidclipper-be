import os

from django.http import  FileResponse
from django.db import transaction
from yt_helper.settings import logger
from rest_framework import viewsets, status, generics
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import AllowAny

from .models import CLIP_STATUS_CHOICES, Clip, ClipRequest, SpeedEditRequest, User
from .serializers import ClipRequestSerializer, SpeedEditRequestSerializer

from utility.functions import runSerializer
from utility.variables import defaultPassword
from yt_helper.celery import app as celery_app
import traceback
from utility.functions import sendMail, format_validation_errors
from .tasks import (
    cleanup_cancelled_dir_task,
    cleanup_old_files,
    process_clip_task,
    process_speed_edit_task,
)
from .utils import update_obj_status


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

    def create(self, request, *args, **kwargs):
        """
        Create a new clip request using runSerializer with transaction management
        """
        try:
            logger.info(f"Creating new clip request with data: {request.data}")

            # Commit the row before the worker runs. Celery does not wait for this transaction.
            with transaction.atomic():
                clipRequest, serializer = runSerializer(
                    ClipRequestSerializer, 
                    request.data, 
                    request=request
                )
            
            try:
                # Dispatch background processing task via Celery
                celeryTask = process_clip_task.delay(str(clipRequest.id))

                # Save celery task ID in existing rq_job_id field without requiring database migrations
                clipRequest.rq_job_id = celeryTask.id
                clipRequest.save(update_fields=['rq_job_id'])

                responseData = ClipRequestSerializer(clipRequest, context={'request': request}).data

                return Response(responseData, status=status.HTTP_201_CREATED)

            except Exception as e:
                # Update status to failed with error message
                update_obj_status(clipRequest, status=CLIP_STATUS_CHOICES.FAILED, errorMessage=str(e))
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

            serializer = ClipRequestSerializer(clipRequest,context={'request': request, 'exclude_fields':["processing_log","rq_job_id","proxy"]})
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
            if not email :
                raise Exception('User email is required')

            clipRequestId = request.GET.get('clip_request_id')
            if not clipRequestId:
                raise Exception('clip_request_id  is required')
            
            clipRequest = ClipRequest.objects.filter(id=clipRequestId).first()
            if not clipRequest:
                raise Exception(f"Clip request not found: {clipRequestId}")

            # Only send email when clip processing has successfully completed
            if clipRequest.status != CLIP_STATUS_CHOICES.COMPLETED:
                raise Exception(f"Cannot send email: clip request is in {clipRequest.status.upper()} state")
            
            user = User.objects.filter(email__iexact=email).first()
                    
            if not user:
                # Create user with default password
                user = User(
                    username=email.split('@')[0],
                    email=email,
                    is_verified=True,
                )
                user.set_password(defaultPassword)
                user.save()

            
            if not clipRequest.user: 
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
            fileType = request.query_params.get('file_type','clip')

            
            if fileType.lower() not in ["clip","speed_edit"]:
                raise Exception(f"Invalid file type: {fileType}")
            
            fileObj = None
            fullFilePath = ""
            if fileType == "clip":
                # Get the clip object
                try:
                    fileObj = Clip.objects.get(id=pk)
                    fullFilePath = fileObj.clip.path
                except Clip.DoesNotExist:
                    raise Exception(f"Clip request not found: {pk}")
            elif fileType == "speed_edit":
                # Get the speed edit object
                try:
                    fileObj = SpeedEditRequest.objects.get(id=pk)
                    fullFilePath = fileObj.output_video.path
                except SpeedEditRequest.DoesNotExist:
                    raise Exception(f"Speed edit request not found: {pk}")

            
            # Validate file existence
            if not os.path.exists(fullFilePath):
                raise Exception(f"File not found on disk: {fullFilePath}")
                
            # Validate file size
            try:
                fileSize = os.path.getsize(fullFilePath)
                if fileSize == 0:
                    raise Exception(f"Empty file found: {fullFilePath}")
                    
            except OSError as e:
                raise Exception(f"Error accessing file {fullFilePath}: {str(e)}")
            
            # Generate appropriate filename
            filename = self._generate_download_filename(fileObj,fileType)
            
            try:
                # Create file response with proper headers
                response = FileResponse(
                    open(fullFilePath, 'rb'),
                    content_type='video/mp4',
                    as_attachment=True,
                    filename=filename
                )
                
                # Add additional security headers
                response['Content-Length'] = fileSize
                response['Cache-Control'] = 'no-cache, no-store, must-revalidate'
                response['Pragma'] = 'no-cache'
                response['Expires'] = '0'
                
                # Add CORS headers if needed
                response['Access-Control-Allow-Origin'] = '*'
                response['Access-Control-Expose-Headers'] = 'Content-Disposition'
                
                logger.info(f"SuccessfulS serving file {filename}, id : {pk} ")
                       
                return response
                
            except IOError as e:
                raise Exception(f"Error reading file {fullFilePath}: {str(e)}")

                
        except Exception as e:
            logger.error(f"Download failed for clip {pk}: {str(e)}")
            return Response({
                'error': 'Download failed',
                'details': str(e)
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    
    def _generate_download_filename(self, clip,fileType:str="clip"):
        """
        Generate download filename based on clip request and clip resolution
        """
        if fileType == "clip":
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
            resolution = clip.resolution or '720p'

            fileName = f"{video_title}_{resolution}.mp4"

        elif fileType == "speed_edit":
            speedEditRequest = clip
            video_title = 'speed_edit'
            speed_factor = speedEditRequest.speed_factor
            fileName = f"{video_title}_{speed_factor}x.mp4"

        return fileName


class SpeedEditViewSet(viewsets.ModelViewSet):
    """
    ViewSet for speed editing service
    Allows users to upload videos or select existing clips and adjust playback speed
    """
    queryset = SpeedEditRequest.objects.all()
    serializer_class = SpeedEditRequestSerializer
    permission_classes = [AllowAny] 
    
    def create(self, request, *args, **kwargs):
        """
        Create a new speed edit request
        """
        try:
            logger.info(f"Creating speed edit request with data: {request.data}")
            
            # Commit the row before the worker runs. Celery does not wait for this transaction.
            with transaction.atomic():
                speedEditRequest, serializer = runSerializer(
                    SpeedEditRequestSerializer,
                    request.data,
                    request=request
                )
            try :
                # Dispatch background speed edit processing task via Celery
                celeryTask = process_speed_edit_task.delay(str(speedEditRequest.id))
                jobId = celeryTask.id
                speedEditRequest.rq_job_id = jobId
                speedEditRequest.save(update_fields=['rq_job_id'])
                
                responseData = SpeedEditRequestSerializer(speedEditRequest, context={'request': request}).data
                
                return Response(responseData, status=status.HTTP_201_CREATED)
            except Exception as e:
                # Update status to failed with error message
                update_obj_status(speedEditRequest, status=CLIP_STATUS_CHOICES.FAILED, errorMessage=str(e))
                raise Exception(e)

        except Exception as e:
            e = format_validation_errors(e,self.get_exception_handler_context())
            logger.error(traceback.format_exc())
            return Response({
                'error': str(e)
            }, status=status.HTTP_400_BAD_REQUEST)
    
    @action(detail=False, methods=['get'])
    def status(self, request):
        """Get status of a speed edit request"""
        try:
            requestId = request.query_params.get('request_id')
            if not requestId:
                raise Exception('request_id parameter is required')
            
            speedEditRequest = SpeedEditRequest.objects.filter(id=requestId).first()
            if not speedEditRequest:
                raise Exception(f"Speed edit request not found: {requestId}")

            serializer = SpeedEditRequestSerializer(speedEditRequest, context={'request': request})
            return Response(serializer.data)
            
        except Exception as e:
            logger.error(traceback.format_exc())
            return Response({
                'error': 'Failed to get status',
                'details': str(e)
            }, status=status.HTTP_400_BAD_REQUEST)


class CancelRequestViewSet(generics.GenericAPIView):
    """
    Unified cancel API for clip requests and speed edit requests.
    POST with body: { "request_type": "clip_request" | "speed_edit", "request_id": "<uuid>" }
    """

    def post(self, request):
        """
        Cancel a clip request or speed edit request
        """
        
        try:
            requestType = request.data.get('request_type')
            requestId = request.data.get('request_id')

            if not requestType or not requestId:
                raise Exception('request_type and request_id are required')
            if requestType not in ('clip_request', 'speed_edit'):
                raise Exception('Invalid request_type')
            requestObj = None

            if requestType == 'clip_request':
                requestObj = ClipRequest.objects.filter(id=requestId).first()

            elif requestType == 'speed_edit':
                requestObj = SpeedEditRequest.objects.filter(id=requestId).first()

            if not requestObj:
                raise Exception(f"{requestType} request not found: {requestId}")

            self.cancel_request(requestObj, requestType)

            return Response({'status': 'Request cancelled successfully', 'request_type': requestType}, status=status.HTTP_200_OK)
        except Exception as e:
            logger.error(traceback.format_exc())
            return Response({
                'error': str(e),
            }, status=status.HTTP_400_BAD_REQUEST)

    def cancel_request(self, requestObj, requestType):
        jobWasRunning = False

        with transaction.atomic():
            if not (requestObj.status == CLIP_STATUS_CHOICES.PENDING or requestObj.status == CLIP_STATUS_CHOICES.PROCESSING):
                raise Exception(f'Cannot cancel, {requestType} request is in {requestObj.status.upper()} state')

            jobId = requestObj.rq_job_id
            # Commit cancelled before revoke. acks_late can redeliver the task, and it must see this status.
            update_obj_status(requestObj, status=CLIP_STATUS_CHOICES.CANCELLED)

        if jobId:
            taskResult = celery_app.AsyncResult(jobId)
            # STARTED means ffmpeg may already be running, so stop that worker child.
            if taskResult.state == 'STARTED':
                celery_app.control.revoke(jobId, terminate=True, signal='SIGTERM')
                jobWasRunning = True
            else:
                # Still queued. Revoke so a worker that reserved it will drop it.
                celery_app.control.revoke(jobId)

        logger.info(f"Cancelled {requestType} request {requestObj.id}, jobWasRunning: {jobWasRunning}")

        if jobWasRunning:
            # Wait so a stopped ffmpeg can release the folder before we delete it.
            cleanup_cancelled_dir_task.apply_async(
                args=[str(requestObj.id), requestType],
                countdown=30,
            )


class CleanupOldFilesViewSet(generics.GenericAPIView):
    """
    API endpoint to trigger cleanup of old files based on retention policy
    This can be protected or scheduled as needed
    """

    def get(self, request):
        try:
            cleanup_old_files.delay()
            return Response(status=status.HTTP_200_OK)
        except Exception as e:
            logger.error(traceback.format_exc())
            return Response({
                'error': 'Failed to trigger cleanup task',
                'details': str(e),
            }, status=status.HTTP_400_BAD_REQUEST)