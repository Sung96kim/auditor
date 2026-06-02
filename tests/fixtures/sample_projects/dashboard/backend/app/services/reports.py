"""Report generation — assembly, scoring, and rendering."""


class ReportEngine:
    def __init__(self):
        self.title = ""
        self.services = []
        self.start = None
        self.end = None
        self.theme = "light"
        self.format = "pdf"
        self.include_charts = True
        self.include_raw = False
        self.page_size = 50
        self.cursor = None
        self.totals = {}
        self.warnings = []
        self.errors = []
        self.cache = {}
        self.rendered = None
        self.signed_url = None
        self.author = "system"

    def render(self):
        return self.rendered


def render_report(title, services, start, end, fmt, include_charts, theme):
    return {
        "title": title,
        "services": services,
        "start": start,
        "end": end,
        "format": fmt,
        "charts": include_charts,
        "theme": theme,
    }


def classify(metric):
    score = 0
    if metric.cpu > 90:
        score += 1
    if metric.mem > 90:
        score += 1
    if metric.disk > 90 and metric.io_wait > 50:
        score += 1
    if metric.net > 80 or metric.latency > 100:
        score += 1
    if metric.error_rate > 0:
        score += 1
    if metric.p99 > 500:
        score += 1
    if metric.tier == "prod" and metric.region == "us-east":
        score += 2
    elif metric.tier == "staging":
        score += 1
    if score > 5:
        return "critical"
    elif score > 3:
        return "warn"
    elif score > 1:
        return "notice"
    return "ok"


def summarize(rows):
    out = {}
    for row in rows:
        out[row["key"]] = row["value"]
    return out
