"""Panel jobs: a persistent queue of browser jobs (scrape, warm, probe,
browser cleanup) run one at a time as wrapped subprocesses.

  model.py     job rows in panel.sqlite
  kinds.py     what each kind runs (command, files it needs)
  wrap.py      the per-job wrapper process: forwards cancel signals, records
               the exit status in exit.json
  progress.py  what a job's output.log says (current URL/page, blocks)
  runner.py    the Supervisor thread: start, watch, cancel, finalize
"""
