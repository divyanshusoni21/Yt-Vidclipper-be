from django.contrib import admin
from .models import ClipRequest,Clip,VideoDetail


# Register your models here.

@admin.register(Clip)
class ClipAdmin(admin.ModelAdmin):
    list_display = ["id","clip_request","clip","resolution","created_at"]
    list_filter = ["resolution"]
    search_fields = ['id', 'clip_request__youtube_url', 'clip_request__video_info__video_title', 'clip_request__video_info__channel_name','clip_request__id']
    ordering = ['-created_at']
    list_per_page = 10
    list_max_show_all = 100


@admin.register(VideoDetail)
class VideoDetailAdmin(admin.ModelAdmin):
    list_display = ['id', 'video_title', 'channel_name', 'created_at']
    search_fields = ['id', 'video_title', 'channel_name',"video_id"]
    ordering = ['-created_at']
    list_per_page = 10
    list_max_show_all = 100


class ClipInline(admin.TabularInline):
    model = Clip
    extra = 0
    readonly_fields = ['id', 'clip', 'size',  'resolution', 'created_at']
    fields = ['id', 'clip', 'resolution', 'size',  'created_at']
    can_delete = False
    show_change_link = True


@admin.register(ClipRequest)
class ClipRequestAdmin(admin.ModelAdmin):
    list_display = ['id', 'youtube_url', 'start_time', 'end_time', 'status', 'created_at']
    list_filter = ['status', ]
    search_fields = ['id', 'youtube_url', 'video_info__video_title', 'video_info__channel_name']
    ordering = ['-created_at']
    list_per_page = 10
    list_max_show_all = 100
    list_display_links = ['id', 'youtube_url']
    list_select_related = ['video_info']
    inlines = [ClipInline]
 