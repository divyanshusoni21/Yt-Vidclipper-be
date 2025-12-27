import re
from rest_framework import serializers
from django.core.exceptions import ValidationError
from utility.mixins import FieldMixin
from .models import ClipRequest, ClipAnalytics, VideoDetail, Clip
from utility.functions import time_to_seconds




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
          
            if clip_duration < 10:
                raise serializers.ValidationError({
                    'endTime': 'Clip duration must be at least 10 second.'
                })
            
            # Maximum clip duration of 30 minutes (1800 seconds)
            if clip_duration > 1800:
                raise serializers.ValidationError({
                    'endTime': 'Clip duration cannot exceed 30 minutes.'
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
    
