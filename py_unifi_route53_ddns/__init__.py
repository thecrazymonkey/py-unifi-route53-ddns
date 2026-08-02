import argparse
import functools
import getpass
import ipaddress
import logging
import os
import shutil

import boto3
import urllib3

systemd_service = """[Unit]
Description="py-unifi-route53-ddns"

[Service]
ExecStart={entrypoint} run
"""

systemd_timer = """[Unit]
Description="Run py-unifi-route53-ddns.service every 5 minutes"

[Timer]
OnCalendar=*:0/5
Persistent=true
Unit=py-unifi-route53-ddns.service

[Install]
WantedBy=timers.target
"""

systemd_override = """[Service]
Environment="AWS_ACCESS_KEY_ID={akid}"
Environment="AWS_SECRET_ACCESS_KEY={access_key}"
Environment="ROUTE53_HOSTED_ZONE_DNS_NAME={zone_name}"
Environment="ROUTE53_MY_DNS_NAME={host_name}"
Environment="ROUTE53_TTL=300"
"""

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
http = urllib3.PoolManager()
parser = argparse.ArgumentParser(prog=__name__)
parser.add_argument("action", choices=["install", "run"])


@functools.lru_cache(maxsize=None)
def route53_client():
    return boto3.client("route53")


def get_my_ip():
    res = http.request("GET", "https://cloudflare.com/cdn-cgi/trace", timeout=urllib3.Timeout(total=10))
    if res.status != 200:
        logger.error("IP lookup returned unexpected status %s", res.status)
        return None
    for line in res.data.decode().splitlines():
        data = line.split("=")
        if data[0] == "ip":
            try:
                return str(ipaddress.IPv4Address(data[1]))
            except ValueError:
                logger.error("IP lookup returned %s, expected an IPv4 address", data[1])
                return None


def get_route53_ip(hosted_zone_dns_name, my_dns_name):
    res = route53_client().list_hosted_zones_by_name(DNSName=hosted_zone_dns_name)
    if not res.get("HostedZones") or res["HostedZones"][0]["Name"] != f"{hosted_zone_dns_name}.":
        logger.error("Could not find hosted zone for %s", hosted_zone_dns_name)
        return None, None
    hosted_zone_id = res["HostedZones"][0]["Id"]
    lrrs_paginator = route53_client().get_paginator("list_resource_record_sets")
    for page in lrrs_paginator.paginate(HostedZoneId=hosted_zone_id):
        for rrs in page["ResourceRecordSets"]:
            if rrs["Name"] == f"{my_dns_name}." and rrs["Type"] == "A":
                return rrs["ResourceRecords"][0]["Value"], hosted_zone_id
    return None, hosted_zone_id


def set_route53_ip(new_ip, my_dns_name, hosted_zone_id, ttl):
    route53_change = {
        "Action": "UPSERT",
        "ResourceRecordSet": {
            "Name": f"{my_dns_name}.",
            "Type": "A",
            "ResourceRecords": [{"Value": new_ip}],
            "TTL": ttl,
        },
    }
    res = route53_client().change_resource_record_sets(
        HostedZoneId=hosted_zone_id, ChangeBatch={"Changes": [route53_change]}
    )
    logger.info("Completed update: %s", res)


def run():
    HOSTED_ZONE_DNS_NAME = os.environ["ROUTE53_HOSTED_ZONE_DNS_NAME"].strip().rstrip(".").lower()
    MY_DNS_NAME = os.environ["ROUTE53_MY_DNS_NAME"].strip().rstrip(".").lower()
    TTL = int(os.environ.get("ROUTE53_TTL", "300"))
    my_ip = get_my_ip()
    if my_ip is None:
        logger.error("Skipping update due to failure to determine current IP.")
        return
    route53_ip, hosted_zone_id = get_route53_ip(hosted_zone_dns_name=HOSTED_ZONE_DNS_NAME, my_dns_name=MY_DNS_NAME)
    if hosted_zone_id is None:
        logger.error("Skipping update due to missing hosted zone.")
        return
    if my_ip != route53_ip:
        logger.info(
            "Will update IP in %s (%s) for %s from %s to %s",
            HOSTED_ZONE_DNS_NAME,
            hosted_zone_id,
            MY_DNS_NAME,
            route53_ip,
            my_ip,
        )
        set_route53_ip(new_ip=my_ip, my_dns_name=MY_DNS_NAME, hosted_zone_id=hosted_zone_id, ttl=TTL)
    else:
        logger.info(
            "IP in %s (%s) for %s (%s) matches, nothing to do", HOSTED_ZONE_DNS_NAME, hosted_zone_id, MY_DNS_NAME, my_ip
        )


def install():
    if not shutil.which("systemctl"):
        parser.exit("systemctl does not appear to be active")
    if os.geteuid() != 0:
        parser.exit("install must be run as root (retry with sudo)")
    if not shutil.which("py-unifi-route53-ddns"):
        parser.exit("unable to resolve location of py-unifi-route53-ddns")
    logger.info("Installing /etc/systemd/system/py-unifi-route53-ddns.service...")
    with open("/etc/systemd/system/py-unifi-route53-ddns.service", "w") as service_fh:
        service_fh.write(systemd_service.format(entrypoint=shutil.which("py-unifi-route53-ddns")))
    logger.info("Installing /etc/systemd/system/py-unifi-route53-ddns.timer...")
    with open("/etc/systemd/system/py-unifi-route53-ddns.timer", "w") as timer_fh:
        timer_fh.write(systemd_timer)
    os.makedirs("/etc/systemd/system/py-unifi-route53-ddns.service.d", exist_ok=True)
    akid = input("AWS access key ID: ")
    access_key = getpass.getpass("AWS secret access key (hidden): ")
    zone_name = input("Route53 hosted zone DNS name (e.g. example.net): ")
    host_name = input("Route53 dynamic host name (e.g. unifi.example.net): ")
    env_conf_fd = os.open(
        "/etc/systemd/system/py-unifi-route53-ddns.service.d/env.conf", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600
    )
    with os.fdopen(env_conf_fd, "w") as env_fh:
        env_fh.write(
            systemd_override.format(akid=akid, access_key=access_key, zone_name=zone_name, host_name=host_name)
        )
    logger.info(
        'Done. Please run "systemctl start py-unifi-route53-ddns.timer" and "systemctl enable py-unifi-route53-ddns.timer".'
    )


def main():
    args = parser.parse_args()
    if args.action == "install":
        install()
    elif args.action == "run":
        run()
