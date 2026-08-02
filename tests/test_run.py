import datetime

import pytest
from botocore.stub import Stubber

import py_unifi_route53_ddns as ddns

ZONE = {"Id": "/hostedzone/Z123", "Name": "example.net.", "CallerReference": "ref"}
CHANGE_INFO = {
    "Id": "/change/C1",
    "Status": "PENDING",
    "SubmittedAt": datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc),
}


@pytest.fixture
def stub(monkeypatch):
    monkeypatch.setenv("ROUTE53_HOSTED_ZONE_DNS_NAME", "Example.NET.")
    monkeypatch.setenv("ROUTE53_MY_DNS_NAME", "unifi.Example.net.")
    ddns.route53_client.cache_clear()
    with Stubber(ddns.route53_client()) as stubber:
        yield stubber
        stubber.assert_no_pending_responses()
    ddns.route53_client.cache_clear()


def stub_zone_and_records(stub, ip):
    stub.add_response(
        "list_hosted_zones_by_name",
        {"HostedZones": [ZONE], "IsTruncated": False, "MaxItems": "100"},
        {"DNSName": "example.net"},
    )
    rrs = [{"Name": "unifi.example.net.", "Type": "A", "TTL": 300, "ResourceRecords": [{"Value": ip}]}]
    stub.add_response(
        "list_resource_record_sets",
        {"ResourceRecordSets": rrs, "IsTruncated": False, "MaxItems": "300"},
        {"HostedZoneId": "/hostedzone/Z123"},
    )


def test_ip_matches_no_update(stub, monkeypatch):
    monkeypatch.setattr(ddns, "get_my_ip", lambda: "1.2.3.4")
    stub_zone_and_records(stub, "1.2.3.4")
    ddns.run()


def test_ip_differs_updates_record(stub, monkeypatch):
    monkeypatch.setattr(ddns, "get_my_ip", lambda: "5.6.7.8")
    stub_zone_and_records(stub, "1.2.3.4")
    change = {
        "Action": "UPSERT",
        "ResourceRecordSet": {
            "Name": "unifi.example.net.",
            "Type": "A",
            "ResourceRecords": [{"Value": "5.6.7.8"}],
            "TTL": 300,
        },
    }
    stub.add_response(
        "change_resource_record_sets",
        {"ChangeInfo": CHANGE_INFO},
        {"HostedZoneId": "/hostedzone/Z123", "ChangeBatch": {"Changes": [change]}},
    )
    ddns.run()


def test_missing_record_creates_it(stub, monkeypatch):
    monkeypatch.setattr(ddns, "get_my_ip", lambda: "5.6.7.8")
    stub.add_response(
        "list_hosted_zones_by_name",
        {"HostedZones": [ZONE], "IsTruncated": False, "MaxItems": "100"},
        {"DNSName": "example.net"},
    )
    stub.add_response(
        "list_resource_record_sets",
        {"ResourceRecordSets": [], "IsTruncated": False, "MaxItems": "300"},
        {"HostedZoneId": "/hostedzone/Z123"},
    )
    change = {
        "Action": "UPSERT",
        "ResourceRecordSet": {
            "Name": "unifi.example.net.",
            "Type": "A",
            "ResourceRecords": [{"Value": "5.6.7.8"}],
            "TTL": 300,
        },
    }
    stub.add_response(
        "change_resource_record_sets",
        {"ChangeInfo": CHANGE_INFO},
        {"HostedZoneId": "/hostedzone/Z123", "ChangeBatch": {"Changes": [change]}},
    )
    ddns.run()


def test_missing_zone_skips_update(stub, monkeypatch):
    monkeypatch.setattr(ddns, "get_my_ip", lambda: "5.6.7.8")
    stub.add_response(
        "list_hosted_zones_by_name",
        {"HostedZones": [], "IsTruncated": False, "MaxItems": "100"},
        {"DNSName": "example.net"},
    )
    ddns.run()


def test_no_ip_skips_update(stub, monkeypatch):
    monkeypatch.setattr(ddns, "get_my_ip", lambda: None)
    ddns.run()
