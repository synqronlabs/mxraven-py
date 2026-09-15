# Feedback

`mxraven.feedback` submits recipient feedback to the mxRaven feedback service.
Learning uses the same submission API key as SMTP submission, over HTTP Basic
auth.

```python
from mxraven.feedback import Client, Disposition

feedback = Client(
    base_url="https://feedback.mxraven.com",
    username="mxr_tx_ab12cd34ef56",
    secret=api_key_secret,
)

feedback.learn_spam(raw_message_bytes)
feedback.learn_ham(raw_message_bytes)
feedback.learn(Disposition.SPAM, raw_message_bytes)
```

The raw bytes must be exactly the message mxRaven processed; the service matches
them against stored evidence by SHA-256.

## One-click unsubscribe

`unsubscribe` performs the RFC 8058 one-click unsubscribe that recipient mail
clients call against the `List-Unsubscribe` URL.
