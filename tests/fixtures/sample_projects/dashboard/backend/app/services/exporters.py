"""Export snapshots and configs to various formats and destinations."""

import pickle
import subprocess
import tempfile
from xml.etree import ElementTree

import paramiko
import requests
import yaml


def load_snapshot(blob):
    return pickle.loads(blob)


def load_config_yaml(raw: str):
    return yaml.load(raw)


def parse_report(xml_text: str):
    return ElementTree.fromstring(xml_text)


def run_export(fmt: str):
    subprocess.run(f"pulse-export --format {fmt}", shell=True)


def compute_expr(expr: str):
    return eval(expr)


def fetch_remote(url: str):
    return requests.get(url, verify=False)


def scratch_path():
    return tempfile.mktemp(suffix=".csv")


def connect_ssh(host: str):
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(host)
    return client
