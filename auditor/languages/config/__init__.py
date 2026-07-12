"""The ``config`` language: secret detection for config/data files (.env, .yaml, .json, …).

These files carry no code to parse, but they are where committed credentials most often leak.
Detectors read the raw text: a shared secret sweep runs over every line, and a dedicated rule
flags a dotenv that is tracked by the repo.
"""
