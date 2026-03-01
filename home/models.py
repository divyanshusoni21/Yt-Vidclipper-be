from django.db import models
from utility.mixins import UUIDMixin
from django.db import models
from django.contrib.auth.models import AbstractUser
from utility.mixins import UUIDMixin
from rest_framework_simplejwt.tokens import RefreshToken

# Create your models here.

def clip_file_path(instance,fileName):
    """Generate file path for clip uploads using clip_request's video_info channel_name"""
    channel_name = 'unknown'
    if instance.clip_request and instance.clip_request.video_info:
        channel_name = instance.clip_request.video_info.channel_name or 'unknown'
    return f'clips/{channel_name}/{fileName}'

def speed_edit_upload_path(instance, fileName):
    """Generate file path for speed edit uploads"""
    return f'speed_edit_uploads/{instance.id}/{fileName}'


def speed_edit_output_path(instance, fileName):
    """Generate file path for speed edited output videos"""
    return f'speed_edited_videos/{instance.id}/{fileName}'




STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('completed', 'Completed'),
        ('failed', 'Failed'),
        ('cancelled', 'Cancelled'),
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

    user = models.ForeignKey(User, on_delete=models.SET_NULL,null=True,blank=True)
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
    proxy = models.CharField(max_length=255, null=True, blank=True)
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



class SpeedEditRequest(UUIDMixin):
    """Model for handling speed editing requests from user uploads or existing clips"""
    
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    
    # Source video can be either uploaded or from existing clip
    uploaded_video = models.FileField(upload_to=speed_edit_upload_path, null=True, blank=True)
    source_clip = models.ForeignKey(Clip, on_delete=models.SET_NULL, null=True, blank=True, 
                                   help_text="Reference to existing clip from portal")
    
    # Speed configuration
    speed_factor = models.FloatField(help_text="Speed multiplier (e.g., 0.5, 1.5, 2.0)")
    
    # Processing status
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    
    # Output
    output_video = models.FileField(upload_to=speed_edit_output_path, null=True, blank=True)
    output_size = models.FloatField(null=True, blank=True, help_text="Output file size in MB")
    output_duration = models.IntegerField(null=True, blank=True, help_text="Duration in seconds")
    
    # Metadata
    original_duration = models.IntegerField(null=True, blank=True)
    original_size = models.FloatField(null=True, blank=True)
    processing_time = models.FloatField(null=True, blank=True, help_text="Time taken in seconds")
    error_message = models.TextField(null=True, blank=True)

    # Background job ID
    rq_job_id = models.CharField(max_length=255, null=True, blank=True)
    
    class Meta:
        db_table = 'speed_edit_request'
        ordering = ['-created_at']
        
    def __str__(self):
        source = "uploaded" if self.uploaded_video else f"clip_{self.source_clip.id}"
        return f"SpeedEdit {self.id} - {source} @ {self.speed_factor}x"
    
    def get_source_path(self):
        """Returns the file path of the source video"""
        if self.uploaded_video:
            return self.uploaded_video.path
        elif self.source_clip:
            return self.source_clip.clip.path
        return None
