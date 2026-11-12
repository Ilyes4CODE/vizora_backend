from django.test.runner import DiscoverRunner

from .base import RecordingClient
from .routes import endpoints


class ApiCoverageRunner(DiscoverRunner):
    def build_suite(self, test_labels=None, **kwargs):
        self.full_run = not test_labels
        return super().build_suite(test_labels, **kwargs)

    def suite_result(self, suite, result, **kwargs):
        failures = super().suite_result(suite, result, **kwargs)
        if not self.full_run:
            return failures
        expected = {(method, route.replace("^", "")) for method, route, _ in endpoints()}
        missing = sorted(expected - {(method, route.replace("^", "")) for method, route in RecordingClient.hits})
        print(f"\nAPI coverage: {len(expected) - len(missing)}/{len(expected)} endpoints returned a success response")
        for method, route in missing:
            print(f"NOT COVERED {method} {route}")
        return failures + len(missing)
