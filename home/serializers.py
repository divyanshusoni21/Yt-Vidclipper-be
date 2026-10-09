from rest_framework import serializers

from utility.mixins import FieldMixin
from .models import ClipRequest,  VideoDetail, Clip,User, SpeedEditRequest
from utility.functions import time_to_seconds
from utility.variables import maxSpeedEditBytes,minClipDurationInSec,maxClipDurationInSec


class UserSerializer(FieldMixin,serializers.ModelSerializer):

    class Meta :
        model = User
        exclude = ["password","is_staff","is_superuser","groups"]



class VideoDetailSerializer(serializers.ModelSerializer):
    """Serializer for VideoDetail model"""
    
    class Meta:
        model = VideoDetail
        fields = '__all__'
        read_only_fields = ('id', 'created_at', 'updated_at')


class ClipSerializer(serializers.ModelSerializer):
    """Serializer for Clip model"""
    
    class Meta:
        model = Clip
        fields = '__all__'
        read_only_fields = ('id', 'created_at', 'updated_at')

    

class ClipRequestSerializer(FieldMixin, serializers.ModelSerializer):
    """
    Serializer for ClipRequest model with field exclusion capabilities
    and custom validation for timestamp ranges and YouTube URLs
    """

    clips = serializers.SerializerMethodField()

    
    class Meta:
        model = ClipRequest
        fields = '__all__'
        read_only_fields = ('id', 'created_at', 'updated_at', 'processed_at', 
                           'error_message', 'processing_log', 'video_info', 
                           'clip_duration', 'total_time_taken', 'rq_job_id')    

    def validate_youtube_url(self, value):
        """
        Validate whether the provided URL is a valid YouTube URL.
        """
        # Imported inside method to avoid circular import issues with services module
        from .services import ClipProcessingService

        clipProcessingService = ClipProcessingService()
        isValidYoutubeUrl = clipProcessingService.validate_youtube_url(value)

        if not isValidYoutubeUrl:
            raise serializers.ValidationError(f"Invalid YouTube URL: {value}")

        return value

    def validate(self, data):
        """
        Cross-field validation for timestamp ranges
        """
        startTime = data.get('start_time')
        endTime = data.get('end_time')

        startTime = time_to_seconds(str(startTime))
        endTime = time_to_seconds(str(endTime))
        
        
        if startTime is not None and endTime is not None:
            if endTime <= startTime:
                raise serializers.ValidationError({
                    'endTime': 'End time must be after start time.'
                })
            
            # Check if clip duration is reasonable (not too short or too long)
            clip_duration = endTime - startTime
          
            if clip_duration < minClipDurationInSec:
                raise serializers.ValidationError({
                    'endTime': f'Clip duration must be at least {minClipDurationInSec} second.'
                })
            
            # Maximum clip duration of 5 minutes (300 seconds)
            if clip_duration > maxClipDurationInSec:
                raise serializers.ValidationError({
                    'endTime': f'Clip duration cannot exceed {maxClipDurationInSec//60} minutes.'
                })
        
        return data
    
    def get_clips(self,obj):
        clips = obj.clips.all()
        return ClipSerializer(clips, many=True,context=self.context).data

    def to_representation(self, instance):
        """
        Customize the serialized representation
        """
        data = super().to_representation(instance)
        
        # # Add computed fields for API responses
        startTime = data.get('start_time')
        endTime = data.get('end_time')

        startTime = time_to_seconds(str(startTime))
        endTime = time_to_seconds(str(endTime))

        clipDuration = endTime - startTime
        data['clip_duration'] = clipDuration

        if "video_info" in data and data["video_info"] is not None:
            data["video_info"] = VideoDetailSerializer(instance.video_info).data

        return data


class SpeedEditRequestSerializer(FieldMixin, serializers.ModelSerializer):
    """Serializer for SpeedEditRequest with validation"""
    
    class Meta:
        model = SpeedEditRequest
        fields = '__all__'

    def validate_speed_factor(self, value):
        """
        Validate that the speed factor is positive and within the allowed range (0.25x to 4.0x).
        """
        if value <= 0:
            raise serializers.ValidationError("Speed factor must be positive")
        if value < 0.25 or value > 4.0:
            raise serializers.ValidationError("Speed factor must be between 0.25x and 4.0x")
        return value

    def validate_uploaded_video(self, value):
        """
        Validate that uploaded video does not exceed the allowed size limit (50 MB).
        """
        if value and value.size > maxSpeedEditBytes:
            raise serializers.ValidationError("Input video must be 50 MB or smaller.")
        return value

    def validate(self, data):
        """
        Cross-field validation to ensure either uploaded_video or source_clip is provided, but not both.
        """
        uploadedVideo = data.get('uploaded_video', getattr(self.instance, 'uploaded_video', None))
        sourceClip = data.get('source_clip', getattr(self.instance, 'source_clip', None))

        if not uploadedVideo and not sourceClip:
            raise serializers.ValidationError(
                "Either 'uploaded_video' or 'source_clip' must be provided"
            )

        if uploadedVideo and sourceClip:
            raise serializers.ValidationError(
                "Provide either 'uploaded_video' or 'source_clip', not both"
            )

        return data

    def create(self, validated_data):
        """
        Create SpeedEditRequest instance and compute its original size in MB
        """
        # When request data is sent as multipart/form-data, DRF defaults missing boolean fields to False.
        # Explicitly setting is_active to True ensures requests remain active upon creation.
        validated_data['is_active'] = True

        speedEditRequest = super().create(validated_data)

        # Compute original video size in bytes from uploaded file or existing clip
        if speedEditRequest.uploaded_video:
            originalSizeBytes = speedEditRequest.uploaded_video.size
        elif speedEditRequest.source_clip and speedEditRequest.source_clip.clip:
            originalSizeBytes = speedEditRequest.source_clip.clip.size
        else:
            originalSizeBytes = 0

        speedEditRequest.original_size = round(originalSizeBytes / (1024 * 1024), 2)
        speedEditRequest.save(update_fields=['original_size'])

        return speedEditRequest
    
    def to_representation(self, instance):
        """
        Customize the serialized representation
        """
        data = super().to_representation(instance)
        if "source_clip" in data and data["source_clip"] is not None:
            data["source_clip"] = ClipSerializer(instance.source_clip,context=self.context).data
        return data

    
