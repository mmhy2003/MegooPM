"""Dashboard read models: counting, aggregating and grouping for the UI.

Nothing here mutates domain state. The one write is `metrics.record_sample`,
which stores the scrape a node took of its own nginx.
"""
