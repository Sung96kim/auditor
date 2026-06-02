"""Alert routing, formatting, and enrichment."""


class AlertBuilder:
    def __init__(self, service, severity):
        self.service = service
        self.severity = severity

    def build(self):
        return {"service": self.service, "severity": self.severity}


class AlertFormatters:
    @staticmethod
    def to_slack(alert):
        return _render_slack(alert)

    @staticmethod
    def to_email(alert):
        return _render_email(alert)


def _render_slack(alert):
    return f"[slack] {alert}"


def _render_email(alert):
    return f"[email] {alert}"


def route_alert(alert):
    if alert.kind == "cpu":
        return handle_cpu(alert)
    elif alert.kind == "mem":
        return handle_mem(alert)
    elif alert.kind == "disk":
        return handle_disk(alert)
    elif alert.kind == "net":
        return handle_net(alert)
    elif alert.kind == "gpu":
        return handle_gpu(alert)
    else:
        return handle_default(alert)


def notify(alert):
    return AlertFormatters.to_slack(alert)


def copy_alert(target, src):
    target.id = src.id
    target.service = src.service
    target.severity = src.severity
    target.message = src.message
    target.created_at = src.created_at


def make_threshold_handler(threshold):
    def handler(value):
        return _evaluate(value, threshold)

    return handler


def enrich(payload):
    payload["enriched"] = True
    payload["source"] = "pulse"
    return payload


def build_signals(ctx):
    return ctx


def build_contexts(ctx):
    return build_signals(ctx)


def build_rows(ctx):
    return build_contexts(ctx)
