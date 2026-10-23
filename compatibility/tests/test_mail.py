"""Exercise generated MIME, encrypted SMTP negotiation and one-use tickets."""
import asyncio
import json
from pathlib import Path
import smtplib
import tempfile
import unittest
from unittest.mock import patch

from tunnel.mail import VerificationMail


class MailTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.mail = VerificationMail(Path(self.temp.name) / 'smtp.json', '<Node & friends>')
        self.mail.configure(dict(username='sender@example.com', password='smtp-secret-123'))

    async def test_ticket_is_bound_to_registration_and_consumed_on_failure(self):
        messages = []
        with patch.object(self.mail, '_send', side_effect=messages.append), patch('tunnel.mail.secrets.randbelow', return_value=12345):
            ticket = await self.mail.issue('User@example.com', 'Alice', 'nonce-1')
        message = messages[0]
        self.assertEqual(message['To'], 'User@example.com')
        self.assertIn('012345', message.get_body(preferencelist=('plain',)).get_content())
        self.assertIn('&lt;Node &amp; friends&gt;', message.get_body(preferencelist=('html',)).get_content())
        self.assertNotIn('smtp-secret-123', message.as_string())
        with self.assertRaises(ValueError):
            self.mail.verify(ticket, '012345', 'Alice', 'other-nonce')
        with self.assertRaises(ValueError):
            self.mail.verify(ticket, '012345', 'Alice', 'nonce-1')

    def test_unconfigured_smtp_does_not_disable_required_verification(self):
        mail = VerificationMail(Path(self.temp.name) / 'new-smtp.json', 'test')
        self.assertFalse(mail.enabled)
        self.assertTrue(mail.required)
        self.assertTrue(mail.public_settings()['required'])

    async def test_success_replay_expiry_and_rate_limit(self):
        with patch.object(self.mail, '_send'), patch('tunnel.mail.secrets.randbelow', return_value=654321):
            ticket = await self.mail.issue('user@example.com', 'Alice', 'nonce')
            with self.assertRaises(ValueError):
                await self.mail.issue('USER@example.com', 'Alice', 'nonce')
            self.assertEqual(self.mail.verify(ticket, '654321', 'ALICE', 'nonce'), 'user@example.com')
            with self.assertRaises(ValueError):
                self.mail.verify(ticket, '654321', 'Alice', 'nonce')
            expired = await self.mail.issue('other@example.com', 'Alice', 'nonce')
        for entry in self.mail.pending.values():
            entry['expires'] = 0
        with self.assertRaises(ValueError):
            self.mail.verify(expired, '654321', 'Alice', 'nonce')

    async def test_first_request_after_boot_is_allowed_but_real_resends_wait_a_minute(self):
        messages = []
        with patch.object(self.mail, '_send', side_effect=messages.append), \
                patch('tunnel.mail.time') as clock, \
                patch('tunnel.mail.secrets.randbelow', return_value=123456):
            clock.monotonic.return_value = 0
            ticket = await self.mail.issue('new@example.com', 'Alice', 'nonce')
            self.assertEqual(self.mail.verify(ticket, '123456', 'Alice', 'nonce'), 'new@example.com')
            clock.monotonic.return_value = 59
            with self.assertRaisesRegex(ValueError, '验证码已发送'):
                await self.mail.issue('NEW@example.com', 'Alice', 'nonce')
            self.assertEqual(len(messages), 1)
            clock.monotonic.return_value = 60
            renewed = await self.mail.issue('new@example.com', 'Alice', 'new-nonce')
            self.assertEqual(len(messages), 2)
            self.assertEqual(self.mail.verify(renewed, '123456', 'Alice', 'new-nonce'), 'new@example.com')

    async def test_test_message_has_no_usable_code_and_delivery_failure_has_no_secret(self):
        messages = []
        with patch.object(self.mail, '_send', side_effect=messages.append):
            await self.mail.send('user@example.com', '123456', testing=True)
        self.assertEqual(messages[0]['Subject'], 'Buggcraft 发信测试')
        self.assertNotIn('123456', ''.join(p.get_content() for p in messages[0].iter_parts()))
        with patch.object(self.mail, '_send', side_effect=smtplib.SMTPException('smtp-secret-123')):
            with self.assertRaises(ValueError) as caught:
                await self.mail.send('user@example.com', '123456')
        self.assertNotIn('smtp-secret-123', str(caught.exception))

    async def test_failed_delivery_does_not_create_ticket_or_block_successful_retry(self):
        from tunnel.mail import MailDeliveryError
        with patch.object(self.mail, '_send', side_effect=smtplib.SMTPAuthenticationError(535, b'smtp-secret-123')):
            with self.assertLogs('tunnel.mail', level='WARNING') as logs:
                with self.assertRaisesRegex(MailDeliveryError, '发信账号验证失败') as caught:
                    await self.mail.issue('retry@example.com', 'Alice', 'nonce')
        self.assertFalse(self.mail.pending)
        self.assertNotIn('retry@example.com', self.mail.recent)
        self.assertNotIn('smtp-secret-123', str(caught.exception))
        self.assertNotIn('smtp-secret-123', ''.join(logs.output))
        with patch.object(self.mail, '_send'), patch('tunnel.mail.secrets.randbelow', return_value=123456):
            ticket = await self.mail.issue('retry@example.com', 'Alice', 'nonce')
        self.assertEqual(self.mail.verify(ticket, '123456', 'Alice', 'nonce'), 'retry@example.com')

    async def test_parallel_same_recipient_does_not_send_twice(self):
        from unittest.mock import AsyncMock
        started, finish = asyncio.Event(), asyncio.Event()
        async def sending(*args):
            started.set()
            await finish.wait()
        with patch.object(self.mail, 'send', new=AsyncMock(side_effect=sending)) as deliver:
            first = asyncio.create_task(self.mail.issue('parallel@example.com', 'Alice', 'nonce'))
            try:
                await asyncio.wait_for(started.wait(), 3)
                with self.assertRaisesRegex(ValueError, '验证码已发送'):
                    await self.mail.issue('parallel@example.com', 'Alice', 'nonce')
                self.assertEqual(deliver.await_count, 1)
            finally:
                finish.set()
                await first

    def test_secret_is_persisted_but_excluded_from_settings_response(self):
        self.assertNotIn('password', self.mail.public_settings())
        self.assertNotIn('smtp-secret-123', json.dumps(self.mail.public_settings()))
        self.mail.configure(dict(username='sender@example.com', password=''))
        self.assertEqual(VerificationMail(self.mail.path, 'test').settings['password'], 'smtp-secret-123')

    def test_starttls_happens_before_credentials(self):
        self.mail.settings['port'] = 587
        with patch('tunnel.mail.smtplib.SMTP') as smtp:
            client = smtp.return_value.__enter__.return_value
            self.mail._send('message')
        self.assertEqual([call[0] for call in client.method_calls],
                         ['ehlo', 'starttls', 'ehlo', 'login', 'send_message'])
