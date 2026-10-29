import json
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
import app


class TestApi(unittest.TestCase):
    def test_validation_sql_injection_blocked(self):
        with self.assertRaises(app.ApiError):
            app.validate_params({'exporter': "ARG'; DROP TABLE x;--"})

    def test_validation_bad_month_blocked(self):
        with self.assertRaises(app.ApiError):
            app.validate_params({'start': '2026-13'})

    def test_validation_scope_required(self):
        with self.assertRaises(app.ApiError):
            app.validate_params({'exporter': 'ARG'}, require_scope=True)

    def test_heatmap_sql_and_status(self):
        data = [{'partner': 'BRA', 'period': '2023-Q1', 'total_count': 3,
                 'matched_count': 2, 'anomaly_count': 1}]
        with patch.object(app, 'sql_rows', return_value=data) as sql:
            result = app.get_heatmap({'exporter': 'ARG', 'product': '1001',
                                      'frequency': 'quarterly'})
        self.assertEqual(result['cells'][0]['status'], 'flagged')
        self.assertIn('COUNT_IF(b.is_anomaly)', sql.call_args.args[0])

    def test_health_does_not_call_athena(self):
        event = {'rawPath': '/api/v1/health',
                 'requestContext': {'http': {'method': 'GET'}}}
        with patch.object(app, 'sql_rows') as sql:
            response = app.lambda_handler(event, None)
        self.assertEqual(response['statusCode'], 200)
        self.assertEqual(json.loads(response['body'])['status'], 'ok')
        sql.assert_not_called()

    def test_anomalies_limit_validation(self):
        with self.assertRaises(app.ApiError):
            app.get_anomalies({'limit': '5000'})

    def test_anomaly_id_validation(self):
        with self.assertRaises(app.ApiError):
            app.get_anomaly('abc')


if __name__ == '__main__':
    unittest.main()
