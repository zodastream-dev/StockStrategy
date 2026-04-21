"""
邮件通知模块
支持 SMTP（本地）和 SendGrid HTTP API（Railway等云环境）
"""
import smtplib
import os
import urllib.request
import urllib.parse
import json
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.header import Header
from typing import Optional


class EmailNotifier:
    """邮件通知器"""

    def __init__(self, smtp_host: str = None, smtp_port: int = 465,
                 sender_email: str = None, sender_password: str = None,
                 sendgrid_api_key: str = None):
        self.smtp_host = smtp_host or os.environ.get('SMTP_HOST', 'smtp.qq.com')
        self.smtp_port = smtp_port
        self.sender_email = sender_email or os.environ.get('SMTP_EMAIL')
        self.sender_password = sender_password or os.environ.get('SMTP_PASSWORD')
        # SendGrid 优先（SMTP在云环境不通）
        self.sendgrid_api_key = sendgrid_api_key or os.environ.get('SENDGRID_API_KEY')

    def is_configured(self) -> bool:
        """检查是否已配置（SendGrid优先，否则SMTP）"""
        return bool(self.sendgrid_api_key) or bool(self.sender_email and self.sender_password)

    def send_email(self, to_email: str, subject: str, html_content: str,
                   sender_name: str = "A+H策略平台") -> dict:
        """
        发送邮件（SendGrid优先，云环境走HTTP API；SMTP兜底用于本地）

        Args:
            to_email: 收件人邮箱
            subject: 邮件主题
            html_content: HTML格式的邮件内容
            sender_name: 发件人昵称

        Returns:
            dict: {'success': True/False, 'message': str}
        """
        if not self.is_configured():
            return {'success': False, 'message': '邮件服务未配置，请先设置SMTP信息或SendGrid API Key'}

        # 优先使用 SendGrid HTTP API（云环境推荐）
        if self.sendgrid_api_key:
            return self._send_via_sendgrid(to_email, subject, html_content, sender_name)

        # SMTP 兜底（本地开发用）
        return self._send_via_smtp(to_email, subject, html_content, sender_name)

    def _send_via_sendgrid(self, to_email: str, subject: str, html_content: str,
                           sender_name: str = "A+H策略平台") -> dict:
        """通过 SendGrid Web API 发送邮件"""
        import ssl as ssl_module
        url = 'https://api.sendgrid.com/v3/mail/send'
        payload = {
            'personalizations': [{'to': [{'email': to_email}]}],
            'from': {'email': self.sender_email, 'name': sender_name},
            'subject': subject,
            'content': [{'type': 'text/html', 'value': html_content}]
        }
        data = json.dumps(payload).encode('utf-8')
        req = urllib.request.Request(
            url, data=data,
            headers={
                'Authorization': f'Bearer {self.sendgrid_api_key}',
                'Content-Type': 'application/json'
            },
            method='POST'
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                # SendGrid 202 = 成功
                return {'success': True, 'message': f'邮件已发送至 {to_email}（SendGrid）'}
        except urllib.error.HTTPError as e:
            body = e.read().decode('utf-8', errors='replace')
            return {'success': False, 'message': f'SendGrid错误 {e.code}: {body[:200]}'}
        except Exception as e:
            return {'success': False, 'message': f'SendGrid发送失败: {str(e)}'}

    def _send_via_smtp(self, to_email: str, subject: str, html_content: str,
                       sender_name: str = "A+H策略平台") -> dict:
        """通过 SMTP 发送邮件（本地用）"""
        import ssl as ssl_module
        try:
            msg = MIMEMultipart('alternative')
            msg['From'] = Header(f"{sender_name} <{self.sender_email}>")
            msg['To'] = Header(to_email)
            msg['Subject'] = Header(subject, 'utf-8')
            html_part = MIMEText(html_content, 'html', 'utf-8')
            msg.attach(html_part)

            timeout = 15
            if self.smtp_port == 465:
                context = ssl_module.create_default_context()
                with smtplib.SMTP_SSL(self.smtp_host, self.smtp_port,
                                      context=context, timeout=timeout) as server:
                    server.login(self.sender_email, self.sender_password)
                    server.sendmail(self.sender_email, [to_email], msg.as_string())
            else:
                with smtplib.SMTP(self.smtp_host, self.smtp_port,
                                  timeout=timeout) as server:
                    server.starttls()
                    server.login(self.sender_email, self.sender_password)
                    server.sendmail(self.sender_email, [to_email], msg.as_string())

            return {'success': True, 'message': f'邮件已发送至 {to_email}'}

        except smtplib.SMTPAuthenticationError:
            return {'success': False, 'message': '邮箱认证失败，请检查SMTP密码/授权码'}
        except smtplib.SMTPRecipientsRefused:
            return {'success': False, 'message': '收件人邮箱地址无效'}
        except smtplib.SMTPServerDisconnected:
            return {'success': False, 'message': 'SMTP连接意外断开，请检查网络或SMTP配置'}
        except smtplib.SMTPConnectError:
            return {'success': False, 'message': '无法连接到SMTP服务器，请检查主机地址和端口'}
        except TimeoutError:
            return {'success': False, 'message': 'SMTP连接超时，请检查网络连接'}
        except ssl_module.SSLError as e:
            return {'success': False, 'message': f'SSL错误: {str(e)}'}
        except smtplib.SMTPException as e:
            return {'success': False, 'message': f'SMTP错误: {str(e)}'}
        except Exception as e:
            return {'success': False, 'message': f'发送失败: {str(e)}'}

    def send_strategy_alert(self, to_email: str, strategy_name: str,
                            stock_info: dict, alert_details: str) -> dict:
        """
        发送策略预警邮件

        Args:
            to_email: 收件人
            strategy_name: 策略名称
            stock_info: 股票信息 dict
            alert_details: 预警详情
        """
        subject = f"【策略预警】{strategy_name} - {stock_info.get('name', '未知')}"

        html_content = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
            <style>
                body {{ font-family: 'Microsoft YaHei', Arial, sans-serif; line-height: 1.6; color: #333; }}
                .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
                .header {{ background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; padding: 20px; border-radius: 10px 10px 0 0; }}
                .header h1 {{ margin: 0; font-size: 20px; }}
                .content {{ background: #f8f9fa; padding: 20px; border-radius: 0 0 10px 10px; }}
                .stock-card {{ background: white; padding: 15px; border-radius: 8px; margin: 15px 0; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }}
                .stock-name {{ font-size: 18px; font-weight: bold; color: #667eea; }}
                .stock-code {{ color: #888; font-size: 14px; }}
                .price {{ font-size: 24px; font-weight: bold; color: #333; margin: 10px 0; }}
                .change {{ font-size: 14px; padding: 4px 8px; border-radius: 4px; display: inline-block; }}
                .change.up {{ background: #fee; color: #c33; }}
                .change.down {{ background: #efe; color: #3c3; }}
                .alert-box {{ background: #fff3cd; border: 1px solid #ffc107; padding: 15px; border-radius: 8px; margin: 15px 0; }}
                .alert-title {{ color: #856404; font-weight: bold; margin-bottom: 8px; }}
                .footer {{ text-align: center; color: #888; font-size: 12px; margin-top: 20px; }}
                .btn {{ display: inline-block; background: #667eea; color: white; padding: 10px 20px; text-decoration: none; border-radius: 5px; }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <h1>📈 A+H策略预警</h1>
                    <p>{strategy_name}</p>
                </div>
                <div class="content">
                    <div class="stock-card">
                        <div class="stock-name">{stock_info.get('name', '未知')}</div>
                        <div class="stock-code">股票代码: {stock_info.get('code', '-')}</div>
                        <div class="price">{'¥' if stock_info.get('currency') == 'CNY' else 'HK$'}{stock_info.get('price', '-')}</div>
                        <span class="change {'up' if stock_info.get('change_pct', 0) > 0 else 'down'}">
                            {'▲' if stock_info.get('change_pct', 0) > 0 else '▼'} {stock_info.get('change_pct', 0):.2f}%
                        </span>
                    </div>

                    <div class="alert-box">
                        <div class="alert-title">⚠️ 策略触发条件</div>
                        <p>{alert_details}</p>
                    </div>

                    <p><strong>触发时间:</strong> {stock_info.get('trigger_time', 'N/A')}</p>

                    <div class="footer">
                        <p>此邮件由 A+H策略回测平台 自动发送</p>
                        <p>请勿直接回复此邮件</p>
                    </div>
                </div>
            </div>
        </body>
        </html>
        """

        return self.send_email(to_email, subject, html_content)


# 全局邮件通知器实例
_email_notifier: Optional[EmailNotifier] = None


def get_email_notifier() -> EmailNotifier:
    """获取邮件通知器单例"""
    global _email_notifier
    if _email_notifier is None:
        _email_notifier = EmailNotifier()
    return _email_notifier


def init_email_notifier(smtp_host: str = None, smtp_port: int = 465,
                       sender_email: str = None, sender_password: str = None,
                       sendgrid_api_key: str = None) -> EmailNotifier:
    """初始化邮件通知器"""
    global _email_notifier
    _email_notifier = EmailNotifier(smtp_host, smtp_port, sender_email, sender_password,
                                    sendgrid_api_key)
    return _email_notifier
