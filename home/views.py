import os

from django.http import  FileResponse
from django.db import transaction
from yt_helper.settings import logger
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import AllowAny

from .models import ClipRequest, STATUS_CHOICES,User,Clip, SpeedEditRequest
from .serializers import ClipRequestSerializer, SpeedEditRequestSerializer
from .services import ClipProcessingService, SpeedEditService

from utility.functions import runSerializer
from utility.variables import defaultPassword
import django_rq
import traceback
from threading import Thread
from utility.functions import sendMail,format_validation_errors



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
                
                # # TODO: Queue background processing task
                queue = django_rq.get_queue('default')
                rqJob = queue.enqueue(clipProcessingService.process_clip_request, clipRequest)
                
                jobId = rqJob.id
                # logger.info(f"Queued background processing for clip request {clipRequest.id}, job ID: {jobId}")
                
                # # # Add job_id to response for tracking
                clipRequest.rq_job_id = jobId
                clipRequest.save(update_fields=['rq_job_id'])

                # thread = Thread(target=clipProcessingService.process_clip_request, args=(clipRequest,))
                # thread.start()

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
    permission_classes = [AllowAny]  # Adjust based on your auth requirements
    
    @transaction.atomic
    def create(self, request, *args, **kwargs):
        """
        Create a new speed edit request
        """
        try:
            logger.info(f"Creating speed edit request with data: {request.data}")

            uploadedVideo = request.data.get('uploaded_video')
            sourceClip = request.data.get('source_clip')

            if not uploadedVideo and not sourceClip:
                raise Exception(
                    "Either 'uploaded_video' or 'source_clip' must be provided"
                )
        
            if uploadedVideo and sourceClip:
                raise Exception(
                    "Provide either 'uploaded_video' or 'source_clip', not both"
                )
            
            # Validate speed factor
            speed_factor = float(request.data.get('speed_factor'))
            if speed_factor is not None:
                if speed_factor <= 0:
                    raise Exception('Speed factor must be positive')
                if speed_factor < 0.25 or speed_factor > 4.0:
                    raise Exception('Speed factor must be between 0.25x and 4.0x')
            
            # If source_clip_id provided, verify it exists and map to source_clip
            if sourceClip:
                sourceClip = Clip.objects.filter(id=sourceClip).first()
                if not sourceClip:
                    raise Exception(f'Clip not found : {sourceClip}')



            requestData = request.data.copy()
            requestData["is_active"] = True
            # Create the speed edit request
            speedEditRequest, serializer = runSerializer(
                SpeedEditRequestSerializer,
                requestData,
                request=request
            )
            
            # Set user if authenticated
            if request.user.is_authenticated:
                speedEditRequest.user = request.user
                speedEditRequest.save(update_fields=['user'])
            
            # Process in background thread
            speedEditService = SpeedEditService()

            queue = django_rq.get_queue('default')
            rqJob = queue.enqueue(speedEditService.process_speed_edit_request, speedEditRequest)
            jobId = rqJob.id
            speedEditRequest.rq_job_id = jobId
            speedEditRequest.save(update_fields=['rq_job_id'])

            # thread = Thread(target=speedEditService.process_speed_edit_request, args=(speedEditRequest,))
            # thread.start()
            
            responseData = SpeedEditRequestSerializer(speedEditRequest, context={'request': request}).data
            
            return Response(responseData, status=status.HTTP_201_CREATED)
            
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
