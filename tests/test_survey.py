from contextlib import closing
import http.client
import json
from pathlib import Path
import re
import sqlite3
import tempfile
import threading
import unittest
from urllib.parse import urlencode

from clinic_ai.app import ClinicServer


class SurveyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'surveys.sqlite3'
        self.start()

    def start(self):
        self.server = ClinicServer(0, self.path)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def tearDown(self):
        self.stop()
        self.temp.cleanup()

    def request(self, method='GET', path='/survey', values=None, headers=None):
        conn = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        hdr = {'Content-Type': 'application/x-www-form-urlencoded'}
        hdr.update(headers or {})
        conn.request(method, path, urlencode(values) if values is not None else None, hdr)
        response = conn.getresponse()
        result = response.status, dict(response.getheaders()), response.read().decode()
        conn.close()
        return result

    def values(self):
        status, _, body = self.request()
        self.assertEqual(status, 200)
        token = re.search(r'name="token" value="([^"]+)"', body)[1]
        return dict(token=token, name='로컬테스트', phone='010-0000-0000',
                    birth='000101', identity='3000000', complaint='테스트',
                    onset='', details='합성 응답', medication='없음', privacy='on')

    def rows(self):
        with closing(sqlite3.connect(self.path)) as db:
            return db.execute('SELECT id, created_at, properties FROM surveys').fetchall()

    def test_form_matches_source(self):
        status, headers, body = self.request()
        expected = [('name', True), ('phone', True), ('birth', True), ('identity', True),
                    ('complaint', True), ('onset', False), ('details', True),
                    ('medication', False), ('privacy', False), ('kakao', False)]
        seen = []
        for match in re.finditer(r'<(?:input|textarea)\b[^>]*>', body):
            field = re.search(r'name="([^"]+)"', match[0])
            if field and field[1] != 'token':
                seen.append((field[1], ' required' in match[0]))
        self.assertEqual(seen, expected)
        self.assertEqual(headers['Cache-Control'], 'no-store')
        self.assertIn('frame-ancestors', headers['Content-Security-Policy'])

    def test_save_duplicate_and_restart(self):
        values = self.values()
        for _ in range(2):
            status, headers, _ = self.request('POST', '/submit', values)
            self.assertEqual(status, 303)
            self.assertEqual(headers['Location'], '/complete')
        rows = self.rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(json.loads(rows[0][2])['설문 상태'], '세션연결완료')
        self.assertFalse(json.loads(rows[0][2])['카카오 안내 동의'])
        self.assertEqual(self.server.store.counts()['sessions'], 1)
        self.stop()
        self.start()
        self.assertEqual(self.rows(), rows)
        self.assertEqual(self.request(path='/health')[0], 200)
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_missing_required_and_optional(self):
        values = self.values()
        for field in ('name', 'phone', 'birth', 'identity', 'complaint', 'details', 'medication'):
            with self.subTest(field=field):
                invalid = {k: v for k, v in values.items() if k != field}
                self.assertEqual(self.request('POST', '/submit', invalid)[0], 422)
                self.assertEqual(self.rows(), [])
        self.assertEqual(self.request('POST', '/submit', values)[0], 303)

    def test_changed_duplicate_is_rejected(self):
        values = self.values()
        self.assertEqual(self.request('POST', '/submit', values)[0], 303)
        rows = self.rows()
        values['name'] = '다른 응답'
        self.assertEqual(self.request('POST', '/submit', values)[0], 409)
        self.assertEqual(self.rows(), rows)

    def test_cross_origin_and_forged_token(self):
        values = self.values()
        for headers in ({'Origin': 'https://example.com'}, {'Host': 'example.com'}, {'Sec-Fetch-Site': 'cross-site'}):
            self.assertEqual(self.request('POST', '/submit', values, headers)[0], 403)
        values['token'] = '0' * 32 + '.' + '0' * 64
        self.assertEqual(self.request('POST', '/submit', values)[0], 403)
        self.assertEqual(self.rows(), [])

    def test_invalid_digits_and_escaped_error(self):
        values = self.values()
        values['birth'] = 'abcdef'
        values['name'] = '<script>alert(1)</script>'
        status, _, body = self.request('POST', '/submit', values)
        self.assertEqual(status, 422)
        self.assertNotIn('<script>', body)
        self.assertIn('&lt;script&gt;', body)
        self.assertNotIn(values['identity'], body)
        self.assertEqual(self.rows(), [])

    def test_concurrent_duplicate(self):
        from concurrent.futures import ThreadPoolExecutor
        values = self.values()
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: self.request('POST', '/submit', values)[0], range(4)))
        self.assertEqual(results, [303] * 4)
        self.assertEqual(len(self.rows()), 1)


if __name__ == '__main__':
    unittest.main()
