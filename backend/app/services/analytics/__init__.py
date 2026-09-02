"""Visitor analytics: ingestion, aggregation and retention.

The nginx log phase writes counters into Redis; the pieces here drain them into
one row per visitor per day, resolve a country once per distinct address, and
enforce the retention window those rows are subject to.
"""
