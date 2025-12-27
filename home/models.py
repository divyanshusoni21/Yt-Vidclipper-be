from django.db import models
from utility.mixins import UUIDMixin

# Create your models here.

def clip_file_path(instance,fileName):
    """Generate file path for clip uploads using clip_request's video_info channel_name"""
    channel_name = 'unknown'
    if instance.clip_request and instance.clip_request.video_info:
        channel_name = instance.clip_request.video_info.channel_name or 'unknown'
    return f'clips/{channel_name}/{fileName}'

from django.db import models
from django.contrib.auth.models import AbstractUser
from utility.mixins import UUIDMixin
from rest_framework_simplejwt.tokens import RefreshToken

STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('completed', 'Completed'),
        ('failed', 'Failed'),
    ]

CLIP_RESOLUTION = (
    ('1080p', '1080p'), 
    ('720p', '720p'),
    ('480p', '480p'),
    ('360p', '360p'),
    ('240p', '240p'),
)

class User(AbstractUser, UUIDMixin):
    """Custom User model extending Django's AbstractUser"""
    email = models.EmailField(unique=True)
    username = models.CharField(max_length=150,blank=True)
    is_verified = models.BooleanField(default=False)
    phone = models.BigIntegerField(null=True,blank=True)
    
    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = ['username']
    
    def __str__(self):
        return self.email 
    

    def tokens(self):
        refresh = RefreshToken.for_user(self)

        accessToken = refresh.access_token
       
        return {
            'refresh': str(refresh),
            'access': str(accessToken)
        }
    
class VideoDetail(UUIDMixin):
    video_id = models.CharField(max_length=100,blank=True)
    video_duration = models.IntegerField(null=True, blank=True)
    channel_name = models.CharField(max_length=200, null=True, blank=True)
    channel_id = models.CharField(max_length=100, null=True, blank=True)
    video_title = models.CharField(max_length=200, null=True, blank=True)
    
    def __str__(self):
        return f"{self.video_title} - {self.video_id}"



class ClipRequest(UUIDMixin):

    user = models.ForeignKey(User, on_delete=models.CASCADE,null=True,blank=True)
    youtube_url = models.URLField()
    start_time = models.TimeField()  
    end_time = models.TimeField()    
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    processed_at = models.DateTimeField(null=True, blank=True)
    video_info = models.ForeignKey(VideoDetail, on_delete=models.SET_NULL, null=True, blank=True)
    clip_duration = models.IntegerField(null=True, blank=True,help_text="clip duration in seconds")  # clip duration in seconds
    error_message = models.TextField(null=True, blank=True)
    total_time_taken = models.IntegerField(null=True, blank=True)
    processing_log = models.JSONField(default=dict, blank=True)  # Stores detailed processing steps and errors
    rq_job_id = models.CharField(max_length=255, null=True, blank=True)
    
    class Meta:
        db_table = 'clip_request'
        ordering = ['-created_at']
        
    def __str__(self):
        return f"ClipRequest {self.id} - {self.youtube_url} ({self.start_time}-{self.end_time}s)"
    
class Clip(UUIDMixin):
    clip_request = models.ForeignKey(ClipRequest, on_delete=models.CASCADE, related_name='clips')
    clip = models.FileField(upload_to=clip_file_path)
    size = models.FloatField(null=True, blank=True,help_text="clip size in mb")
    duration = models.IntegerField(null=True, blank=True,help_text="clip duration in seconds")
    resolution = models.CharField(max_length=10,choices=CLIP_RESOLUTION, blank=True)

    def __str__(self):
        return f"{self.clip_request.id} - {self.resolution}"


class ClipAnalytics(UUIDMixin):
    # Request Analytics
    clip_request = models.ForeignKey(ClipRequest, on_delete=models.CASCADE, related_name='analytics')
    
    # Video Analytics
    video_id = models.CharField(max_length=50)  # YouTube video ID
    video_duration = models.IntegerField()  # in seconds
    clip_duration = models.IntegerField()  # in seconds
    clip_percentage = models.FloatField()  # percentage of original video clipped
    
    # Processing Analytics
    processing_method = models.CharField(max_length=30)
    processing_time = models.FloatField(null=True, blank=True)  # in seconds
    download_size = models.BigIntegerField(null=True, blank=True)  # bytes downloaded
    final_file_size = models.BigIntegerField(null=True, blank=True)  # final clip size
    
    # Channel Analytics
    channel_name = models.CharField(max_length=200, null=True, blank=True)
    channel_id = models.CharField(max_length=100, null=True, blank=True)
    
    # User Behavior Analytics
    user_ip = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(null=True, blank=True)
    referrer = models.URLField(null=True, blank=True)
    
    # System Performance
    server_load = models.FloatField(null=True, blank=True)
    memory_usage = models.FloatField(null=True, blank=True)  # in MB
    
    # Success/Failure Analytics
    success = models.BooleanField(default=False)
    error_type = models.CharField(max_length=100, null=True, blank=True)
    retry_count = models.IntegerField(default=0)
    
    class Meta:
        db_table = 'clip_analytics'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['video_id']),
            models.Index(fields=['channel_id']),
            models.Index(fields=['processing_method']),
            models.Index(fields=['success']),
            models.Index(fields=['created_at']),
        ]
        
    def __str__(self):
        return f"ClipAnalytics {self.id} - {self.video_id} ({self.processing_method})"
