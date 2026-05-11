"""Mock telemetry provider. Reads canned data from the fixture dict."""


def get_exception_trend(fixture, window):
    trend = fixture.get("telemetry", {}).get("trend")
    if not trend:
        return {}
    return {**trend, "window": window}


def get_stack_sample(fixture, exception_type):
    stack = fixture.get("telemetry", {}).get("stack")
    if not stack:
        return {}
    if stack.get("exception_type") and stack["exception_type"] != exception_type:
        return {}
    return stack
