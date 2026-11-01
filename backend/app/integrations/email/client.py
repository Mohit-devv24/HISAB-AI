import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

class EmailClient:
    def __init__(self, smtp_host="smtp.gmail.com", smtp_port=587):
        self.smtp_host = smtp_host
        self.smtp_port = smtp_port
