from rest_framework.pagination import PageNumberPagination


class StandardResultsSetPagination(PageNumberPagination):
    page_size = 10
    page_size_query_param = "page_size"

    def get_page_size(self, request):
        page_size = request.query_params.get(self.page_size_query_param)
        if page_size == 'all':
            return 99999999  # Large page size to include all results in a single page
        try:
            return int(page_size)
        except (TypeError, ValueError):
            return self.page_size