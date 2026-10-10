"""Local control panel for the OLX scraper (docs/CONTROL_PANEL.md).

A localhost-only web app over the scraper's own data: what has been collected
per OLX location node, how fresh it is, and (later phases) a map and scrape
actions. It only ever reads the scraper's files — olx.sqlite,
locations.sqlite, run manifests, run_config.yaml — and keeps its own state in
scraped_data/panel.sqlite.

    uv run python -m olx_panel            # http://127.0.0.1:8765
"""
