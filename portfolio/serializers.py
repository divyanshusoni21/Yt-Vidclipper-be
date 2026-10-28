from rest_framework import serializers

from utility.mixins import FieldMixin

from .models import Blog, BlogMedia


class BlogMediaSerializer(serializers.ModelSerializer):
    """
    Serializer for individual media items (images or videos) attached to a blog.
    Returns the media file URL, slug identifier, type, caption, and display order.
    """

    class Meta:
        model = BlogMedia
        fields = (
            "id",
            "slug",
            "media_file",
            "media_type",
            "caption",
            "order",
            "created_at",
        )
        read_only_fields = ("id", "created_at")


class BlogSerializer(FieldMixin, serializers.ModelSerializer):
    """
    Serializer for Blog posts.
    Inherits FieldMixin to allow dynamic field exclusion.
    Replaces media slug placeholders (e.g., 'image:arch-diagram' or 'video:demo-clip')
    with full absolute URLs directly inside 'content' at serialization time.
    """

    class Meta:
        model = Blog
        fields = (
            "id",
            "title",
            "slug",
            "summary",
            "content",
            "banner_image",
            "video_url",
            "author",
            "tags",
            "reading_time_minutes",
            "is_published",
            "published_at",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("id", "created_at", "updated_at")

    def to_representation(self, instance):
        """
        Replace media slug placeholders in the markdown content with their
        full absolute URLs. This provides the frontend with valid, ready-to-render links.
        """
        data = super().to_representation(instance)
        request = self.context.get("request")
        content = data.get("content")

        if content and instance.media_files.all():
            for mediaItem in instance.media_files.all():
                if not mediaItem.slug or not mediaItem.media_file:
                    continue

                mediaUrl = (
                    request.build_absolute_uri(mediaItem.media_file.url)
                    if request
                    else mediaItem.media_file.url
                )

                imagePlaceholder = f"image:{mediaItem.slug}"
                videoPlaceholder = f"video:{mediaItem.slug}"

                if imagePlaceholder in content:
                    content = content.replace(imagePlaceholder, mediaUrl)
                if videoPlaceholder in content:
                    content = content.replace(videoPlaceholder, mediaUrl)

            data["content"] = content

        return data
