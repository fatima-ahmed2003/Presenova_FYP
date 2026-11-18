"""GAP_DETECTOR: refresh failure and circuit-breaker regression tests."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # GAP_DETECTOR

from services.rewrite.executor import RewriteExecutor
from services.rewrite_engine import _build_improvements_summary


class FakeProvider:
    def __init__(self):
        self.calls = 0

    def generate_structured(self, **_kwargs):
        self.calls += 1
        raise TimeoutError("simulated AI timeout")


class RewriteFailureTests(unittest.TestCase):
    # GAP_DETECTOR
    def test_refresh_failure_falls_back_and_stops_after_three_calls(self):
        provider = FakeProvider()
        slides = [
            {
                'slide_number': 1,
                'title': 'Overview',
                'textboxes': [{
                    'shape_index': 0,
                    'paragraphs': [
                        'This is generic filler content that needs a specific and useful rewrite.'
                    ],
                }],
            },
            {
                'slide_number': 2,
                'title': 'Details',
                'textboxes': [{
                    'shape_index': 0,
                    'paragraphs': [
                        'This is another generic filler paragraph that should be refreshed for readers.'
                    ],
                }],
            },
        ]

        executor = RewriteExecutor(provider=provider, refresh=True)
        rewritten, metrics = executor.execute_rewrite(slides)

        self.assertEqual(metrics['ai_slides_succeeded'], 0)
        self.assertEqual(metrics['slide_status'], {1: 'ai_failed', 2: 'ai_failed'})
        self.assertEqual(provider.calls, 3)
        self.assertIn('simulated AI timeout', metrics['last_ai_error'])
        self.assertEqual(len(rewritten), len(slides))

    # GAP_DETECTOR
    def test_ai_failure_message_precedes_no_change_message(self):
        improvements = _build_improvements_summary(
            quality_scores={},
            change_stats={'total': 4, 'changed': 0},
            rewrite_metrics={'ai_failed': True},
        )

        self.assertEqual(
            improvements,
            [
                'The AI could not rewrite this deck (quota or timeout). '
                'The file contains only minor rule-based edits. Please retry in a few minutes.'
            ],
        )


if __name__ == '__main__':
    unittest.main()
