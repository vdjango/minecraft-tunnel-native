"""SMTP delivery and one-use email confirmation codes."""
import asyncio
from email.message import EmailMessage
from email.utils import formataddr
import hashlib
import html
import json
import logging
import re
import secrets
import smtplib
import ssl
import time
from pathlib import Path

from .portal import atomic_json


class MailDeliveryError(ValueError):
    """Delivery failure with a safe message suitable for the registration page."""


class VerificationMail:
    def __init__(self, path, node_name):
        self.path, self.node_name = Path(path), str(node_name)[:80]
        self.settings = {'host': 'smtp.163.com', 'port': 465, 'username': '', 'password': '',
                         'from_name': 'Buggcraft 节点', 'required': True}
        if self.path.exists():
            value = json.loads(self.path.read_text(encoding='utf-8'))
            if not isinstance(value, dict):
                raise ValueError('Invalid SMTP settings')
            self.settings.update(value)
        self.pending, self.recent = {}, {}
        self.lock = asyncio.Lock()
        self.delivery_slots = asyncio.Semaphore(2)

    @property
    def enabled(self):
        return bool(self.settings.get('username') and self.settings.get('password'))

    @property
    def required(self):
        return bool(self.settings.get('required', True))

    def public_settings(self):
        return {'enabled': self.enabled, 'required': self.required,
                'host': self.settings['host'], 'port': self.settings['port'],
                'username': self.settings['username'], 'from_name': self.settings['from_name']}

    def configure(self, data):
        if not isinstance(data, dict):
            raise ValueError('Invalid SMTP settings')
        host = data.get('host', 'smtp.163.com')
        port = data.get('port', 465)
        username = data.get('username', '')
        password = data.get('password', '')
        from_name = data.get('from_name', 'Buggcraft 节点')
        required = data.get('required', True)
        if (not isinstance(host, str) or not re.fullmatch(r'[A-Za-z0-9.-]{1,253}', host) or
                type(port) is not int or port not in (465, 587) or
                not isinstance(username, str) or len(username) > 254 or
                not isinstance(password, str) or len(password) > 512 or
                not isinstance(from_name, str) or not 1 <= len(from_name) <= 40 or
                type(required) is not bool):
            raise ValueError('请检查邮箱服务器、端口和发件人设置。')
        if not password:
            password = self.settings.get('password', '')
        self.settings = {'host': host, 'port': port, 'username': username, 'password': password,
                         'from_name': from_name, 'required': required}
        atomic_json(self.path, self.settings)

    async def send(self, recipient, code, testing=False):
        if (not isinstance(recipient, str) or len(recipient) > 254 or
                not re.fullmatch(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,63}", recipient)):
            raise ValueError('请输入有效的邮箱地址。')
        if not self.enabled:
            raise ValueError('节点尚未配置发信邮箱，请联系管理员。')
        message = EmailMessage()
        message['Subject'] = 'Buggcraft 发信测试' if testing else 'Buggcraft 邮箱验证码'
        message['From'] = formataddr((self.settings['from_name'], self.settings['username']))
        message['To'] = recipient
        message.set_content(f"你好！\n\n你的 Buggcraft 注册验证码是：{code}\n\n验证码有效期 10 分钟。请勿转发给他人。\n如果这不是你发起的操作，可以忽略此邮件。\n\n{self.node_name}")
        safe_name, safe_code = html.escape(self.node_name), html.escape(code)
        message.add_alternative(f'''<!doctype html><html><body style="margin:0;background:#f2f0f7;font-family:Arial,'Microsoft YaHei',sans-serif;color:#241e30"><div style="max-width:560px;margin:36px auto;padding:0 18px"><div style="padding:22px 28px;background:#241e30;border-radius:16px 16px 0 0;color:#fff"><div style="font-size:12px;letter-spacing:2px;color:#c7aaff">BUGGCRAFT · NODE</div><h1 style="margin:12px 0 0;font-size:23px">确认你的邮箱</h1></div><div style="padding:28px;background:#fff;border:1px solid #e6e0ed;border-top:0;border-radius:0 0 16px 16px"><p style="font-size:15px">你好，</p><p style="color:#625b6e;line-height:1.7">请输入下面的验证码，完成 <b>{safe_name}</b> 节点账号注册。</p><div style="margin:24px 0;padding:18px;text-align:center;background:#f5f0ff;border:1px solid #e7dafa;border-radius:12px;color:#5838a0;font-size:32px;font-weight:700;letter-spacing:9px">{safe_code}</div><p style="color:#756e80;font-size:13px;line-height:1.7">验证码 10 分钟内有效。请勿告诉他人。若你没有发起注册，请忽略此邮件。</p><hr style="border:0;border-top:1px solid #eee8f3;margin:26px 0"><small style="color:#91899b">此邮件由 {safe_name} 自动发送。</small></div></div></body></html>''', subtype='html')
        if testing:
            message.clear_content()
            message.set_content('发信测试成功。此邮件不包含有效验证码。\n\n' + self.node_name)
            message.add_alternative(f'<html><body style="background:#f2f0f7;font-family:Arial,sans-serif;padding:32px"><div style="max-width:520px;margin:auto;background:#fff;border-radius:16px;padding:32px"><p style="color:#7956b8">BUGGCRAFT · NODE</p><h1>邮件服务已连接</h1><p>{safe_name} 已成功发送测试邮件。</p><p style="color:#756e80">这是一封发信测试邮件，没有有效验证码，无需操作。</p></div></body></html>', subtype='html')
        if self.delivery_slots.locked():
            raise MailDeliveryError('发信服务繁忙，请稍后重试。')
        try:
            async with self.delivery_slots, asyncio.timeout(30):
                await asyncio.to_thread(self._send, message)
        except smtplib.SMTPAuthenticationError as exc:
            logging.getLogger(__name__).warning('SMTP authentication rejected (code %s)', exc.smtp_code)
            raise MailDeliveryError('节点发信账号验证失败，请联系管理员检查发件邮箱和 SMTP 授权码。') from None
        except TimeoutError:
            logging.getLogger(__name__).warning('SMTP delivery timed out')
            raise MailDeliveryError('邮件服务连接超时，请稍后重新发送。') from None
        except (smtplib.SMTPException, OSError) as exc:
            logging.getLogger(__name__).warning('SMTP delivery failed (%s, code %s)',
                                                type(exc).__name__, getattr(exc, 'smtp_code', None))
            raise MailDeliveryError('邮件暂未发出，请稍后重试；持续失败请联系节点管理员。') from None

    def _send(self, message):
        if self.settings['port'] == 465:
            with smtplib.SMTP_SSL(self.settings['host'], 465, timeout=12, context=ssl.create_default_context()) as client:
                client.login(self.settings['username'], self.settings['password'])
                client.send_message(message)
        else:
            with smtplib.SMTP(self.settings['host'], 587, timeout=12) as client:
                client.ehlo()
                client.starttls(context=ssl.create_default_context())
                client.ehlo()
                client.login(self.settings['username'], self.settings['password'])
                client.send_message(message)

    async def issue(self, email, username, nonce):
        if not isinstance(email, str) or not re.fullmatch(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,63}", email.strip()):
            raise ValueError('请输入有效的邮箱地址。')
        if not isinstance(username, str) or not re.fullmatch('[A-Za-z0-9_-]{3,32}', username):
            raise ValueError('用户名只能使用 3–32 位英文字母、数字、下划线或短横线。')
        key = email.strip().casefold()
        async with self.lock:
            now = time.monotonic()
            old = self.recent.get(key)
            if old is not None and now - old < 60:
                raise ValueError('验证码已发送或正在发送，请等待 60 秒后再试。')
            self.recent[key] = now
            self.recent = {k: t for k, t in self.recent.items() if now - t < 3600}
            if len(self.recent) > 256:
                raise ValueError('请求过多，请稍后再试。')
        code = f'{secrets.randbelow(1000000):06d}'
        ticket = secrets.token_urlsafe(32)
        try:
            await self.send(email.strip(), code)
        except Exception:
            # A failed delivery must not pretend that a usable code was sent.
            async with self.lock:
                self.recent.pop(key, None)
            raise
        self.pending[hashlib.sha256(ticket.encode()).hexdigest()] = {
            'code': hashlib.sha256(code.encode()).hexdigest(), 'email': key,
            'username': username.casefold(), 'nonce': nonce, 'expires': time.monotonic() + 600}
        self.pending = {k: v for k, v in self.pending.items() if v['expires'] > time.monotonic()}
        return ticket

    def verify(self, ticket, code, username, nonce):
        key = hashlib.sha256(ticket.encode()).hexdigest() if isinstance(ticket, str) else ''
        value = self.pending.pop(key, None)
        if (not value or value['expires'] <= time.monotonic() or not isinstance(code, str) or
                not secrets.compare_digest(value['code'], hashlib.sha256(code.encode()).hexdigest()) or
                not isinstance(username, str) or value['username'] != username.casefold() or value['nonce'] != nonce):
            raise ValueError('邮箱验证码无效或已过期，请重新发送。')
        return value['email']
