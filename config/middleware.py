import uuid

import sentry_sdk

from .request_context import request_id_context


class RequestIdMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.request_id = str(uuid.uuid4())
        context_token = request_id_context.set(request.request_id)
        sentry_sdk.set_tag("request_id", request.request_id)
        try:
            response = self.get_response(request)
            response["X-Request-ID"] = request.request_id
            return response
        finally:
            request_id_context.reset(context_token)
