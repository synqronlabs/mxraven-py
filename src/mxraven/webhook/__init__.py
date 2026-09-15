"""mxRaven webhook verification and decoding.

Verifies the ``X-MxRaven-*`` HMAC-SHA256 signature over the canonical request
string and decodes ``DELIVER_WEBHOOK`` (inbound email) and ``NOTIFY_WEBHOOK``
(SMTP or object-storage delivery status) payloads.
"""
