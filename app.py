import os
import time
import smtplib
import sqlite3
import logging
import csv
from datetime import datetime
from flask import Flask, render_template, jsonify, send_file
from pythonping import ping
from threading import Thread
from pysnmp.hlapi import *
from twilio.rest import Client

# Configuration
HOSTS = ["8.8.8.8", "1.1.1.1"]
DB_FILE = "network_logs.db"
ALERT_EMAIL = "your_email@example.com"
EMAIL_USER = "your_smtp_email@example.com"
EMAIL_PASS = "your_email_password"
TWILIO_SID = "your_account_sid"
TWILIO_AUTH_TOKEN = "your_auth_token"
TWILIO_PHONE = "+1234567890"
ALERT_PHONE = "+0987654321"

# Flask App
app = Flask(__name__)

# Logging setup
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
log = logging.getLogger()

# Track downed hosts to prevent spam alerts
down_hosts = set()

def init_db():
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL;")
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS network_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT,
                host TEXT,
                status TEXT,
                latency REAL
            )
        """)
        conn.commit()

def log_status(host, status, latency=None):
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cursor.execute("INSERT INTO network_logs (timestamp, host, status, latency) VALUES (?, ?, ?, ?)", 
                       (timestamp, host, "UP" if status else "DOWN", latency))
        conn.commit()

def send_alert(host):
    try:
        with smtplib.SMTP("smtp.gmail.com", 587) as server:
            server.starttls()
            server.login(EMAIL_USER, EMAIL_PASS)
            message = f"Subject: Network Alert!\n\nHost {host} is DOWN!"
            server.sendmail(EMAIL_USER, ALERT_EMAIL, message)
        log.info(f"Alert email sent for {host}")
    except Exception as e:
        log.error(f"Failed to send alert email: {e}")

def send_sms_alert(host):
    try:
        client = Client(TWILIO_SID, TWILIO_AUTH_TOKEN)
        message = client.messages.create(
            body=f"⚠️ Network Alert! {host} is DOWN!",
            from_=TWILIO_PHONE,
            to=ALERT_PHONE
        )
        log.info(f"SMS alert sent: {message.sid}")
    except Exception as e:
        log.error(f"Failed to send SMS alert: {e}")

def get_snmp(host, oid, community="public"):
    iterator = getCmd(
        SnmpEngine(),
        CommunityData(community, mpModel=0),
        UdpTransportTarget((host, 161), timeout=2.0),
        ContextData(),
        ObjectType(ObjectIdentity(oid))
    )
    errorIndication, errorStatus, errorIndex, varBinds = next(iterator)
    if errorIndication or errorStatus:
        return "N/A"
    return str(varBinds[0][1])

def monitor_network():
    global down_hosts
    while True:
        for host in HOSTS:
            response = ping(host, count=3, timeout=1)
            if response.success():
                avg_latency = round(response.rtt_avg_ms, 2)
                log_status(host, True, avg_latency)
                if host in down_hosts:
                    log.info(f"{host} is back UP!")
                    down_hosts.remove(host)
            else:
                log_status(host, False)
                if host not in down_hosts:
                    down_hosts.add(host)
                    send_alert(host)
                    send_sms_alert(host)
        time.sleep(30)

Thread(target=monitor_network, daemon=True).start()

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/logs")
def get_logs():
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT timestamp, host, status, latency FROM network_logs ORDER BY id DESC LIMIT 20")
        logs = cursor.fetchall()
    return jsonify(logs)

@app.route("/snmp")
def get_snmp_data():
    snmp_data = {
        "cpu_usage": get_snmp("192.168.1.1", "1.3.6.1.4.1.2021.11.9.0"),
        "memory_usage": get_snmp("192.168.1.1", "1.3.6.1.4.1.2021.4.6.0"),
        "bandwidth": get_snmp("192.168.1.1", "1.3.6.1.2.1.2.2.1.10.2")
    }
    return jsonify(snmp_data)

@app.route("/export")
def export_logs():
    csv_file = "network_logs.csv"
    with sqlite3.connect(DB_FILE) as conn, open(csv_file, "w", newline="") as file:
        cursor = conn.cursor()
        cursor.execute("SELECT timestamp, host, status, latency FROM network_logs ORDER BY id DESC")
        logs = cursor.fetchall()

        writer = csv.writer(file)
        writer.writerow(["Timestamp", "Host", "Status", "Latency (ms)"])
        writer.writerows(logs)

    return send_file(csv_file, as_attachment=True)

if __name__ == "__main__":
    init_db()
    app.run(debug=True, host="0.0.0.0", port=5000)
