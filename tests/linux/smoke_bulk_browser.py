"""Offline real-browser regression checks; no credentials or network required."""
from pathlib import Path
import sys
import threading
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'linux'))
import bulk
from playwright.sync_api import sync_playwright


class BrowserTests(unittest.TestCase):
    def setUp(self):
        self.pw = sync_playwright().start()
        self.browser = self.pw.chromium.launch(headless=True)
        self.page = self.browser.new_page()
        self.page.route('**/*', lambda route: route.fulfill(body='<html></html>', content_type='text/html'))
        self.page.goto('https://auth.openai.com/log-in')
        self.callback = {}
        self.page.expose_function('finished', lambda: self.callback.update(code='ok'))

    def tearDown(self):
        self.browser.close()
        self.pw.stop()

    def drive(self):
        return bulk.drive_login(self.page, ('person@example.com', 'password', 'JBSWY3DPEHPK3PXP'),
                                self.callback, threading.Event(), lambda status: None, timeout=1)

    def test_submit_button_without_enter_handler(self):
        self.page.set_content('<input type="email"><button type="submit" onclick="finished()">Continue</button>')
        self.assertEqual(self.drive(), 'ok')
        self.assertEqual(self.page.locator('input').input_value(), 'person@example.com')

    def test_numeric_totp_with_two_factor_label(self):
        self.page.set_content('<p>Two-factor verification</p><input inputmode="numeric" id="verification-code">'
                              '<button type="submit" onclick="finished()">Verify</button>')
        self.assertEqual(self.drive(), 'ok')
        self.assertRegex(self.page.locator('input').input_value(), r'^\d{6}$')

    def test_email_code_is_not_filled_with_totp(self):
        self.page.set_content('<p>Check your email for a verification code</p><input name="code">')
        with self.assertRaisesRegex(ValueError, 'timed out'):
            self.drive()
        self.assertEqual(self.page.locator('input').input_value(), '')

    def test_phone_enrollment_is_not_filled_with_totp(self):
        self.page.set_content('<p>Set up two-factor authentication; enter your phone number</p>'
                              '<input type="tel" inputmode="numeric"><button type="submit" onclick="finished()">Continue</button>')
        with self.assertRaisesRegex(ValueError, 'timed out'):
            self.drive()
        self.assertEqual(self.page.locator('input').input_value(), '')

    def test_error_page_is_not_misreported_as_verification(self):
        self.page.set_content('<h1>Oops, an error occurred!</h1><p>Unexpected token &lt;, is not valid JSON</p>')
        with self.assertRaisesRegex(ValueError, 'temporary.*login'):
            self.drive()

    def test_untrusted_origin_never_receives_credentials(self):
        self.page.goto('https://example.com/log-in')
        self.page.set_content('<input type="email"><input type="password">')
        with self.assertRaisesRegex(ValueError, 'timed out'):
            self.drive()
        self.assertEqual(self.page.locator('input').evaluate_all('(xs) => xs.map(x => x.value)'), ['', ''])


if __name__ == '__main__':
    unittest.main()
