"""Bespoke SMTP submission protocol implementation.

Mirrors the behaviour of ``github.com/synqronlabs/raven/client``: a direct
socket and TLS client with EHLO, mandatory STARTTLS and SMTP AUTH, MAIL
FROM/RCPT TO/DATA transactions, dot-stuffing, per-recipient results, enhanced
status code parsing, and a bounded connection pool.

The standard library :mod:`smtplib` is deliberately not used: the submission
service requires precise control over the greeting, STARTTLS ordering, pipelined
envelope commands, reply parsing, and connection reuse.
"""
